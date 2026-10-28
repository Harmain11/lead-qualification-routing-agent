from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from lead_agent.application.email_templates import (
    apply_workspace_email_signature,
    get_email_template,
)
from lead_agent.application.gmail_threading import same_gmail_subject
from lead_agent.application.governance import (
    OutreachBlocked,
    assert_outreach_allowed,
    operations_policy,
)
from lead_agent.application.meeting_email import manual_selected_slot_body_is_intact
from lead_agent.domain.follow_up import FollowUpEmailContent
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.infrastructure.db.models import (
    LeadFollowUpEmailORM,
    LeadORM,
    PipelineEventORM,
    ProviderConnectionORM,
    RoutingDecisionORM,
    WorkspaceORM,
)
from lead_agent.infrastructure.db.operations import LeadNoteORM
from lead_agent.infrastructure.gmail import GMAIL_SEND_SCOPE, GmailOutboundProvider
from lead_agent.infrastructure.provider_lifecycle import get_workspace_connection_resolver
from lead_agent.infrastructure.reasoning.factory import resolve_workspace_reasoning_provider


class FollowUpNotEligible(ValueError):
    pass


def _current_email(
    session: Session, lead_id: UUID, workspace_id: UUID
) -> LeadFollowUpEmailORM | None:
    return session.scalar(
        select(LeadFollowUpEmailORM)
        .where(
            LeadFollowUpEmailORM.lead_id == lead_id,
            LeadFollowUpEmailORM.workspace_id == workspace_id,
            or_(
                LeadFollowUpEmailORM.kind == "ai_follow_up",
                LeadFollowUpEmailORM.kind.like("ai_follow_up:%"),
            ),
        )
        .order_by(LeadFollowUpEmailORM.created_at.desc(), LeadFollowUpEmailORM.id.desc())
    )


def generate_follow_up_draft(
    session: Session, lead: LeadORM, *, actor_id: str
) -> LeadFollowUpEmailORM:
    try:
        assert_outreach_allowed(session, lead.workspace_id, lead.email)
    except OutreachBlocked as error:
        raise FollowUpNotEligible(str(error)) from error
    route = session.scalar(
        select(RoutingDecisionORM).where(
            RoutingDecisionORM.lead_id == lead.id,
            RoutingDecisionORM.workspace_id == lead.workspace_id,
            RoutingDecisionORM.is_current.is_(True),
        )
    )
    qualification = lead.qualification
    if qualification is None or route is None:
        raise FollowUpNotEligible(
            "A completed qualification and routing decision are required first."
        )
    if (
        qualification.hard_disqualified
        or qualification.classification.lower()
        in {
            "disqualify",
            "disqualified",
            "hard_disqualified",
        }
        or route.manual_review
        or (lead.analysis and lead.analysis.requires_manual_review)
    ):
        raise FollowUpNotEligible(
            "Resolve the disqualification or manual review before drafting outreach."
        )

    existing = _current_email(session, lead.id, lead.workspace_id)
    if existing is not None and existing.status in {"sending", "delivery_uncertain"}:
        return existing
    next_kind = "ai_follow_up"
    if existing is not None and existing.status == "sent":
        # Preserve every sent message; the existing unique purpose constraint
        # supports a distinct, bounded purpose identifier for each follow-up.
        next_kind = f"ai_follow_up:{uuid4().hex[:16]}"
        existing = None
    plan = route.action_plan if isinstance(route.action_plan, dict) else {}
    activity = list(
        session.scalars(
            select(PipelineEventORM)
            .where(
                PipelineEventORM.workspace_id == lead.workspace_id,
                PipelineEventORM.lead_id == lead.id,
                PipelineEventORM.stage.in_(("manual_progress", "gmail_message")),
            )
            .order_by(PipelineEventORM.created_at.desc(), PipelineEventORM.id.desc())
            .limit(20)
        )
    )
    notes = list(
        session.scalars(
            select(LeadNoteORM)
            .where(
                LeadNoteORM.workspace_id == lead.workspace_id,
                LeadNoteORM.lead_id == lead.id,
            )
            .order_by(LeadNoteORM.updated_at.desc(), LeadNoteORM.id.desc())
            .limit(15)
        )
    )
    facts = {
        "lead_name": lead.full_name,
        "company": lead.company_name,
        "job_title": lead.job_title,
        "requested_product_or_service": lead.product_or_service,
        "original_message": lead.message[:1600],
        "qualification": qualification.classification,
        "qualification_score": qualification.total_score,
        "next_step_recommendation": str(plan.get("next_step_recommendation", ""))[:500],
        "conversion_goal": str(plan.get("conversion_goal", ""))[:300],
        "current_outcome": (
            {
                "pipeline_stage": lead.outcome.pipeline_stage,
                "conversion_type": lead.outcome.conversion_type,
            }
            if lead.outcome
            else None
        ),
        "recent_activity": [
            {
                "type": event.stage,
                "status": event.status,
                "updated_at": event.created_at.isoformat(),
                "stage": str(event.details.get("stage", event.status))
                if event.stage == "manual_progress"
                else None,
                "direction": str(event.details.get("direction", ""))
                if event.stage == "gmail_message"
                else None,
                "note": str(event.details.get("note", ""))[:1600]
                if event.stage == "manual_progress"
                else None,
                "subject": str(event.details.get("subject", ""))[:300]
                if event.stage == "gmail_message"
                else None,
                "body": str(event.details.get("body", ""))[:1600]
                if event.stage == "gmail_message"
                else None,
            }
            for event in activity
        ],
        "team_notes": [
            {
                "updated_at": note.updated_at.isoformat(),
                "body": note.body.strip()[:1200],
            }
            for note in notes
        ],
    }
    business_profile = operations_policy(
        session, lead.workspace_id
    ).business_profile
    workspace = session.get(WorkspaceORM, lead.workspace_id)
    facts["workspace_business_profile"] = business_profile.model_dump(mode="json")
    workspace_template = get_email_template(session, lead.workspace_id, "follow_up")
    facts["workspace_email_template"] = {
        "subject": workspace_template["subject"],
        "body": workspace_template["body"],
    }
    provider = resolve_workspace_reasoning_provider(session, lead.workspace_id)
    draft_method = getattr(provider, "draft_follow_up", None)
    if not callable(draft_method):
        raise FollowUpNotEligible(
            "Connect and verify OpenAI to generate an AI follow-up. No template draft was created."
        )
    try:
        content = FollowUpEmailContent.model_validate(draft_method(facts))
        content = FollowUpEmailContent.model_validate(
            {
                **content.model_dump(),
                "body": apply_workspace_email_signature(
                    content.body,
                    business_profile,
                    fallback_business_name=workspace.name if workspace else "",
                ),
            }
        )
    except Exception as error:
        raise FollowUpNotEligible(
            "AI drafting did not complete. Check the OpenAI connection and retry. "
            "No template draft was created."
        ) from error
    method, note = "openai", None

    now = datetime.now(UTC)
    if existing is None:
        existing = LeadFollowUpEmailORM(
            id=uuid4(),
            workspace_id=lead.workspace_id,
            lead_id=lead.id,
            provider_connection_id=None,
            kind=next_kind,
            recipient=lead.email,
            subject=content.subject,
            body=content.body,
            rationale=content.rationale,
            generation_method=method,
            generation_note=note,
            status="draft",
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        session.add(existing)
    else:
        existing.recipient = lead.email
        existing.subject = content.subject
        existing.body = content.body
        existing.rationale = content.rationale
        existing.generation_method = method
        existing.generation_note = note
        existing.status = "draft"
        existing.error_message = None
        existing.approved_by = None
        existing.approved_at = None
        existing.provider_connection_id = None
        existing.external_message_id = None
        existing.sent_at = None
        existing.updated_at = now
    session.commit()
    session.refresh(existing)
    return existing


def _template(lead: LeadORM, plan: dict[str, object]) -> FollowUpEmailContent:
    first_name = (lead.full_name.strip().split() or ["there"])[0]
    next_step = str(plan.get("next_step_recommendation", "")).strip()
    question = (
        "Would you be open to a short discovery call?"
        if next_step.lower().startswith("schedule")
        else "Would a brief conversation about your needs be useful?"
    )
    return FollowUpEmailContent(
        subject=f"Following up on {lead.product_or_service}",
        body=(
            f"Hi {first_name},\n\nThanks for reaching out to us about "
            f"{lead.product_or_service}. I read your note and would be glad to learn a little "
            f"more about what {lead.company_name} is looking for.\n\n{question}\n\nBest,\nThe team"
        ),
        rationale=(
            "Uses the lead’s stated request and company; asks permission for a next step "
            "without unsupported claims."
        ),
    )


def _meeting_offer_for_draft(
    session: Session, row: LeadFollowUpEmailORM, workspace_id: UUID
) -> PipelineEventORM | None:
    if not row.kind.startswith("meeting_options:"):
        return None
    try:
        offer_id = UUID(row.kind.split(":", 1)[1])
    except (ValueError, IndexError) as error:
        raise FollowUpNotEligible("The meeting reply draft is no longer available.") from error
    offer = session.scalar(
        select(PipelineEventORM).where(
            PipelineEventORM.id == offer_id,
            PipelineEventORM.workspace_id == workspace_id,
            PipelineEventORM.lead_id == row.lead_id,
            PipelineEventORM.stage == "meeting_email_offer",
        )
    )
    if offer is None:
        raise FollowUpNotEligible("The meeting reply draft is no longer available.")
    details = offer.details if isinstance(offer.details, dict) else {}
    if str(details.get("email_draft_id") or "") != str(row.id):
        raise FollowUpNotEligible("This draft is not linked to the meeting request.")
    if offer.status not in {"awaiting_send", "needs_attention"}:
        raise FollowUpNotEligible("This meeting reply has already been sent or resolved.")
    return offer


def send_approved_follow_up(
    session: Session, row: LeadFollowUpEmailORM, *, workspace_id: UUID, actor_id: str
) -> LeadFollowUpEmailORM:
    if row.status == "sent":
        return row
    if row.status in {"sending", "delivery_uncertain"}:
        raise FollowUpNotEligible(
            "Delivery may have occurred. Check the Gmail Sent folder before taking further action."
        )
    if row.status != "draft" or not row.body or not row.subject.strip():
        raise FollowUpNotEligible("Save a complete draft before sending it from connected Gmail.")
    if row.workspace_id != workspace_id:
        raise FollowUpNotEligible("Follow-up draft not found.")
    if not row.recipient.strip():
        lead = session.scalar(
            select(LeadORM).where(
                LeadORM.id == row.lead_id,
                LeadORM.workspace_id == workspace_id,
            )
        )
        recipient = lead.email.strip() if lead and lead.email else ""
        if not recipient:
            raise FollowUpNotEligible(
                "This lead does not have an email address. Add one before sending."
            )
        row.recipient = recipient
        session.flush()
    try:
        assert_outreach_allowed(session, workspace_id, row.recipient)
    except OutreachBlocked as error:
        raise FollowUpNotEligible(str(error)) from error
    meeting_offer = _meeting_offer_for_draft(session, row, workspace_id)
    if meeting_offer is not None:
        offer_details = (
            meeting_offer.details if isinstance(meeting_offer.details, dict) else {}
        )
        if offer_details.get("thread_id") and not same_gmail_subject(
            row.subject, str(offer_details.get("source_subject") or "")
        ):
            raise FollowUpNotEligible(
                "Keep the original Gmail subject in this appointment reply so it stays in the same thread."
            )
        if offer_details.get("thread_id") and not offer_details.get("source_rfc_message_id"):
            raise FollowUpNotEligible(
                "Refresh the Gmail conversation before sending this appointment reply."
            )
        if not manual_selected_slot_body_is_intact(offer_details, row.body or ""):
            raise FollowUpNotEligible(
                "Keep the booking link in the appointment draft so the lead can book."
                if offer_details.get("booking_link_url")
                else "Keep the selected time as the only numbered option so the lead’s reply "
                "can book the correct slot."
            )
        try:
            source_connection_id = UUID(
                str(offer_details.get("gmail_connection_id") or row.provider_connection_id)
            )
        except (TypeError, ValueError) as error:
            raise FollowUpNotEligible(
                "Reconnect the Gmail account used for this meeting thread."
            ) from error
        connection = session.scalar(
            select(ProviderConnectionORM).where(
                ProviderConnectionORM.id == source_connection_id,
                ProviderConnectionORM.workspace_id == workspace_id,
                ProviderConnectionORM.provider_type == "gmail",
                ProviderConnectionORM.is_active.is_(True),
                ProviderConnectionORM.status == "verified",
            )
        )
    else:
        connection = session.scalar(
            select(ProviderConnectionORM)
            .where(
                ProviderConnectionORM.workspace_id == workspace_id,
                ProviderConnectionORM.provider_type == "gmail",
                ProviderConnectionORM.is_active.is_(True),
                ProviderConnectionORM.status == "verified",
            )
            .order_by(ProviderConnectionORM.updated_at.desc())
        )
    decoded_attachments: list[tuple[str, str, bytes]] = []
    total_attachment_bytes = 0
    for item in row.attachments or []:
        try:
            content = base64.b64decode(str(item["content_base64"]), validate=True)
            filename = str(item["filename"])
            content_type = str(item["content_type"])
        except (KeyError, TypeError, ValueError, binascii.Error):
            raise FollowUpNotEligible("An attachment in this draft is invalid. Remove it and save again.") from None
        total_attachment_bytes += len(content)
        if len(decoded_attachments) >= 3 or total_attachment_bytes > 10 * 1024 * 1024:
            raise FollowUpNotEligible("Keep up to 3 attachments totaling 10 MB or less.")
        decoded_attachments.append((filename, content_type, content))
    if connection is None:
        raise FollowUpNotEligible("Connect and verify Gmail before sending this follow-up.")
    if GMAIL_SEND_SCOPE not in set(connection.scopes):
        raise FollowUpNotEligible("Reconnect Gmail with send permission to send this follow-up.")
    resolved = get_workspace_connection_resolver().resolve_connection(
        session, workspace_id, connection.id, actor_id=actor_id
    )
    token = resolved.credentials.get("access_token") if resolved else None
    mailbox = str(connection.connection_metadata.get("gmail_email") or connection.account_name)
    if not token or "@" not in mailbox:
        raise FollowUpNotEligible(
            "The verified Gmail mailbox is unavailable. Reconnect Gmail and retry."
        )

    now = datetime.now(UTC)
    claimed = cast(
        CursorResult[Any],
        session.execute(
            update(LeadFollowUpEmailORM)
            .where(
                LeadFollowUpEmailORM.id == row.id,
                LeadFollowUpEmailORM.workspace_id == workspace_id,
                LeadFollowUpEmailORM.status == "draft",
            )
            .values(
                provider_connection_id=connection.id,
                status="sending",
                attempts=LeadFollowUpEmailORM.attempts + 1,
                approved_by=actor_id[:255],
                approved_at=now,
                error_message=None,
                updated_at=now,
            )
        ),
    )
    if claimed.rowcount != 1:
        session.rollback()
        raise FollowUpNotEligible(
            "This follow-up was already sent or is being sent. Refresh its status."
        )
    session.commit()
    session.refresh(row)
    try:
        meeting_details = (
            meeting_offer.details
            if meeting_offer is not None and isinstance(meeting_offer.details, dict)
            else {}
        )
        send_options: dict[str, str] = {}
        if meeting_offer is not None:
            thread_id = str(meeting_details.get("thread_id") or "")
            in_reply_to = str(meeting_details.get("source_rfc_message_id") or "")
            if thread_id:
                send_options["thread_id"] = thread_id
            if in_reply_to:
                send_options["in_reply_to"] = in_reply_to
        provider = GmailOutboundProvider(connection.id, token, mailbox)
        if row.cc or row.bcc or row.html_body or decoded_attachments:
            result = provider.send_email_with_metadata(
                recipient=row.recipient,
                cc=row.cc or "",
                bcc=row.bcc or "",
                subject=row.subject,
                body=row.body or "",
                html_body=row.html_body,
                attachments=decoded_attachments,
                **send_options,
            )
            message_id = result.get("message_id")
        else:
            message_id = provider.send_email(
                recipient=row.recipient,
                subject=row.subject,
                body=row.body or "",
                **send_options,
            )
    except ProviderOperationError as error:
        row.status = (
            "delivery_uncertain"
            if error.retryable
            else "draft" if meeting_offer is not None else "failed"
        )
        row.error_message = (
            "Gmail did not confirm delivery. Check the Sent folder before retrying."
            if error.retryable
            else str(error)[:1000]
        )
        if meeting_offer is not None:
            meeting_offer.status = (
                "needs_attention"
                if error.retryable
                else "awaiting_send"
            )
            meeting_offer.details = {
                **meeting_details,
                "error_code": error.code,
                "error_message": str(error)[:500],
            }
    except Exception:
        row.status = "delivery_uncertain"
        row.error_message = (
            "Gmail delivery could not be confirmed. Check the Sent folder before retrying."
        )
        if meeting_offer is not None:
            meeting_offer.status = "needs_attention"
            meeting_offer.details = {
                **meeting_details,
                "error_message": row.error_message,
            }
    else:
        row.status = "sent"
        row.external_message_id = message_id
        row.sent_at = datetime.now(UTC)
        row.error_message = None
        if meeting_offer is not None:
            meeting_offer.status = "awaiting_confirmation"
            meeting_offer.details = {
                **meeting_details,
                "outbound_message_id": message_id,
                "offered_at": row.sent_at.isoformat(),
                "error_code": "",
                "error_message": "",
            }
    row.updated_at = datetime.now(UTC)
    session.commit()
    session.refresh(row)
    return row
