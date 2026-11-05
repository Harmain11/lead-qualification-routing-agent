"""Invalidate stored assistant context when permissions or team access change."""

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.domain.rbac import PERMISSION_CATALOG, normalize_role
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.teams import WorkspaceTeamLeadORM, WorkspaceTeamMemberORM

ACCESS_KEY = "_access_fingerprint"


def conversation_access_key(session: Session, workspace: WorkspaceContext) -> str:
    role = normalize_role(workspace.role)
    team_links = []
    if role not in {"owner", "admin"}:
        from lead_agent.infrastructure.db.teams import WorkspaceTeamORM

        rows = session.execute(
            select(WorkspaceTeamMemberORM.team_id, WorkspaceTeamORM.is_default)
            .join(WorkspaceTeamORM, WorkspaceTeamORM.id == WorkspaceTeamMemberORM.team_id)
            .where(
                WorkspaceTeamMemberORM.workspace_id == workspace.workspace_id,
                WorkspaceTeamMemberORM.membership_id == workspace.membership_id,
            )
        ).all()
        explicit_team_ids = tuple(team_id for team_id, is_default in rows if not is_default)
        allowed_team_ids = explicit_team_ids or tuple(team_id for team_id, _ in rows)
        team_links = [
            (str(team_id), str(lead_id))
            for team_id, lead_id in session.execute(
                select(WorkspaceTeamMemberORM.team_id, WorkspaceTeamLeadORM.lead_id)
                .outerjoin(
                    WorkspaceTeamLeadORM,
                    (WorkspaceTeamLeadORM.team_id == WorkspaceTeamMemberORM.team_id)
                    & (WorkspaceTeamLeadORM.workspace_id == workspace.workspace_id),
                )
                .where(
                    WorkspaceTeamMemberORM.workspace_id == workspace.workspace_id,
                    WorkspaceTeamMemberORM.membership_id == workspace.membership_id,
                    WorkspaceTeamMemberORM.team_id.in_(allowed_team_ids),
                )
                .order_by(WorkspaceTeamMemberORM.team_id, WorkspaceTeamLeadORM.lead_id)
            )
        ]
    value = [str(workspace.workspace_id), role, sorted(workspace.permissions), team_links]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def conversation_is_accessible(
    messages: list[dict[str, Any]], workspace: WorkspaceContext, access_key: str
) -> bool:
    if all(message.get(ACCESS_KEY) == access_key for message in messages):
        return True
    # Old histories have no access snapshot. Only full workspace administrators
    # can safely read them; a deny override must invalidate that fallback too.
    return (
        normalize_role(workspace.role) in {"owner", "admin"}
        and PERMISSION_CATALOG <= workspace.permissions
        and all(ACCESS_KEY not in message for message in messages)
    )
