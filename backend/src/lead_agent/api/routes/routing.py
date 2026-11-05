from __future__ import annotations

from datetime import UTC, datetime
from math import isclose, isfinite
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
from lead_agent.application.coaching_privacy import PRIVATE_INSIGHT_KINDS
from lead_agent.application.manual_review import workspace_manual_review_settings
from lead_agent.application.routing import retry_current_notification, route_and_notify
from lead_agent.domain.configuration import ManualReviewSettings, RoutingConfig
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
config_router = APIRouter(prefix="/v1/routing", tags=["routing"])


class RerouteRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class RoutingConfigRequest(BaseModel):
    member_weights: dict[UUID, float] | None = None
    member_percentages: dict[UUID, float] | None = None
    manual_review: ManualReviewSettings | None = None


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
        "manual_review": workspace_manual_review_settings(
            session, workspace_id
        ).model_dump(),
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


@config_router.get("/config")
def routing_config(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("routing.view")),
) -> dict[str, object]:
    return _routing_config_response(session, workspace.workspace_id)


@config_router.put("/config")
def update_routing_config(
    request: RoutingConfigRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("routing.manage")),
) -> dict[str, object]:
    current = _routing_config_response(session, workspace.workspace_id)
    config = session.get(WorkspaceRoutingConfigORM, workspace.workspace_id)
    current_members = current["members"]
    allowed = {str(item["user_id"]) for item in current_members}  # type: ignore[index]
    if request.member_weights is not None and request.member_percentages is not None:
        raise HTTPException(
            status_code=422,
            detail="Send either member_percentages or member_weights, not both",
        )
    if request.member_percentages is not None:
        percentages = request.member_percentages
        percentage_members = {str(user_id) for user_id in percentages}
        unknown = percentage_members - allowed
        missing = allowed - percentage_members
        if unknown:
            raise HTTPException(
                status_code=422,
                detail="Percentages may only be set for active lead assignees",
            )
        if missing:
            raise HTTPException(
                status_code=422,
                detail="Percentages must be provided for every active lead assignee",
            )
        if any(not isfinite(value) or value <= 0 or value > 100 for value in percentages.values()):
            raise HTTPException(
                status_code=422,
                detail="Each routing percentage must be greater than 0 and no more than 100",
            )
        if not isclose(sum(percentages.values()), 100, rel_tol=0, abs_tol=0.005):
            raise HTTPException(status_code=422, detail="Routing percentages must total 100")
        raw_weights = percentages
    elif request.member_weights is not None:
        raw_weights = request.member_weights
    else:
        raw_weights = config.member_weights if config is not None else {}
    unknown = {str(user_id) for user_id in raw_weights} - allowed
    if unknown:
        raise HTTPException(
            status_code=422,
            detail="Weights may only be set for active lead assignees",
        )
    if any(not isfinite(value) or value <= 0 or value > 100 for value in raw_weights.values()):
        raise HTTPException(
            status_code=422,
            detail="Routing weights must be greater than 0 and no more than 100",
        )
    weights = {str(user_id): round(value, 4) for user_id, value in raw_weights.items()}
    manual_review_settings = (
        request.manual_review.model_dump()
        if request.manual_review is not None
        else (
            config.manual_review_settings
            if config is not None
            else ManualReviewSettings().model_dump()
        )
    )
    now = datetime.now(UTC)
    if config is None:
        config = WorkspaceRoutingConfigORM(
            workspace_id=workspace.workspace_id,
            member_weights=weights,
            manual_review_settings=manual_review_settings,
            updated_by=workspace.user_id,
            updated_at=now,
        )
        session.add(config)
    else:
        if request.member_weights is not None or request.member_percentages is not None:
            config.member_weights = weights
        if request.manual_review is not None:
            config.manual_review_settings = manual_review_settings
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
    workspace: WorkspaceContext = Depends(require_permission("activity.view")),
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
        if row.stage not in PRIVATE_INSIGHT_KINDS
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
    workspace: WorkspaceContext = Depends(require_permission("leads.reassign")),
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
