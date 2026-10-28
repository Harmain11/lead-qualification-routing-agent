from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_any_permission
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import AssignmentNotificationORM, LeadORM
from lead_agent.infrastructure.db.session import get_db

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


class AssignmentNotificationResponse(BaseModel):
    id: UUID
    lead_id: UUID
    lead_name: str
    company_name: str
    kind: str
    message: str | None
    created_at: datetime
    read_at: datetime | None


def _current_user_id(workspace: WorkspaceContext) -> UUID:
    if workspace.user_id is None:
        raise HTTPException(status_code=401, detail="Sign in to view notifications.")
    return workspace.user_id


def _response(
    notification: AssignmentNotificationORM, lead: LeadORM
) -> AssignmentNotificationResponse:
    return AssignmentNotificationResponse(
        id=notification.id,
        lead_id=lead.id,
        lead_name=lead.full_name,
        company_name=lead.company_name,
        kind=notification.kind,
        message=notification.message,
        created_at=notification.created_at,
        read_at=notification.read_at,
    )


@router.get("/assignments", response_model=list[AssignmentNotificationResponse])
def list_assignment_notifications(
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(
        require_any_permission("leads.view", "leads.edit")
    ),
) -> list[AssignmentNotificationResponse]:
    user_id = _current_user_id(workspace)
    rows = session.execute(
        select(AssignmentNotificationORM, LeadORM)
        .join(
            LeadORM,
            (LeadORM.id == AssignmentNotificationORM.lead_id)
            & (LeadORM.workspace_id == AssignmentNotificationORM.workspace_id),
        )
        .where(
            AssignmentNotificationORM.workspace_id == workspace.workspace_id,
            AssignmentNotificationORM.recipient_user_id == user_id,
            AssignmentNotificationORM.read_at.is_(None),
        )
        .order_by(AssignmentNotificationORM.created_at.desc())
        .limit(limit)
    ).all()
    return [_response(notification, lead) for notification, lead in rows]


@router.patch(
    "/assignments/{notification_id}/read",
    response_model=AssignmentNotificationResponse,
)
def mark_assignment_notification_read(
    notification_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(
        require_any_permission("leads.view", "leads.edit")
    ),
) -> AssignmentNotificationResponse:
    user_id = _current_user_id(workspace)
    row = session.execute(
        select(AssignmentNotificationORM, LeadORM)
        .join(
            LeadORM,
            (LeadORM.id == AssignmentNotificationORM.lead_id)
            & (LeadORM.workspace_id == AssignmentNotificationORM.workspace_id),
        )
        .where(
            AssignmentNotificationORM.id == notification_id,
            AssignmentNotificationORM.workspace_id == workspace.workspace_id,
            AssignmentNotificationORM.recipient_user_id == user_id,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found.")
    notification, lead = row
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        session.commit()
        session.refresh(notification)
    return _response(notification, lead)
