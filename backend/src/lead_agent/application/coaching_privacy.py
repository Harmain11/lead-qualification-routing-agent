"""Personal coaching ownership is independent of shared lead visibility."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from lead_agent.domain.rbac import normalize_role
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import LeadORM, RoutingDecisionORM, WorkspaceMembershipORM
from lead_agent.infrastructure.db.operations import AgentInsightORM

PRIVATE_INSIGHT_KINDS = ("coach", "digest")


def personal_data_user_ids(session: Session, workspace: WorkspaceContext) -> frozenset[str] | None:
    # Team membership resolution is shared with the request scope foundation.
    from lead_agent.application.team import personal_data_user_ids as team_scope

    return team_scope(session, workspace)


def insight_visibility(session: Session, workspace: WorkspaceContext) -> ColumnElement[bool]:
    """SQL predicate: apply before sorting/pagination, including legacy records."""
    users = personal_data_user_ids(session, workspace)
    if users is None:
        return AgentInsightORM.workspace_id == workspace.workspace_id
    shared = AgentInsightORM.kind.not_in(PRIVATE_INSIGHT_KINDS)
    if not users:
        return and_(AgentInsightORM.workspace_id == workspace.workspace_id, shared)
    owner = AgentInsightORM.content["owner_id"].as_string()
    coach = and_(
        AgentInsightORM.kind == "coach",
        or_(owner.in_(users), and_(owner.is_(None), AgentInsightORM.created_by.in_(users))),
    )
    digest = and_(
        AgentInsightORM.kind == "digest",
        AgentInsightORM.content["recipient_id"].as_string().in_(users),
    )
    if normalize_role(workspace.role) == "sales_rep":
        digest = and_(digest, AgentInsightORM.status == "approved")
    return and_(AgentInsightORM.workspace_id == workspace.workspace_id, or_(shared, coach, digest))


def coaching_owner_id(session: Session, lead: LeadORM) -> str | None:
    """Snapshot the assigned member; never infer ownership from a mutable display name."""
    owner = session.scalar(
        select(RoutingDecisionORM.assigned_rep_id)
        .where(
            RoutingDecisionORM.workspace_id == lead.workspace_id,
            RoutingDecisionORM.lead_id == lead.id,
            RoutingDecisionORM.is_current.is_(True),
        )
        .order_by(RoutingDecisionORM.created_at.desc())
        .limit(1)
    )
    try:
        owner_uuid = UUID(owner) if owner else None
    except ValueError:
        return None
    if owner_uuid is None:
        return None
    member = session.scalar(
        select(WorkspaceMembershipORM.id).where(
            WorkspaceMembershipORM.workspace_id == lead.workspace_id,
            WorkspaceMembershipORM.user_id == owner_uuid,
            WorkspaceMembershipORM.status == "active",
        )
    )
    return str(owner_uuid) if member is not None else None


def can_coach_lead(session: Session, workspace: WorkspaceContext, lead: LeadORM) -> bool:
    if lead.workspace_id != workspace.workspace_id or normalize_role(workspace.role) == "viewer":
        return False
    users = personal_data_user_ids(session, workspace)
    return users is None or coaching_owner_id(session, lead) in users
