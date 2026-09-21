from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_policies, get_workspace_context
from lead_agent.api.schemas import (
    NotificationResponse,
    PipelineEventResponse,
    RoutingDecisionResponse,
    notification_response,
    routing_response,
)
from lead_agent.application.routing import retry_current_notification, route_and_notify
from lead_agent.domain.configuration import RoutingConfig
from lead_agent.domain.routing import NotificationProvider
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.notifications.factory import get_notification_provider
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
    workspace: WorkspaceContext = Depends(get_workspace_context),
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
    workspace: WorkspaceContext = Depends(get_workspace_context),
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
    workspace: WorkspaceContext = Depends(get_workspace_context),
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
    workspace: WorkspaceContext = Depends(get_workspace_context),
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
    workspace: WorkspaceContext = Depends(get_workspace_context),
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


@router.post("/{lead_id}/notifications/retry", response_model=NotificationResponse)
def retry_notification(
    lead_id: UUID,
    session: Session = Depends(get_db),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> NotificationResponse:
    lead = _lead_or_404(lead_id, session, workspace.workspace_id)
    try:
        notification = retry_current_notification(session, lead, notification_provider)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return notification_response(notification)


@router.post("/{lead_id}/routing/reroute", response_model=RoutingDecisionResponse)
def reroute_lead(
    lead_id: UUID,
    request: RerouteRequest,
    session: Session = Depends(get_db),
    policies: tuple[object, RoutingConfig] = Depends(get_policies),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    workspace: WorkspaceContext = Depends(get_workspace_context),
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
