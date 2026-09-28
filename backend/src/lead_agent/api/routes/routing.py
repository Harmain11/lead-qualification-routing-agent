from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_policies, require_permission
from lead_agent.api.schemas import (
    NotificationResponse,
    PipelineEventResponse,
    RoutingDecisionResponse,
    notification_response,
    routing_response,
)
from lead_agent.application.routing import retry_current_notification, route_and_notify
from lead_agent.domain.configuration import RoutingConfig
from lead_agent.domain.rbac import effective_permissions, normalize_role
from lead_agent.domain.routing import NotificationProvider
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import (
    RoutingDecisionORM,
    UserORM,
    WorkspaceMembershipORM,
    WorkspaceRoutingConfigORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.notifications.factory import get_notification_provider
from lead_agent.infrastructure.rate_limit import rate_limit_dependency
from lead_agent.infrastructure.repositories import (
    get_current_routing,
    get_lead,
    list_notifications,
    list_pipeline_events,
    list_routing_history,
)

router = APIRouter(prefix="/v1/leads", tags=["routing"])


class RerouteRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class RoutingWeightsRequest(BaseModel):
    member_weights: dict[UUID, float] = Field(default_factory=dict)


def _routing_config_response(session: Session, workspace_id: UUID) -> dict[str, object]:
    config = session.get(WorkspaceRoutingConfigORM, workspace_id)
    configured = config.member_weights if config is not None else {}
    rows = session.execute(
        select(WorkspaceMembershipORM, UserORM)
        .join(UserORM, UserORM.id == WorkspaceMembershipORM.user_id)
        .where(
            WorkspaceMembershipORM.workspace_id == workspace_id,
            WorkspaceMembershipORM.status == "active",
            UserORM.status == "active",
        )
    ).all()
    eligible = [
        (membership, user)
        for membership, user in rows
        if "leads.edit" in effective_permissions(membership.role, membership.permission_overrides)
    ]
    counts = dict(
        session.execute(
            select(RoutingDecisionORM.assigned_rep_id, func.count(RoutingDecisionORM.id))
            .where(
                RoutingDecisionORM.workspace_id == workspace_id,
                RoutingDecisionORM.is_current.is_(True),
                RoutingDecisionORM.assigned_rep_id.is_not(None),
            )
            .group_by(RoutingDecisionORM.assigned_rep_id)
        ).all()
    )
    return {
        "workspace_id": str(workspace_id),
        "default_strategy": "equal",
        "updated_at": config.updated_at if config is not None else None,
        "members": [
            {
                "user_id": str(user.id),
                "display_name": user.display_name,
                "email": user.email,
                "role": normalize_role(membership.role),
                "weight": float(configured.get(str(user.id), 1)),
                "open_leads": int(counts.get(str(user.id), 0)),
            }
            for membership, user in eligible
        ],
    }


@router.get("/config")
def routing_config(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("routing.view")),
) -> dict[str, object]:
    return _routing_config_response(session, workspace.workspace_id)


@router.put("/config")
def update_routing_config(
    request: RoutingWeightsRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("routing.manage")),
) -> dict[str, object]:
    current = _routing_config_response(session, workspace.workspace_id)
    current_members = current["members"]
    allowed = {str(item["user_id"]) for item in current_members}  # type: ignore[index]
    unknown = {str(user_id) for user_id in request.member_weights} - allowed
    if unknown:
        raise HTTPException(
            status_code=422,
            detail="Weights may only be set for active lead assignees",
        )
    weights = {
        str(user_id): round(value, 4) for user_id, value in request.member_weights.items()
    }
    if any(value <= 0 or value > 100 for value in weights.values()):
        raise HTTPException(
            status_code=422,
            detail="Routing weights must be greater than 0 and no more than 100",
        )
    config = session.get(WorkspaceRoutingConfigORM, workspace.workspace_id)
    now = datetime.now(UTC)
    if config is None:
        config = WorkspaceRoutingConfigORM(
            workspace_id=workspace.workspace_id,
            member_weights=weights,
            updated_by=workspace.user_id,
            updated_at=now,
        )
        session.add(config)
    else:
        config.member_weights = weights
        config.updated_by = workspace.user_id
        config.updated_at = now
    session.commit()
    return _routing_config_response(session, workspace.workspace_id)


def _lead_or_404(lead_id: UUID, session: Session, workspace_id: UUID) -> Any:
    lead = get_lead(session, lead_id, workspace_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def _required_routing_response(row: Any) -> RoutingDecisionResponse:
    response = routing_response(row)
    if response is None:
        raise HTTPException(status_code=404, detail="Routing decision not found")
    return response


@router.get("/{lead_id}/routing", response_model=RoutingDecisionResponse)
def retrieve_routing(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("routing.view")),
) -> RoutingDecisionResponse:
    _lead_or_404(lead_id, session, workspace.workspace_id)
    decision = get_current_routing(session, lead_id, workspace.workspace_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Routing decision not found")
    return _required_routing_response(decision)


@router.get("/{lead_id}/routing/action-plan")
def retrieve_action_plan(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("routing.view")),
) -> dict[str, object]:
    _lead_or_404(lead_id, session, workspace.workspace_id)
    decision = get_current_routing(session, lead_id, workspace.workspace_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Routing decision not found")
    return decision.action_plan


@router.get("/{lead_id}/routing/history", response_model=list[RoutingDecisionResponse])
def retrieve_routing_history(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("routing.view")),
) -> list[RoutingDecisionResponse]:
    _lead_or_404(lead_id, session, workspace.workspace_id)
    return [
        _required_routing_response(row)
        for row in list_routing_history(session, lead_id, workspace.workspace_id)
    ]


@router.get("/{lead_id}/notifications", response_model=list[NotificationResponse])
def retrieve_notifications(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("routing.view")),
) -> list[NotificationResponse]:
    _lead_or_404(lead_id, session, workspace.workspace_id)
    return [
        notification_response(row)
        for row in list_notifications(session, lead_id, workspace.workspace_id)
    ]


@router.get("/{lead_id}/pipeline-history", response_model=list[PipelineEventResponse])
def retrieve_pipeline_history(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("routing.view")),
) -> list[PipelineEventResponse]:
    _lead_or_404(lead_id, session, workspace.workspace_id)
    return [
        PipelineEventResponse(
            id=row.id,
            stage=row.stage,
            status=row.status,
            details=row.details,
            created_at=row.created_at,
        )
        for row in list_pipeline_events(session, lead_id, workspace.workspace_id)
    ]


@router.post(
    "/{lead_id}/notifications/retry",
    response_model=NotificationResponse,
    dependencies=[Depends(rate_limit_dependency("provider_actions"))],
)
def retry_notification(
    lead_id: UUID,
    session: Session = Depends(get_db),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    workspace: WorkspaceContext = Depends(require_permission("routing.manage")),
) -> NotificationResponse:
    lead = _lead_or_404(lead_id, session, workspace.workspace_id)
    try:
        notification = retry_current_notification(session, lead, notification_provider)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return notification_response(notification)


@router.post(
    "/{lead_id}/routing/reroute",
    response_model=RoutingDecisionResponse,
    dependencies=[Depends(rate_limit_dependency("provider_actions"))],
)
def reroute_lead(
    lead_id: UUID,
    request: RerouteRequest,
    session: Session = Depends(get_db),
    policies: tuple[object, RoutingConfig] = Depends(get_policies),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    workspace: WorkspaceContext = Depends(require_permission("routing.manage")),
) -> RoutingDecisionResponse:
    lead = _lead_or_404(lead_id, session, workspace.workspace_id)
    _, routing = policies
    decision = route_and_notify(
        session,
        lead,
        routing,
        notification_provider=notification_provider,
        force=True,
        reroute_reason=request.reason,
    )
    return _required_routing_response(decision)
