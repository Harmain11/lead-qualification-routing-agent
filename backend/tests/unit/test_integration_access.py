from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.application.integration_access import (
    allowed_connection_actions,
    can_view_provider,
    grant_connection_access,
    is_protected_owner_mailbox,
    revoke_connection_access,
)
from lead_agent.domain.rbac import role_permissions
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    ProviderConnectionORM,
    UserORM,
    WorkspaceMembershipORM,
    WorkspaceORM,
)
from lead_agent.infrastructure.db.teams import ConnectionAccessGrantORM


def _connection(workspace_id, owner_id, provider="gmail", connection_type="personal"):
    now = datetime.now(UTC)
    return ProviderConnectionORM(
        id=uuid4(),
        workspace_id=workspace_id,
        owner_user_id=owner_id,
        connection_type=connection_type,
        provider_type=provider,
        external_account_id="account@example.test",
        account_name="account@example.test",
        auth_type="oauth2",
        status="verified",
        scopes=[],
        credential_state="configured",
        last_validation_result={"status": "connected", "verified": True},
        connection_metadata={"owner_user_id": str(owner_id)},
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _member(session, workspace_id, role, email):
    now = datetime.now(UTC)
    user = UserORM(
        id=uuid4(), email=email, display_name=email, password_hash="hash", status="active",
        created_at=now, updated_at=now,
    )
    member = WorkspaceMembershipORM(
        id=uuid4(), workspace_id=workspace_id, user_id=user.id, role=role, status="active",
        created_at=now, updated_at=now,
    )
    session.add_all([user, member])
    session.flush()
    return user, member


def _context(workspace_id, user, member, extra=frozenset()):
    return WorkspaceContext(
        workspace_id=workspace_id,
        user_id=user.id,
        actor_id=str(user.id),
        membership_id=member.id,
        role=member.role,
        permissions=role_permissions(member.role) | extra,
    )


def test_owner_mailbox_is_non_overridable_and_admin_still_manages_workspace_connections():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        now = datetime.now(UTC)
        workspace = WorkspaceORM(
            id=uuid4(), name="Access", slug=f"access-{uuid4().hex}", status="active",
            is_demo=False, created_at=now, updated_at=now,
        )
        session.add(workspace)
        owner, owner_member = _member(session, workspace.id, "owner", "owner@example.test")
        admin, admin_member = _member(session, workspace.id, "admin", "admin@example.test")
        mailbox = _connection(workspace.id, owner.id)
        crm = _connection(workspace.id, owner.id, "salesforce", "workspace_managed")
        session.add_all([mailbox, crm])
        session.commit()

        owner_context = _context(workspace.id, owner, owner_member)
        admin_context = _context(workspace.id, admin, admin_member)
        assert is_protected_owner_mailbox(session, mailbox)
        assert {"view", "send", "sync"} <= allowed_connection_actions(
            session, owner_context, mailbox
        )
        assert allowed_connection_actions(session, admin_context, mailbox) == frozenset()
        assert {"view", "configure", "manage"} <= allowed_connection_actions(
            session, admin_context, crm
        )


def test_team_shared_connection_requires_both_delegation_and_provider_permission():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        now = datetime.now(UTC)
        workspace = WorkspaceORM(
            id=uuid4(), name="Shared", slug=f"shared-{uuid4().hex}", status="active",
            is_demo=False, created_at=now, updated_at=now,
        )
        session.add(workspace)
        owner, _owner_member = _member(session, workspace.id, "owner", "owner2@example.test")
        rep, rep_member = _member(session, workspace.id, "sales_rep", "rep@example.test")
        connection = _connection(workspace.id, owner.id, "salesforce", "team_shared")
        session.add(connection)
        session.add(ConnectionAccessGrantORM(
            connection_id=connection.id,
            membership_id=rep_member.id,
            workspace_id=workspace.id,
            actions=["view", "sync"],
            granted_by=owner.id,
            created_at=now,
        ))
        session.commit()

        without_provider_grant = _context(workspace.id, rep, rep_member)
        assert allowed_connection_actions(
            session, without_provider_grant, connection
        ) == frozenset()
        with_provider_grant = _context(
            workspace.id,
            rep,
            rep_member,
            frozenset({"integrations.salesforce.view", "integrations.salesforce.sync"}),
        )
        assert allowed_connection_actions(session, with_provider_grant, connection) == {
            "view", "sync"
        }


def test_team_shared_mailbox_can_be_delegated_without_exposing_owner_personal_mailbox():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        now = datetime.now(UTC)
        workspace = WorkspaceORM(
            id=uuid4(), name="Shared inbox", slug=f"shared-inbox-{uuid4().hex}",
            status="active", is_demo=False, created_at=now, updated_at=now,
        )
        session.add(workspace)
        owner, _owner_member = _member(session, workspace.id, "owner", "owner3@example.test")
        rep, rep_member = _member(session, workspace.id, "sales_rep", "rep3@example.test")
        connection = _connection(workspace.id, owner.id, "gmail", "team_shared")
        session.add(connection)
        session.add(ConnectionAccessGrantORM(
            connection_id=connection.id,
            membership_id=rep_member.id,
            workspace_id=workspace.id,
            actions=["view", "send"],
            granted_by=owner.id,
            created_at=now,
        ))
        session.commit()

        context = _context(
            workspace.id,
            rep,
            rep_member,
            frozenset({"integrations.gmail.view", "integrations.gmail.send"}),
        )
        assert allowed_connection_actions(session, context, connection) == {"view", "send"}


def test_provider_cards_are_role_and_provider_specific():
    workspace_id = uuid4()
    rep = WorkspaceContext(
        workspace_id=workspace_id,
        role="sales_rep",
        permissions=role_permissions("sales_rep"),
    )
    viewer = WorkspaceContext(
        workspace_id=workspace_id,
        role="viewer",
        permissions=role_permissions("viewer"),
    )
    assert can_view_provider(rep, "gmail")
    assert can_view_provider(rep, "cloudtalk")
    assert can_view_provider(rep, "openai")
    assert not can_view_provider(rep, "salesforce")
    assert not can_view_provider(viewer, "gmail")


def test_connection_grants_reject_personal_mailboxes_and_revoke_shared_access():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        now = datetime.now(UTC)
        workspace = WorkspaceORM(
            id=uuid4(), name="Delegation", slug=f"delegation-{uuid4().hex}",
            status="active", is_demo=False, created_at=now, updated_at=now,
        )
        session.add(workspace)
        owner, owner_member = _member(session, workspace.id, "owner", "owner4@example.test")
        rep, rep_member = _member(session, workspace.id, "sales_rep", "rep4@example.test")
        personal = _connection(workspace.id, owner.id, "gmail", "personal")
        shared = _connection(workspace.id, owner.id, "gmail", "team_shared")
        shared.external_account_id = "shared@example.test"
        shared.account_name = "shared@example.test"
        session.add_all([personal, shared])
        session.commit()

        owner_context = _context(workspace.id, owner, owner_member)
        with pytest.raises(HTTPException, match="personal connections cannot be delegated"):
            grant_connection_access(
                session, owner_context, personal, rep_member, ["view"],
            )

        grant_connection_access(
            session, owner_context, shared, rep_member, ["view", "send"],
        )
        session.commit()
        rep_context = _context(
            workspace.id,
            rep,
            rep_member,
            frozenset({"integrations.gmail.view", "integrations.gmail.send"}),
        )
        assert allowed_connection_actions(session, rep_context, shared) == {"view", "send"}

        revoke_connection_access(session, owner_context, shared, rep_member.id)
        session.commit()
        assert allowed_connection_actions(session, rep_context, shared) == frozenset()
