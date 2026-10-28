from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from lead_agent.api.dependencies import require_permission
from lead_agent.api.routes.email import (
    EmailAttachment,
    _sanitize_email_html,
    _validate_address_list,
)
from lead_agent.application.follow_up import (
    FollowUpNotEligible,
    generate_follow_up_draft,
    send_approved_follow_up,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import LeadFollowUpEmailORM, LeadORM, PipelineEventORM
from lead_agent.infrastructure.db.session import get_db

router = APIRouter(prefix="/v1/leads", tags=["lead follow-up"])


class FollowUpEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subject: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=10, max_length=8000)
    cc: str = Field(default="", max_length=1024)
    bcc: str = Field(default="", max_length=1024)
    html_body: str | None = Field(default=None, max_length=40_000)
    attachments: list[EmailAttachment] = Field(default_factory=list, max_length=3)

    @field_validator("subject", "body")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("Email content cannot be blank")
        return value

    @field_validator("cc", "bcc")
    @classmethod
    def validate_recipients(cls, value: str) -> str:
        return _validate_address_list(value, required=False)

    @field_validator("attachments")
    @classmethod
    def limit_attachments(cls, value: list[EmailAttachment]) -> list[EmailAttachment]:
        total = 0
        for attachment in value:
            try:
                total += len(base64.b64decode(attachment.content_base64, validate=True))
            except (binascii.Error, ValueError):
                raise ValueError("An attachment could not be decoded") from None
        if total > 10 * 1024 * 1024:
            raise ValueError("Attachments must total 10 MB or less")
        return value


def _response(
    row: LeadFollowUpEmailORM | None,
    *,
    fallback_recipient: str | None = None,
    session: Session | None = None,
) -> dict[str, object] | None:
    if row is None:
        return None
    offer_details: dict = {}
    if session is not None and row.kind.startswith("meeting_options:"):
        try:
            offer_id = UUID(row.kind.split(":", 1)[1])
        except ValueError:
            offer_id = None
        offer = session.get(PipelineEventORM, offer_id) if offer_id else None
        if offer is not None and offer.workspace_id == row.workspace_id and offer.lead_id == row.lead_id:
            offer_details = offer.details if isinstance(offer.details, dict) else {}
    return {
        "id": str(row.id),
        "lead_id": str(row.lead_id),
        "kind": row.kind,
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
        "cc": row.cc or "",
        "bcc": row.bcc or "",
        "html_body": row.html_body,
        "attachments": row.attachments or [],
        "thread_id": offer_details.get("thread_id"),
        "in_reply_to": offer_details.get("source_rfc_message_id"),
        "connection_id": offer_details.get("gmail_connection_id"),
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


def _follow_up_kind_filter() -> ColumnElement[bool]:
    return or_(
        LeadFollowUpEmailORM.kind == "ai_follow_up",
        LeadFollowUpEmailORM.kind.like("ai_follow_up:%"),
        LeadFollowUpEmailORM.kind.like("meeting_options:%"),
    )


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
            _follow_up_kind_filter(),
        )
        .order_by(LeadFollowUpEmailORM.created_at.desc(), LeadFollowUpEmailORM.id.desc())
    )
    return _response(row, fallback_recipient=lead.email, session=session)


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
            _follow_up_kind_filter(),
        )
        .order_by(LeadFollowUpEmailORM.created_at.desc(), LeadFollowUpEmailORM.id.desc())
        .limit(100)
    ).all()
    return [
        response
        for row in rows
        if (response := _response(row, fallback_recipient=lead.email, session=session)) is not None
    ]


@router.delete("/{lead_id}/follow-up/{draft_id}", status_code=204)
def delete_appointment_draft(
    lead_id: UUID,
    draft_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> None:
    _lead(session, lead_id, workspace.workspace_id)
    draft = session.scalar(select(LeadFollowUpEmailORM).where(
        LeadFollowUpEmailORM.id == draft_id,
        LeadFollowUpEmailORM.lead_id == lead_id,
        LeadFollowUpEmailORM.workspace_id == workspace.workspace_id,
    ))
    if draft is None or not draft.kind.startswith("meeting_options:"):
        raise HTTPException(status_code=404, detail="Appointment draft not found")
    if draft.status != "draft":
        raise HTTPException(
            status_code=409, detail="Only an unsent appointment draft can be deleted"
        )
    try:
        offer_id = UUID(draft.kind.split(":", 1)[1])
    except ValueError:
        raise HTTPException(status_code=409, detail="Appointment offer is invalid") from None
    offer = session.scalar(select(PipelineEventORM).where(
        PipelineEventORM.id == offer_id,
        PipelineEventORM.workspace_id == workspace.workspace_id,
        PipelineEventORM.lead_id == lead_id,
    ))
    if offer is None or offer.status != "awaiting_send":
        raise HTTPException(status_code=409, detail="Appointment offer is no longer a draft")
    offer.status = "dismissed"
    offer.details = {**(offer.details if isinstance(offer.details, dict) else {}),
                     "dismissed_at": datetime.now(UTC).isoformat()}
    session.delete(draft)
    session.commit()


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
    return _response(row, session=session) or {}


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
            _follow_up_kind_filter(),
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
    if "cc" in request.model_fields_set:
        row.cc = request.cc
    if "bcc" in request.model_fields_set:
        row.bcc = request.bcc
    if "html_body" in request.model_fields_set:
        row.html_body = _sanitize_email_html(request.html_body)
    if "attachments" in request.model_fields_set:
        row.attachments = [attachment.model_dump() for attachment in request.attachments]
    row.updated_at = datetime.now(UTC)
    session.commit()
    session.refresh(row)
    return _response(row, fallback_recipient=lead.email, session=session) or {}


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
            _follow_up_kind_filter(),
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
    return _response(row, session=session) or {}
