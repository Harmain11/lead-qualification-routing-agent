"""Mailbox ownership rules for member-scoped email access."""

from __future__ import annotations

from typing import Any

from lead_agent.domain.rbac import normalize_role
from lead_agent.domain.workspaces import WorkspaceContext


def connection_owner_id(connection: Any) -> str | None:
    column_owner = getattr(connection, "owner_user_id", None)
    if column_owner:
        return str(column_owner)
    metadata = connection.connection_metadata if isinstance(connection.connection_metadata, dict) else {}
    owner = metadata.get("owner_user_id") or metadata.get("oauth_actor_id")
    return str(owner) if owner else None


def can_use_mailbox(workspace: WorkspaceContext, connection: Any) -> bool:
    """Personal mailbox content is available only to its owner.

    Workspace administration and mailbox-content access are deliberately separate.
    In particular, Admin never inherits access to the Owner's or another member's
    private Gmail/Outlook connection.
    """
    if normalize_role(workspace.role) == "viewer":
        return False
    owner = connection_owner_id(connection)
    return owner is not None and str(workspace.user_id or workspace.actor_id) == owner
