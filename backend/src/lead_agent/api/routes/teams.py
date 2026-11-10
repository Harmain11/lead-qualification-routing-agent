"""Team administration. Access grants are explicit; routing is independent."""

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_workspace_context, require_permission
from lead_agent.domain.rbac import normalize_role
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import (
    LeadORM,
    WorkspaceAdminAuditEventORM,
    WorkspaceMembershipORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.db.teams import (
    WorkspaceTeamLeadORM,
    WorkspaceTeamMemberORM,
    WorkspaceTeamORM,
)

router = APIRouter(prefix="/v1/teams", tags=["workspace-teams"])


def _admin(
    workspace: WorkspaceContext = Depends(require_permission("members.manage")),
) -> WorkspaceContext:
    if normalize_role(workspace.role) not in {"owner", "admin"}:
        raise HTTPException(403, "team administration requires an owner or admin role")
    return workspace


class TeamInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class TeamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workspace_id: UUID
    name: str
    description: str
    is_default: bool
    member_ids: list[UUID]
    manager_ids: list[UUID]
    lead_ids: list[UUID]
    created_at: datetime
    updated_at: datetime


def _response(session: Session, team: WorkspaceTeamORM) -> TeamResponse:
    return TeamResponse(
        id=team.id, workspace_id=team.workspace_id, name=team.name,
        description=team.description, is_default=team.is_default,
        member_ids=list(session.scalars(select(WorkspaceTeamMemberORM.membership_id).where(
            WorkspaceTeamMemberORM.workspace_id == team.workspace_id,
            WorkspaceTeamMemberORM.team_id == team.id,
        ).order_by(WorkspaceTeamMemberORM.membership_id))),
        manager_ids=list(session.scalars(select(WorkspaceTeamMemberORM.membership_id).where(
            WorkspaceTeamMemberORM.workspace_id == team.workspace_id,
            WorkspaceTeamMemberORM.team_id == team.id,
            WorkspaceTeamMemberORM.is_manager.is_(True),
        ).order_by(WorkspaceTeamMemberORM.membership_id))),
        lead_ids=list(session.scalars(select(WorkspaceTeamLeadORM.lead_id).where(
            WorkspaceTeamLeadORM.workspace_id == team.workspace_id,
            WorkspaceTeamLeadORM.team_id == team.id,
            # A compatibility default membership can coexist with explicit
            # teams. Never expose its workspace-wide IDs to a restricted actor.
            WorkspaceTeamLeadORM.lead_id.in_(select(LeadORM.id)),
        ).order_by(WorkspaceTeamLeadORM.lead_id))),
        created_at=team.created_at, updated_at=team.updated_at,
    )


def _team(session: Session, workspace: WorkspaceContext, team_id: UUID) -> WorkspaceTeamORM:
    row = session.scalar(select(WorkspaceTeamORM).where(
        WorkspaceTeamORM.id == team_id, WorkspaceTeamORM.workspace_id == workspace.workspace_id,
    ).with_for_update())
    if row is None:
        raise HTTPException(404, "team not found")
    return row


def _save(
    session: Session, workspace: WorkspaceContext, team: WorkspaceTeamORM,
    operation: str, before: Mapping[str, object] | None = None,
    after: Mapping[str, object] | None = None,
) -> None:
    team.updated_at = datetime.now(UTC)
    session.add(WorkspaceAdminAuditEventORM(
        workspace_id=workspace.workspace_id, actor_id=workspace.user_id,
        operation=operation, before=dict(before or {}),
        after={"team_id": str(team.id), **(after or {})}, created_at=team.updated_at,
    ))
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(409, "team name or link already exists") from error


@router.get("", response_model=list[TeamResponse])
def list_teams(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> list[TeamResponse]:
    query = select(WorkspaceTeamORM).where(
        WorkspaceTeamORM.workspace_id == workspace.workspace_id,
    )
    if normalize_role(workspace.role) not in {"owner", "admin"}:
        query = query.where(WorkspaceTeamORM.id.in_(
            select(WorkspaceTeamMemberORM.team_id).where(
                WorkspaceTeamMemberORM.workspace_id == workspace.workspace_id,
                WorkspaceTeamMemberORM.membership_id == workspace.membership_id,
            ),
        ))
    return [_response(session, row) for row in session.scalars(query.order_by(
        WorkspaceTeamORM.is_default.desc(), WorkspaceTeamORM.name_key,
    ))]


@router.post("", response_model=TeamResponse, status_code=201)
def create_team(
    request: TeamInput, session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(_admin),
) -> TeamResponse:
    from uuid import uuid4

    row = WorkspaceTeamORM(
        id=uuid4(), workspace_id=workspace.workspace_id, name=request.name,
        name_key=request.name.casefold(), description=request.description,
        is_default=False,
    )
    session.add(row)
    _save(session, workspace, row, "team_created", after={"name": row.name})
    return _response(session, row)


@router.patch("/{team_id}", response_model=TeamResponse)
def update_team(
    team_id: UUID, request: TeamInput, session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(_admin),
) -> TeamResponse:
    row = _team(session, workspace, team_id)
    before = {"name": row.name, "description": row.description}
    row.name = request.name
    row.name_key = request.name.casefold()
    row.description = request.description
    _save(session, workspace, row, "team_updated", before, {"name": row.name})
    return _response(session, row)


@router.delete("/{team_id}", status_code=204)
def delete_team(
    team_id: UUID, session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(_admin),
) -> Response:
    row = _team(session, workspace, team_id)
    if row.is_default:
        raise HTTPException(409, "the default team cannot be deleted")
    # Explicit deletes also work on local SQLite connections without FK pragmas.
    for model in (WorkspaceTeamMemberORM, WorkspaceTeamLeadORM):
        session.execute(delete(model).where(
            model.workspace_id == workspace.workspace_id, model.team_id == team_id,
        ))
    session.delete(row)
    _save(session, workspace, row, "team_deleted", {"name": row.name})
    return Response(status_code=204)


@router.put("/{team_id}/members/{membership_id}", response_model=TeamResponse)
@router.delete("/{team_id}/members/{membership_id}", response_model=TeamResponse)
def change_team_member(
    team_id: UUID, membership_id: UUID,
    request: Request,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(_admin),
) -> TeamResponse:
    team = _team(session, workspace, team_id)
    member = session.scalar(select(WorkspaceMembershipORM).where(
        WorkspaceMembershipORM.id == membership_id,
        WorkspaceMembershipORM.workspace_id == workspace.workspace_id,
    ))
    if member is None:
        raise HTTPException(404, "member not found")
    adding = request.method == "PUT"
    if adding and member.status != "active":
        raise HTTPException(409, "only active workspace members can join a team")
    link = session.get(WorkspaceTeamMemberORM, (team_id, membership_id))
    if adding and link is None:
        session.add(WorkspaceTeamMemberORM(
            workspace_id=workspace.workspace_id, team_id=team_id, membership_id=membership_id,
            is_manager=False,
        ))
    elif not adding and link is not None:
        session.delete(link)
    _save(session, workspace, team, "team_member_added" if adding else "team_member_removed",
          after={"membership_id": str(membership_id)})
    return _response(session, team)


@router.put("/{team_id}/managers/{membership_id}", response_model=TeamResponse)
@router.delete("/{team_id}/managers/{membership_id}", response_model=TeamResponse)
def change_team_manager(
    team_id: UUID,
    membership_id: UUID,
    request: Request,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(_admin),
) -> TeamResponse:
    team = _team(session, workspace, team_id)
    member = session.scalar(select(WorkspaceMembershipORM).where(
        WorkspaceMembershipORM.id == membership_id,
        WorkspaceMembershipORM.workspace_id == workspace.workspace_id,
        WorkspaceMembershipORM.status == "active",
    ))
    if member is None:
        raise HTTPException(404, "active member not found")
    if normalize_role(member.role) != "sales_manager":
        raise HTTPException(422, "only a Sales Manager can manage a team")
    adding = request.method == "PUT"
    link = session.get(WorkspaceTeamMemberORM, (team_id, membership_id))
    if link is None and adding:
        link = WorkspaceTeamMemberORM(
            workspace_id=workspace.workspace_id,
            team_id=team_id,
            membership_id=membership_id,
            is_manager=True,
        )
        session.add(link)
    elif link is not None:
        link.is_manager = adding
    _save(
        session,
        workspace,
        team,
        "team_manager_added" if adding else "team_manager_removed",
        after={"membership_id": str(membership_id)},
    )
    return _response(session, team)


@router.put("/{team_id}/leads/{lead_id}", response_model=TeamResponse)
@router.delete("/{team_id}/leads/{lead_id}", response_model=TeamResponse)
def change_team_lead(
    team_id: UUID, lead_id: UUID,
    request: Request,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(_admin),
) -> TeamResponse:
    team = _team(session, workspace, team_id)
    lead = session.scalar(select(LeadORM).where(
        LeadORM.id == lead_id, LeadORM.workspace_id == workspace.workspace_id,
    ).execution_options(include_deleted_leads=True))
    if lead is None:
        raise HTTPException(404, "lead not found")
    adding = request.method == "PUT"
    link = session.get(WorkspaceTeamLeadORM, (team_id, lead_id))
    if adding and link is None:
        session.add(WorkspaceTeamLeadORM(
            workspace_id=workspace.workspace_id, team_id=team_id, lead_id=lead_id,
        ))
    elif not adding and link is not None:
        session.delete(link)
    _save(session, workspace, team, "team_lead_added" if adding else "team_lead_removed",
          after={"lead_id": str(lead_id)})
    return _response(session, team)
