from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission
from lead_agent.application.follow_up import (
    FollowUpNotEligible,
    generate_follow_up_draft,
    send_approved_follow_up,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import LeadFollowUpEmailORM, LeadORM
from lead_agent.infrastructure.db.session import get_db

router = APIRouter(prefix="/v1/leads", tags=["lead follow-up"])


class FollowUpEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subject: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=10, max_length=5000)

    @field_validator("subject", "body")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("Email content cannot be blank")
        return value


def _response(
    row: LeadFollowUpEmailORM | None,
    *,
    fallback_recipient: str | None = None,
) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "id": str(row.id),
        "lead_id": str(row.lead_id),
        "recipient": (
            row.recipient.strip()
            or (
                fallback_recipient.strip()
                if row.status == "draft" and fallback_recipient
                else ""
            )
        ),
        "subject": row.subject,
        "body": row.body,
        "rationale": row.rationale,
        "generation_method": row.generation_method,
        "generation_note": row.generation_note,
        "status": row.status,
        "attempts": row.attempts,
        "error_message": row.error_message,
        "external_message_id": row.external_message_id,
        "approved_by": row.approved_by,
        "approved_at": row.approved_at,
        "sent_at": row.sent_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _lead(session: Session, lead_id: UUID, workspace_id: UUID) -> LeadORM:
    row = session.scalar(
        select(LeadORM).where(LeadORM.id == lead_id, LeadORM.workspace_id == workspace_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return row


@router.get("/{lead_id}/follow-up")
def get_follow_up(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> dict[str, object] | None:
    lead = _lead(session, lead_id, workspace.workspace_id)
    row = session.scalar(
        select(LeadFollowUpEmailORM).where(
            LeadFollowUpEmailORM.lead_id == lead_id,
            LeadFollowUpEmailORM.workspace_id == workspace.workspace_id,
            or_(LeadFollowUpEmailORM.kind == "ai_follow_up", LeadFollowUpEmailORM.kind.like("ai_follow_up:%")),
        )
        .order_by(LeadFollowUpEmailORM.created_at.desc(), LeadFollowUpEmailORM.id.desc())
    )
    return _response(row, fallback_recipient=lead.email)


@router.get("/{lead_id}/follow-up/history")
def get_follow_up_history(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> list[dict[str, object]]:
    lead = _lead(session, lead_id, workspace.workspace_id)
    rows = session.scalars(
        select(LeadFollowUpEmailORM)
        .where(
            LeadFollowUpEmailORM.lead_id == lead_id,
            LeadFollowUpEmailORM.workspace_id == workspace.workspace_id,
            or_(LeadFollowUpEmailORM.kind == "ai_follow_up", LeadFollowUpEmailORM.kind.like("ai_follow_up:%")),
        )
        .order_by(LeadFollowUpEmailORM.created_at.desc(), LeadFollowUpEmailORM.id.desc())
        .limit(100)
    ).all()
    return [
        response
        for row in rows
        if (response := _response(row, fallback_recipient=lead.email)) is not None
    ]


@router.post("/{lead_id}/follow-up/draft")
def create_follow_up_draft(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> dict[str, object]:
    lead = _lead(session, lead_id, workspace.workspace_id)
    try:
        row = generate_follow_up_draft(session, lead, actor_id=workspace.actor_id)
    except FollowUpNotEligible as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _response(row) or {}


@router.patch("/{lead_id}/follow-up/{draft_id}")
def update_follow_up_draft(
    lead_id: UUID,
    draft_id: UUID,
    request: FollowUpEdit,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> dict[str, object]:
    lead = _lead(session, lead_id, workspace.workspace_id)
    row = session.scalar(
        select(LeadFollowUpEmailORM).where(
            LeadFollowUpEmailORM.id == draft_id,
            LeadFollowUpEmailORM.lead_id == lead_id,
            LeadFollowUpEmailORM.workspace_id == workspace.workspace_id,
            or_(LeadFollowUpEmailORM.kind == "ai_follow_up", LeadFollowUpEmailORM.kind.like("ai_follow_up:%")),
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Follow-up draft not found")
    if row.status != "draft":
        raise HTTPException(status_code=409, detail="Only an unsent draft can be edited")
    if not row.recipient.strip() and lead.email:
        row.recipient = lead.email.strip()
    row.subject = request.subject
    row.body = request.body
    row.updated_at = datetime.now(UTC)
    session.commit()
    session.refresh(row)
    return _response(row, fallback_recipient=lead.email) or {}


@router.post("/{lead_id}/follow-up/{draft_id}/send")
def approve_and_send_follow_up(
    lead_id: UUID,
    draft_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> dict[str, object]:
    _lead(session, lead_id, workspace.workspace_id)
    row = session.scalar(
        select(LeadFollowUpEmailORM).where(
            LeadFollowUpEmailORM.id == draft_id,
            LeadFollowUpEmailORM.lead_id == lead_id,
            LeadFollowUpEmailORM.workspace_id == workspace.workspace_id,
            or_(LeadFollowUpEmailORM.kind == "ai_follow_up", LeadFollowUpEmailORM.kind.like("ai_follow_up:%")),
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Follow-up draft not found")
    try:
        row = send_approved_follow_up(
            session, row, workspace_id=workspace.workspace_id, actor_id=workspace.actor_id
        )
    except FollowUpNotEligible as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _response(row) or {}
