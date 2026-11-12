from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from lead_agent.application.auth import membership_permissions, require_membership
from lead_agent.application.team import (
    accept_invitation,
    change_member_access_policy,
    change_member_permissions,
    change_member_role,
    get_membership_for_user,
    invite_member,
    list_admin_audit_events,
    resend_invitation,
    revoke_invitation,
    set_member_status,
)
from lead_agent.domain.auth import AuthorizationError
from lead_agent.domain.rbac import (
    ACCESS_CATEGORY_REGISTRY,
    PERMISSION_CATALOG,
    can_sub_assign_leads,
    effective_permissions,
    normalize_overrides,
    normalize_role,
    permission_registry,
    role_permissions,
)
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    AuthSessionORM,
    LeadORM,
    UserORM,
    WorkspaceInvitationORM,
    WorkspaceMembershipORM,
    WorkspaceORM,
)
from lead_agent.infrastructure.db.teams import WorkspaceTeamORM


def test_lead_sub_assignment_requires_manager_role() -> None:
    assert can_sub_assign_leads("owner")
    assert can_sub_assign_leads("admin")
    assert can_sub_assign_leads("sales_manager")
    assert not can_sub_assign_leads("sales_rep")
    assert not can_sub_assign_leads("viewer")


def _user(email: str) -> UserORM:
    now = datetime.now(UTC)
    return UserORM(
        id=uuid4(),
        email=email,
        display_name=email.split("@", 1)[0],
        password_hash="test-hash",
        status="active",
        created_at=now,
        updated_at=now,
    )


def test_permission_registry_has_unique_categories_and_permissions() -> None:
    registry = permission_registry()
    category_ids = [category["id"] for category in registry["categories"]]
    assert len(category_ids) == len(set(category_ids))
    assert category_ids == [category["id"] for category in ACCESS_CATEGORY_REGISTRY]
    assert "calling" in category_ids
    assert "email_calendar" in category_ids
    assert sum(category["label"] == "Calling" for category in registry["categories"]) == 1

    exposed: set[str] = set()
    for category in registry["categories"]:
        exposed.update(item["id"] for item in category["feature_permissions"])
        for provider in category["providers"]:
            exposed.update(
                f"integrations.{provider['id']}.{action['id']}"
                for action in category["provider_actions"]
            )
    assert exposed == PERMISSION_CATALOG


def test_provider_exceptions_are_exact_and_broad_denies_keep_explicit_grants() -> None:
    baseline = effective_permissions("sales_manager")
    assert "integrations.cloudtalk.use" in baseline
    assert "integrations.twilio.use" in baseline

    exact_deny = effective_permissions(
        "sales_manager",
        {"grants": [], "denies": ["integrations.cloudtalk.use"]},
    )
    assert "integrations.cloudtalk.use" not in exact_deny
    assert "integrations.twilio.use" in exact_deny

    broad_deny_with_exception = effective_permissions(
        "sales_manager",
        {
            "grants": ["integrations.cloudtalk.use"],
            "denies": ["dialer.call"],
        },
    )
    assert "integrations.cloudtalk.use" in broad_deny_with_exception
    assert "integrations.twilio.use" not in broad_deny_with_exception


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


@pytest.fixture
def foreign_key_session() -> Session:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False, expire_on_commit=False) as value:
        yield value
    engine.dispose()


def _workspace_with_owner(session: Session) -> tuple[WorkspaceORM, UserORM, WorkspaceMembershipORM]:
    now = datetime.now(UTC)
    owner = _user("owner@example.test")
    workspace = WorkspaceORM(
        id=uuid4(),
        name="RBAC workspace",
        slug=f"rbac-{uuid4().hex}",
        status="active",
        is_demo=False,
        created_at=now,
        updated_at=now,
    )
    membership = WorkspaceMembershipORM(
        id=uuid4(),
        workspace_id=workspace.id,
        user_id=owner.id,
        role="owner",
        status="active",
        created_at=now,
        updated_at=now,
    )
    session.add_all([owner, workspace])
    session.flush()
    session.add(membership)
    session.commit()
    return workspace, owner, membership


def test_role_catalog_and_deterministic_overrides() -> None:
    assert normalize_role("Sales Manager") == "sales_manager"
    assert role_permissions("owner") == PERMISSION_CATALOG
    assert "leads.export" not in role_permissions("sales manager")
    assert "leads.export" in effective_permissions(
        "sales manager", {"grants": ["leads.export"], "denies": []}
    )
    assert "dialer.call" in role_permissions("sales manager")
    assert "dialer.call" in role_permissions("sales rep")
    assert "routing.manage" not in role_permissions("sales manager")
    assert {"comments.view", "comments.add", "activity.view"} <= role_permissions("sales rep")
    assert "integrations.manage" not in role_permissions("sales rep")
    assert "workspace.manage" not in role_permissions("viewer")
    assert "leads.export" not in role_permissions("viewer")
    assert "leads.export" in effective_permissions(
        "sales rep", {"grants": ["leads.export"], "denies": []}
    )
    assert "dialer.call" in effective_permissions(
        "sales rep", {"grants": ["dialer.call"], "denies": []}
    )
    assert "leads.view" not in effective_permissions(
        "owner", {"grants": [], "denies": ["leads.view"]}
    )
    assert normalize_overrides({"grants": ["leads.export"], "denies": ["leads.export"]}) == {
        "grants": ["leads.export"],
        "denies": ["leads.export"],
    }
    with pytest.raises(ValueError, match="unknown permissions"):
        normalize_overrides({"grants": ["workspace.delete"]})


def test_invitation_acceptance_is_scoped_single_use_and_audited(session: Session) -> None:
    workspace, owner, _ = _workspace_with_owner(session)
    invitee = _user("rep@example.test")
    session.add(invitee)
    session.commit()

    invitation, token = invite_member(
        session,
        workspace.id,
        owner.id,
        invitee.email,
        "Sales Rep",
        {"grants": ["leads.export"], "denies": []},
        {"scopes": {"leads.view": "own", "leads.edit": "own"}},
    )
    assert len(token) >= 48
    assert invitation.token_hash != token
    pending_membership = session.scalar(
        select(WorkspaceMembershipORM).where(WorkspaceMembershipORM.user_id == invitee.id)
    )
    assert pending_membership is not None
    assert pending_membership.status == "invited"
    assert pending_membership.access_policy == {
        "scopes": {"leads.edit": "own", "leads.view": "own"},
    }
    assert invitation.access_policy == pending_membership.access_policy

    with pytest.raises(ValueError, match="another workspace"):
        accept_invitation(session, token, invitee.id, invitee.email, uuid4())
    accepted = accept_invitation(session, token, invitee.id, invitee.email, workspace.id)
    assert accepted.status == "active"
    assert accepted.role == "sales_rep"
    assert accepted.access_policy == pending_membership.access_policy
    assert membership_permissions(accepted) == effective_permissions(
        "sales_rep", {"grants": ["leads.export"], "denies": []}
    )
    with pytest.raises(ValueError, match="already been used"):
        accept_invitation(session, token, invitee.id, invitee.email, workspace.id)
    operations = [event.operation for event in list_admin_audit_events(session, workspace.id)]
    assert "member_invited" in operations
    assert "member_joined" in operations


def test_new_invite_commits_without_a_post_commit_refresh(session: Session, monkeypatch) -> None:
    workspace, owner, _ = _workspace_with_owner(session)

    def unexpected_refresh(*_args, **_kwargs) -> None:
        raise AssertionError("invitation response must not require a second database read")

    monkeypatch.setattr(session, "refresh", unexpected_refresh)
    invitation, token = invite_member(
        session,
        workspace.id,
        owner.id,
        "new-staff@example.test",
        "sales_rep",
    )

    stored = session.scalar(
        select(WorkspaceInvitationORM).where(
            WorkspaceInvitationORM.workspace_id == workspace.id,
            WorkspaceInvitationORM.email == "new-staff@example.test",
        )
    )
    assert stored is not None
    assert stored.id == invitation.id
    assert stored.status == "pending"
    assert stored.token_hash != token


@pytest.mark.parametrize("existing_account", [True, False])
def test_invite_and_accept_with_foreign_keys_enforced(
    foreign_key_session: Session, existing_account: bool
) -> None:
    session = foreign_key_session
    workspace, owner, _ = _workspace_with_owner(session)
    invitee = _user("staff@example.test")
    if existing_account:
        session.add(invitee)
        session.commit()

    invitation, original_token = invite_member(
        session, workspace.id, owner.id, invitee.email, "sales_rep"
    )
    membership = get_membership_for_user(session, workspace.id, invitee.id)
    audit = next(
        row for row in list_admin_audit_events(session, workspace.id)
        if row.operation == "member_invited"
    )
    if existing_account:
        assert membership is not None
        assert membership.status == "invited"
        assert audit.target_membership_id == membership.id
    else:
        assert membership is None
        assert audit.target_membership_id is None

    repeated, repeated_token = invite_member(
        session, workspace.id, owner.id, invitee.email, "sales_rep"
    )
    assert repeated.id == invitation.id
    assert repeated_token != original_token
    resent, fresh_token = resend_invitation(session, workspace.id, invitation.id, owner.id)
    assert resent.id == invitation.id
    assert fresh_token != repeated_token
    assert len(list(session.scalars(select(WorkspaceInvitationORM)))) == 1

    if not existing_account:
        session.add(invitee)
        session.commit()
    accepted = accept_invitation(
        session, fresh_token, invitee.id, invitee.email, workspace.id
    )
    assert accepted.status == "active"
    assert accepted.user_id == invitee.id
    assert invitation.status == "accepted"
    assert any(
        row.operation == "member_joined" and row.target_membership_id == accepted.id
        for row in list_admin_audit_events(session, workspace.id)
    )


def test_invite_parent_flush_does_not_commit_partial_membership(
    foreign_key_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = foreign_key_session
    workspace, owner, _ = _workspace_with_owner(session)
    invitee = _user("staff@example.test")
    session.add(invitee)
    session.commit()

    def fail_audit(*_args, **_kwargs) -> None:
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr("lead_agent.application.team._audit", fail_audit)
    with pytest.raises(RuntimeError, match="simulated audit failure"):
        invite_member(session, workspace.id, owner.id, invitee.email, "sales_rep")
    session.rollback()

    assert get_membership_for_user(session, workspace.id, invitee.id) is None
    assert list(session.scalars(select(WorkspaceInvitationORM))) == []


def test_invitation_expiry_and_revoke_are_enforced(session: Session) -> None:
    workspace, owner, _ = _workspace_with_owner(session)
    expired_user = _user("expired@example.test")
    session.add(expired_user)
    session.commit()
    invitation, token = invite_member(session, workspace.id, owner.id, expired_user.email, "viewer")
    invitation.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()
    with pytest.raises(ValueError, match="expired"):
        accept_invitation(session, token, expired_user.id, expired_user.email, workspace.id)
    assert invitation.status == "expired"
    expired_membership = get_membership_for_user(session, workspace.id, expired_user.id)
    assert expired_membership is not None and expired_membership.status == "revoked"

    pending_user = _user("pending@example.test")
    session.add(pending_user)
    session.commit()
    fresh, fresh_token = invite_member(
        session, workspace.id, owner.id, pending_user.email, "viewer"
    )
    revoked = revoke_invitation(session, workspace.id, fresh.id, owner.id)
    assert revoked.status == "revoked"
    revoked_membership = get_membership_for_user(session, workspace.id, pending_user.id)
    assert revoked_membership is not None and revoked_membership.status == "revoked"
    with pytest.raises(ValueError, match="used or revoked"):
        accept_invitation(
            session, fresh_token, pending_user.id, pending_user.email, workspace.id
        )
    with pytest.raises(ValueError, match="revoked"):
        resend_invitation(session, workspace.id, fresh.id, owner.id)


def test_owner_protection_and_member_lifecycle(session: Session) -> None:
    workspace, owner, owner_membership = _workspace_with_owner(session)
    staff = _user("staff@example.test")
    staff_membership = WorkspaceMembershipORM(
        id=uuid4(),
        workspace_id=workspace.id,
        user_id=staff.id,
        role="sales_rep",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add_all([staff, staff_membership])
    session.commit()
    active_session = AuthSessionORM(
        id=uuid4(),
        user_id=staff.id,
        current_workspace_id=workspace.id,
        token_hash="staff-session-token",
        csrf_token_hash="staff-session-csrf",
        expires_at=datetime.now(UTC) + timedelta(days=1),
        last_seen_at=datetime.now(UTC),
        revoked_at=None,
        created_at=datetime.now(UTC),
    )
    session.add(active_session)
    session.commit()

    with pytest.raises(ValueError, match="protected"):
        set_member_status(session, workspace.id, owner_membership.id, staff.id, "revoked")
    with pytest.raises(ValueError, match="protected"):
        change_member_role(session, workspace.id, owner_membership.id, staff.id, "admin")
    with pytest.raises(ValueError, match="cannot change their own"):
        change_member_permissions(session, workspace.id, staff_membership.id, staff.id, {})

    changed = change_member_role(session, workspace.id, staff_membership.id, owner.id, "viewer")
    assert changed.role == "viewer"
    suspended = set_member_status(session, workspace.id, staff_membership.id, owner.id, "suspended")
    assert suspended.status == "suspended"
    assert session.get(AuthSessionORM, active_session.id).revoked_at is not None
    reactivated = set_member_status(session, workspace.id, staff_membership.id, owner.id, "active")
    assert reactivated.status == "active"
    removed = set_member_status(session, workspace.id, staff_membership.id, owner.id, "revoked")
    assert removed.status == "revoked"
    with pytest.raises(AuthorizationError):
        require_membership(session, staff.id, workspace.id)


def test_offboarding_reassigns_owned_leads_to_an_active_teammate(session: Session) -> None:
    workspace, owner, _owner_membership = _workspace_with_owner(session)
    staff = _user("offboarded-rep@example.test")
    now = datetime.now(UTC)
    staff_membership = WorkspaceMembershipORM(
        id=uuid4(),
        workspace_id=workspace.id,
        user_id=staff.id,
        role="sales_rep",
        status="active",
        created_at=now,
        updated_at=now,
    )
    session.add_all([staff, staff_membership])
    session.commit()
    default_team = session.scalar(
        select(WorkspaceTeamORM).where(
            WorkspaceTeamORM.workspace_id == workspace.id,
            WorkspaceTeamORM.is_default.is_(True),
        )
    )
    assert default_team is not None
    lead = LeadORM(
        id=uuid4(),
        workspace_id=workspace.id,
        current_owner_id=staff.id,
        primary_team_id=default_team.id,
        lead_number=1,
        source="manual",
        full_name="Offboarding Test",
        email="offboarding@example.test",
        company_name="Corral Test",
        product_or_service="CRM",
        message="Please contact me.",
        received_at=now,
        raw_payload={},
        normalization_version="v1",
        created_at=now,
        updated_at=now,
    )
    session.add(lead)
    session.commit()

    set_member_status(session, workspace.id, staff_membership.id, owner.id, "suspended")

    refreshed = session.get(LeadORM, lead.id)
    assert refreshed is not None
    assert refreshed.current_owner_id == owner.id


def test_admin_can_manage_another_admin_and_promote_a_member(session: Session) -> None:
    workspace, _, _ = _workspace_with_owner(session)
    actor = _user("admin-actor@example.test")
    other_admin = _user("other-admin@example.test")
    member = _user("member@example.test")
    now = datetime.now(UTC)
    actor_membership = WorkspaceMembershipORM(
        id=uuid4(),
        workspace_id=workspace.id,
        user_id=actor.id,
        role="admin",
        status="active",
        created_at=now,
        updated_at=now,
    )
    other_admin_membership = WorkspaceMembershipORM(
        id=uuid4(),
        workspace_id=workspace.id,
        user_id=other_admin.id,
        role="admin",
        status="active",
        created_at=now,
        updated_at=now,
    )
    member_membership = WorkspaceMembershipORM(
        id=uuid4(),
        workspace_id=workspace.id,
        user_id=member.id,
        role="sales_rep",
        status="active",
        created_at=now,
        updated_at=now,
    )
    session.add_all(
        [
            actor,
            other_admin,
            member,
            actor_membership,
            other_admin_membership,
            member_membership,
        ]
    )
    session.commit()

    assert "members.manage" in membership_permissions(actor_membership)

    updated_admin = change_member_permissions(
        session,
        workspace.id,
        other_admin_membership.id,
        actor.id,
        {"grants": ["leads.export"], "denies": ["leads.view"]},
    )
    assert updated_admin.permission_overrides == {
        "grants": ["leads.export"],
        "denies": ["leads.view"],
    }
    assert "leads.export" in membership_permissions(updated_admin)
    assert "leads.view" not in membership_permissions(updated_admin)

    promoted_member = change_member_role(
        session,
        workspace.id,
        member_membership.id,
        actor.id,
        "admin",
    )
    assert promoted_member.role == "admin"
    assert "members.manage" in membership_permissions(promoted_member)


def test_permissions_are_workspace_specific(session: Session) -> None:
    workspace_one, owner, _ = _workspace_with_owner(session)
    workspace_two = WorkspaceORM(
        id=uuid4(),
        name="Second workspace",
        slug=f"second-{uuid4().hex}",
        status="active",
        is_demo=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    user = _user("multi@example.test")
    now = datetime.now(UTC)
    session.add_all(
        [
            workspace_two,
            user,
            WorkspaceMembershipORM(
                id=uuid4(),
                workspace_id=workspace_one.id,
                user_id=user.id,
                role="admin",
                status="active",
                created_at=now,
                updated_at=now,
            ),
            WorkspaceMembershipORM(
                id=uuid4(),
                workspace_id=workspace_two.id,
                user_id=user.id,
                role="viewer",
                status="active",
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    session.commit()
    one = session.scalar(
        select(WorkspaceMembershipORM).where(
            WorkspaceMembershipORM.workspace_id == workspace_one.id,
            WorkspaceMembershipORM.user_id == user.id,
        )
    )
    two = session.scalar(
        select(WorkspaceMembershipORM).where(
            WorkspaceMembershipORM.workspace_id == workspace_two.id,
            WorkspaceMembershipORM.user_id == user.id,
        )
    )
    assert one is not None and two is not None
    assert "integrations.manage" in membership_permissions(one)
    assert "integrations.manage" not in membership_permissions(two)


def test_member_access_policy_cannot_exceed_actor_scope(session: Session) -> None:
    workspace, _, _ = _workspace_with_owner(session)
    actor = _user("limited-manager@example.test")
    target_user = _user("target-rep@example.test")
    now = datetime.now(UTC)
    actor_membership = WorkspaceMembershipORM(
        id=uuid4(),
        workspace_id=workspace.id,
        user_id=actor.id,
        role="sales_rep",
        status="active",
        permission_overrides={"grants": ["members.manage"], "denies": []},
        access_policy={"scopes": {"leads.view": "own"}},
        created_at=now,
        updated_at=now,
    )
    target = WorkspaceMembershipORM(
        id=uuid4(),
        workspace_id=workspace.id,
        user_id=target_user.id,
        role="sales_rep",
        status="active",
        permission_overrides={},
        access_policy={},
        created_at=now,
        updated_at=now,
    )
    session.add_all([actor, target_user, actor_membership, target])
    session.commit()

    with pytest.raises(AuthorizationError, match="broader leads.view scope"):
        change_member_access_policy(
            session,
            workspace.id,
            target.id,
            actor.id,
            {"scopes": {"leads.view": "all"}},
        )

    updated = change_member_access_policy(
        session,
        workspace.id,
        target.id,
        actor.id,
        {"scopes": {"leads.view": "own"}},
    )
    assert updated.access_policy == {"scopes": {"leads.view": "own"}}
