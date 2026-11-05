"""Mailbox ownership rules for member-scoped email access."""

from __future__ import annotations

from typing import Any

from lead_agent.domain.rbac import normalize_role
from lead_agent.domain.workspaces import WorkspaceContext


def connection_owner_id(connection: Any) -> str | None:
    metadata = connection.connection_metadata if isinstance(connection.connection_metadata, dict) else {}
    owner = metadata.get("owner_user_id") or metadata.get("oauth_actor_id")
    return str(owner) if owner else None


def can_use_mailbox(workspace: WorkspaceContext, connection: Any) -> bool:
    """Admins retain workspace mailbox administration; members get their own mailbox only."""
    if normalize_role(workspace.role) in {"owner", "admin"}:
        return True
    owner = connection_owner_id(connection)
    return owner is not None and str(workspace.user_id or workspace.actor_id) == owner
