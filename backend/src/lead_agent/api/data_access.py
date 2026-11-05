"""Fail closed for provider data that cannot be constrained to workspace teams."""

from fastapi import HTTPException

from lead_agent.domain.rbac import normalize_role
from lead_agent.domain.workspaces import WorkspaceContext


def require_workspace_wide_access(workspace: WorkspaceContext) -> None:
    if normalize_role(workspace.role) not in {"owner", "admin"}:
        raise HTTPException(403, "This provider operation cannot be limited to your teams.")
