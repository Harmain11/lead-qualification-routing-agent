"""Provider and connection-level authorization, including protected mailboxes."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from lead_agent.application.mailbox_access import connection_owner_id
from lead_agent.domain.rbac import normalize_role
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.communications import DIALERS
from lead_agent.infrastructure.db.models import (
    ProviderConnectionORM,
    WorkspaceAdminAuditEventORM,
    WorkspaceMembershipORM,
)
from lead_agent.infrastructure.db.teams import ConnectionAccessGrantORM
from lead_agent.infrastructure.reasoning.configuration import REASONING_PROVIDERS

MAILBOX_PROVIDERS = frozenset({"gmail", "outlook"})
PERSONAL_PROVIDERS = frozenset({"gmail", "outlook", "google_calendar", "calendly"})
CONNECTION_ACTIONS = frozenset(
    {
        "view",
        "view_status",
        "use",
        "sync",
        "send",
        "disconnect",
        "manage",
        "configure",
    }
)
DELEGATABLE_CONNECTION_ACTIONS = frozenset({"view", "view_status", "use", "sync", "send"})


def provider_permission(provider: str, action: str) -> str:
    aliases = {
        "disconnect": "disconnect_own",
        "manage": "manage_own",
    }
    return f"integrations.{provider}.{aliases.get(action, action)}"


def can_view_provider(workspace: WorkspaceContext, provider: str) -> bool:
    role = normalize_role(workspace.role)
    if role in {"owner", "admin"}:
        return True
    if provider_permission(provider, "view") in workspace.permissions:
        return True
    if provider in MAILBOX_PROVIDERS:
        return "mailbox.view_own" in workspace.permissions
    if provider in DIALERS:
        return "dialer.call" in workspace.permissions
    if provider in REASONING_PROVIDERS:
        return "agents.view" in workspace.permissions
    return False


def provider_card_actions(workspace: WorkspaceContext, provider: str) -> frozenset[str]:
    if not can_view_provider(workspace, provider):
        return frozenset()
    role = normalize_role(workspace.role)
    actions = {"view"}
    if role in {"owner", "admin"}:
        actions.update({"connect", "configure", "manage_credentials"})
    if provider_permission(provider, "connect_own") in workspace.permissions or (
        provider in MAILBOX_PROVIDERS and "mailbox.connect_own" in workspace.permissions
    ):
        actions.add("connect_own")
    return frozenset(actions)


def _owner_role(
    session: Session,
    connection: ProviderConnectionORM,
) -> str | None:
    owner = connection_owner_id(connection)
    if owner is None:
        return None
    try:
        from uuid import UUID

        owner_id = UUID(owner)
    except ValueError:
        return None
    role = session.scalar(
        select(WorkspaceMembershipORM.role).where(
            WorkspaceMembershipORM.workspace_id == connection.workspace_id,
            WorkspaceMembershipORM.user_id == owner_id,
            WorkspaceMembershipORM.status == "active",
        )
    )
    return normalize_role(role) if role else None


def is_protected_owner_mailbox(
    session: Session,
    connection: ProviderConnectionORM,
) -> bool:
    return (
        connection.provider_type in MAILBOX_PROVIDERS
        and _owner_role(session, connection) == "owner"
    )


def _delegated_actions(
    session: Session,
    workspace: WorkspaceContext,
    connection: ProviderConnectionORM,
) -> frozenset[str]:
    if workspace.membership_id is None:
        return frozenset()
    now = datetime.now(UTC)
    rows = session.scalars(
        select(ConnectionAccessGrantORM).where(
            ConnectionAccessGrantORM.workspace_id == workspace.workspace_id,
            ConnectionAccessGrantORM.connection_id == connection.id,
            ConnectionAccessGrantORM.membership_id == workspace.membership_id,
            or_(
                ConnectionAccessGrantORM.expires_at.is_(None),
                ConnectionAccessGrantORM.expires_at > now,
            ),
        )
    ).all()
    return frozenset(
        str(action) for row in rows for action in row.actions if str(action) in CONNECTION_ACTIONS
    )


def allowed_connection_actions(
    session: Session,
    workspace: WorkspaceContext,
    connection: ProviderConnectionORM,
) -> frozenset[str]:
    if connection.workspace_id != workspace.workspace_id:
        return frozenset()
    role = normalize_role(workspace.role)
    actor = str(workspace.user_id or workspace.actor_id)
    owner = connection_owner_id(connection)
    own = owner is not None and actor == owner
    connection_type = connection.connection_type or (
        "personal" if connection.provider_type in MAILBOX_PROVIDERS else "workspace_managed"
    )
    personal = connection_type == "personal"

    # This invariant is checked before ordinary grants or delegation. Even a
    # workspace Admin cannot inspect or operate the Owner's personal mailbox.
    if personal and is_protected_owner_mailbox(session, connection) and not own:
        return frozenset()

    allowed: set[str] = set()
    provider = connection.provider_type
    if own:
        if provider in MAILBOX_PROVIDERS:
            if "mailbox.view_own" in workspace.permissions:
                allowed.update({"view", "view_status"})
            if "mailbox.connect_own" in workspace.permissions:
                allowed.add("manage")
            if "mailbox.disconnect_own" in workspace.permissions:
                allowed.add("disconnect")
            if "mailbox.sync_own" in workspace.permissions:
                allowed.update({"use", "sync"})
            if "mailbox.send_own" in workspace.permissions:
                allowed.add("send")
        else:
            allowed.update({"view", "view_status", "manage", "disconnect", "use"})
    elif not personal and role in {"owner", "admin"}:
        allowed.update(CONNECTION_ACTIONS)
    elif not personal:
        allowed.update(_delegated_actions(session, workspace, connection))

    # Provider-specific denies still win because the permission is absent from
    # the effective workspace permission set.
    for action in tuple(allowed):
        permission = provider_permission(provider, action)
        legacy_ok = (
            (provider in DIALERS and action == "use" and "dialer.call" in workspace.permissions)
            or (
                provider in REASONING_PROVIDERS
                and action in {"view", "view_status", "use"}
                and "agents.view" in workspace.permissions
            )
            or (provider in MAILBOX_PROVIDERS and own)
        )
        if permission not in workspace.permissions and not legacy_ok and role != "owner":
            allowed.discard(action)
    return frozenset(allowed)


def assert_connection_action(
    session: Session,
    workspace: WorkspaceContext,
    connection: ProviderConnectionORM,
    action: str,
) -> None:
    if action not in CONNECTION_ACTIONS:
        raise ValueError(f"unsupported connection action: {action}")
    if action not in allowed_connection_actions(session, workspace, connection):
        raise HTTPException(
            403,
            f"connection permission required: {connection.provider_type}.{action}",
        )


def visible_connections(
    session: Session,
    workspace: WorkspaceContext,
    connections: Iterable[ProviderConnectionORM],
) -> list[ProviderConnectionORM]:
    return [
        connection
        for connection in connections
        if "view" in allowed_connection_actions(session, workspace, connection)
        or "view_status" in allowed_connection_actions(session, workspace, connection)
    ]


def assert_manage_connection_grants(
    workspace: WorkspaceContext,
    connection: ProviderConnectionORM,
) -> None:
    if connection.workspace_id != workspace.workspace_id:
        raise HTTPException(404, "connection not found")
    if connection.connection_type == "personal":
        raise HTTPException(403, "personal connections cannot be delegated")
    permission = provider_permission(connection.provider_type, "manage_shared")
    if (
        normalize_role(workspace.role) not in {"owner", "admin"}
        and permission not in workspace.permissions
    ):
        raise HTTPException(403, f"permission required: {permission}")


def grant_connection_access(
    session: Session,
    workspace: WorkspaceContext,
    connection: ProviderConnectionORM,
    membership: WorkspaceMembershipORM,
    actions: Iterable[str],
) -> ConnectionAccessGrantORM:
    """Delegate bounded shared-connection actions without transferring ownership."""
    assert_manage_connection_grants(workspace, connection)
    if membership.workspace_id != workspace.workspace_id or membership.status != "active":
        raise HTTPException(404, "active workspace member not found")
    normalized_actions = sorted({str(action) for action in actions})
    invalid = set(normalized_actions) - DELEGATABLE_CONNECTION_ACTIONS
    if invalid:
        raise HTTPException(422, f"unsupported delegated actions: {', '.join(sorted(invalid))}")
    grant = session.get(ConnectionAccessGrantORM, (connection.id, membership.id))
    before = {"actions": list(grant.actions)} if grant else {}
    if grant is None:
        grant = ConnectionAccessGrantORM(
            connection_id=connection.id,
            membership_id=membership.id,
            workspace_id=workspace.workspace_id,
            created_at=datetime.now(UTC),
        )
        session.add(grant)
    grant.actions = normalized_actions
    grant.granted_by = workspace.user_id
    grant.expires_at = None
    session.add(
        WorkspaceAdminAuditEventORM(
            id=uuid4(),
            workspace_id=workspace.workspace_id,
            actor_id=workspace.user_id,
            target_membership_id=membership.id,
            operation="connection_access_granted",
            before={"connection_id": str(connection.id), **before},
            after={"connection_id": str(connection.id), "actions": normalized_actions},
            created_at=datetime.now(UTC),
        )
    )
    return grant


def revoke_connection_access(
    session: Session,
    workspace: WorkspaceContext,
    connection: ProviderConnectionORM,
    membership_id: UUID,
) -> None:
    assert_manage_connection_grants(workspace, connection)
    grant = session.get(ConnectionAccessGrantORM, (connection.id, membership_id))
    if grant is None or grant.workspace_id != workspace.workspace_id:
        raise HTTPException(404, "connection access grant not found")
    before = list(grant.actions)
    session.delete(grant)
    session.add(
        WorkspaceAdminAuditEventORM(
            id=uuid4(),
            workspace_id=workspace.workspace_id,
            actor_id=workspace.user_id,
            target_membership_id=membership_id,
            operation="connection_access_revoked",
            before={"connection_id": str(connection.id), "actions": before},
            after={"connection_id": str(connection.id)},
            created_at=datetime.now(UTC),
        )
    )
