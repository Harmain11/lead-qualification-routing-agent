from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from lead_agent.application.auth import membership_permissions, require_membership
from lead_agent.application.team import (
    accept_invitation,
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
    PERMISSION_CATALOG,
    effective_permissions,
    normalize_overrides,
    normalize_role,
    role_permissions,
)
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    UserORM,
    WorkspaceMembershipORM,
    WorkspaceORM,
)


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


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


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
    session.add_all([owner, workspace, membership])
    session.commit()
    return workspace, owner, membership


def test_role_catalog_and_deterministic_overrides() -> None:
    assert normalize_role("Sales Manager") == "sales_manager"
    assert role_permissions("owner") == PERMISSION_CATALOG
    assert "leads.export" in role_permissions("sales manager")
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
    )
    assert len(token) >= 48
    assert invitation.token_hash != token
    pending_membership = session.scalar(
        select(WorkspaceMembershipORM).where(WorkspaceMembershipORM.user_id == invitee.id)
    )
    assert pending_membership is not None
    assert pending_membership.status == "invited"

    with pytest.raises(ValueError, match="another workspace"):
        accept_invitation(session, token, invitee.id, invitee.email, uuid4())
    accepted = accept_invitation(session, token, invitee.id, invitee.email, workspace.id)
    assert accepted.status == "active"
    assert accepted.role == "sales_rep"
    assert membership_permissions(accepted) == effective_permissions(
        "sales_rep", {"grants": ["leads.export"], "denies": []}
    )
    with pytest.raises(ValueError, match="already been used"):
        accept_invitation(session, token, invitee.id, invitee.email, workspace.id)
    operations = [event.operation for event in list_admin_audit_events(session, workspace.id)]
    assert "member_invited" in operations
    assert "member_joined" in operations


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
    reactivated = set_member_status(session, workspace.id, staff_membership.id, owner.id, "active")
    assert reactivated.status == "active"
    removed = set_member_status(session, workspace.id, staff_membership.id, owner.id, "revoked")
    assert removed.status == "revoked"
    with pytest.raises(AuthorizationError):
        require_membership(session, staff.id, workspace.id)


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
