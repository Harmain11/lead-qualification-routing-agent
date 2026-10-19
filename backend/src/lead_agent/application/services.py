from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.application.email_templates import get_email_template, render_email_template
from lead_agent.application.governance import assert_outreach_allowed
from lead_agent.application.icp import resolve_workspace_icp
from lead_agent.application.routing import record_pipeline_event, route_and_notify
from lead_agent.domain.analysis import (
    LeadContextAnalysis,
    ReasoningOutput,
    ReasoningProvider,
    ReasoningProviderError,
    ReasoningProviderTimeout,
    ReasoningProviderUnavailable,
)
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.intake import SourceEvent
from lead_agent.domain.models import NormalizedLead, QualificationResult
from lead_agent.domain.normalization import normalize_lead
from lead_agent.domain.routing import NotificationProvider
from lead_agent.domain.scoring import find_hard_disqualifier, qualify_lead
from lead_agent.domain.workspaces import DEFAULT_WORKSPACE_ID
from lead_agent.infrastructure.db.models import (
    LeadFollowUpEmailORM,
    LeadORM,
    ProviderConnectionORM,
    SourceEventORM,
    WorkspaceEmailAutomationSettingORM,
)
from lead_agent.infrastructure.gmail import GMAIL_SEND_SCOPE, GmailOutboundProvider
from lead_agent.infrastructure.provider_lifecycle import get_workspace_connection_resolver
from lead_agent.infrastructure.reasoning.deterministic import DeterministicReasoningProvider
from lead_agent.infrastructure.reasoning.factory import resolve_workspace_reasoning_provider
from lead_agent.infrastructure.repositories import (
    create_lead_with_result,
    create_source_event,
    get_current_routing,
    get_source_event,
    update_lead_with_result,
    update_source_event,
)

_LOGGER = logging.getLogger("lead_agent.intake")


def _record_pre_routing_history(
    session: Session,
    row: LeadORM,
    analysis: LeadContextAnalysis,
    result: QualificationResult,
) -> None:
    source_event_id = str(row.source_event_id) if row.source_event_id else None
    record_pipeline_event(
        session,
        row.id,
        "received",
        "completed",
        {"source": row.source, "source_event_id": source_event_id},
    )
    record_pipeline_event(
        session,
        row.id,
        "normalized",
        "completed",
        {"normalization_version": row.normalization_version},
    )
    record_pipeline_event(
        session,
        row.id,
        "analyzed",
        "manual_review" if analysis.requires_manual_review else "completed",
        {"provider": analysis.provider, "confidence": analysis.overall_confidence},
    )
    record_pipeline_event(
        session,
        row.id,
        "qualified",
        "completed",
        {
            "band": result.classification,
            "score": result.total_score,
            "icp_source": result.icp_source,
            "icp_version_id": str(result.icp_version_id) if result.icp_version_id else None,
            "icp_profile_version": result.icp_profile_version,
        },
    )


def _deliver_lead_confirmation_email(session: Session, row: LeadORM) -> None:
    """Send one idempotent confirmation when the workspace has opted in."""
    recipient = (row.email or "").strip().lower()
    if "@" not in recipient:
        return
    automation = session.get(WorkspaceEmailAutomationSettingORM, row.workspace_id)
    if automation is None or not automation.lead_confirmation_enabled:
        return
    assert_outreach_allowed(session, row.workspace_id, recipient)
    connection = session.scalar(
        select(ProviderConnectionORM)
        .where(
            ProviderConnectionORM.workspace_id == row.workspace_id,
            ProviderConnectionORM.provider_type == "gmail",
            ProviderConnectionORM.is_active.is_(True),
            ProviderConnectionORM.status == "verified",
        )
        .order_by(ProviderConnectionORM.updated_at.desc())
    )
    if connection is None:
        return
    connection_id = connection.id
    connection_scopes = set(connection.scopes)
    mailbox = str(connection.connection_metadata.get("gmail_email") or connection.account_name)
    existing = session.scalar(
        select(LeadFollowUpEmailORM).where(
            LeadFollowUpEmailORM.lead_id == row.id,
            LeadFollowUpEmailORM.kind == "lead_confirmation",
        )
    )
    if existing is not None and existing.status in {"sent", "sending"}:
        return
    session.rollback()
    resolved = get_workspace_connection_resolver().resolve_connection(
        session, row.workspace_id, connection_id, actor_id="system"
    )
    session.rollback()
    existing = session.scalar(
        select(LeadFollowUpEmailORM).where(
            LeadFollowUpEmailORM.lead_id == row.id,
            LeadFollowUpEmailORM.kind == "lead_confirmation",
        )
    )
    if existing is not None and existing.status in {"sent", "sending"}:
        return

    now = datetime.now(UTC)
    template = render_email_template(
        get_email_template(session, row.workspace_id, "confirmation"), row
    )
    subject = template["subject"]
    body = template["body"]
    if existing is None:
        existing = LeadFollowUpEmailORM(
            id=uuid4(),
            workspace_id=row.workspace_id,
            lead_id=row.id,
            provider_connection_id=connection_id,
            kind="lead_confirmation",
            recipient=recipient,
            subject=subject,
            status="pending",
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        session.add(existing)
        try:
            session.flush()
            session.commit()
        except IntegrityError:
            # Another intake worker may have created the unique lead-confirmation
            # row after our lookup. Re-read it and compete for the send claim.
            session.rollback()
            existing = session.scalar(
                select(LeadFollowUpEmailORM).where(
                    LeadFollowUpEmailORM.lead_id == row.id,
                    LeadFollowUpEmailORM.kind == "lead_confirmation",
                )
            )
            if existing is None:
                raise
            if existing.status in {"sent", "sending"}:
                return
    elif existing.provider_connection_id != connection_id:
        existing.provider_connection_id = connection_id
        existing.recipient = recipient
        existing.error_message = None
        existing.status = "pending"
        existing.updated_at = now
    elif existing.attempts >= 3 and existing.status in {"failed", "retryable"}:
        return

    if GMAIL_SEND_SCOPE not in connection_scopes:
        existing.status = "failed"
        existing.error_message = (
            "Reconnect the admin Gmail connection and approve Gmail send permission "
            "to enable automatic lead confirmations."
        )
        existing.updated_at = now
        session.commit()
        return
    access_token = resolved.credentials.get("access_token") if resolved else None
    if resolved is None or not access_token or "@" not in mailbox:
        existing.status = "failed"
        existing.error_message = "The connected Gmail mailbox is not available for sending."
        existing.updated_at = now
        session.commit()
        return
    session.flush()
    claimed_id = session.scalar(
        update(LeadFollowUpEmailORM)
        .where(
            LeadFollowUpEmailORM.id == existing.id,
            LeadFollowUpEmailORM.status.in_(("pending", "retryable", "failed")),
            LeadFollowUpEmailORM.attempts < 3,
        )
        .values(
            status="sending",
            attempts=LeadFollowUpEmailORM.attempts + 1,
            updated_at=now,
        )
        .returning(LeadFollowUpEmailORM.id)
    )
    session.commit()
    if claimed_id is None:
        return
    try:
        message_id = GmailOutboundProvider(connection_id, access_token, mailbox).send_email(
            recipient=recipient, subject=subject, body=body
        )
    except Exception as error:  # Do not fail lead ingestion because confirmation mail failed.
        existing.status = "retryable" if getattr(error, "retryable", False) else "failed"
        existing.error_message = str(error)[:1000]
        existing.updated_at = datetime.now(UTC)
        session.commit()
        return
    existing.status = "sent"
    existing.external_message_id = message_id
    existing.error_message = None
    existing.sent_at = datetime.now(UTC)
    existing.updated_at = datetime.now(UTC)
    session.commit()


def _send_lead_confirmation_email(session: Session, row: LeadORM) -> None:
    """Attempt delivery without allowing email problems to fail lead intake."""
    try:
        _deliver_lead_confirmation_email(session, row)
    except Exception as error:  # pragma: no cover - defensive integration boundary
        session.rollback()
        _LOGGER.warning(
            "lead confirmation delivery deferred",
            extra={
                "lead_id": str(row.id),
                "workspace_id": str(row.workspace_id),
                "error_type": error.__class__.__name__,
                "error_message": str(error)[:500],
            },
        )


@dataclass(frozen=True)
class IntakeResult:
    source_event_id: str
    status: str
    lead: LeadORM
    duplicate: bool = False


def _context_analysis(
    lead: NormalizedLead,
    icp: IcpConfig,
    routing: RoutingConfig,
    provider: ReasoningProvider | None,
) -> LeadContextAnalysis:
    fallback = DeterministicReasoningProvider()
    disqualifier = find_hard_disqualifier(lead, icp)
    if disqualifier:
        output = fallback.analyze(lead, icp)
        code, _ = disqualifier
        grounded_icp = output.icp_evaluation.model_copy(update={"explicit_disqualifiers": [code]})
        return LeadContextAnalysis(
            **output.model_copy(update={"icp_evaluation": grounded_icp}).model_dump(),
            status="skipped_hard_disqualifier",
            provider="deterministic-safeguard",
            model=fallback.model_name,
            prompt_version=fallback.prompt_version,
            requires_manual_review=False,
        )

    active_provider = provider or fallback
    try:
        output = ReasoningOutput.model_validate(active_provider.analyze(lead, icp))
    except Exception as error:
        output = fallback.analyze(lead, icp)
        code = (
            error.code if isinstance(error, ReasoningProviderError) else "invalid_provider_output"
        )
        return LeadContextAnalysis(
            **output.model_dump(),
            status="provider_error",
            provider=getattr(active_provider, "provider_name", "unknown"),
            model=getattr(active_provider, "model_name", "unknown"),
            prompt_version=getattr(active_provider, "prompt_version", "unknown"),
            requires_manual_review=True,
            error_code=code,
            error_message=str(error)[:2000] or error.__class__.__name__,
        )

    requires_review = output.overall_confidence < routing.manual_review.minimum_confidence or bool(
        output.contradictions
    )
    return LeadContextAnalysis(
        **output.model_dump(),
        status="manual_review" if requires_review else "completed",
        provider=active_provider.provider_name,
        model=active_provider.model_name,
        prompt_version=active_provider.prompt_version,
        requires_manual_review=requires_review,
        error_code="low_confidence_or_contradiction" if requires_review else None,
        error_message=(
            "Analysis confidence is below policy threshold or contains contradictory evidence."
            if requires_review
            else None
        ),
    )


def _analyze_and_qualify(
    lead: NormalizedLead,
    icp: IcpConfig,
    routing: RoutingConfig,
    provider: ReasoningProvider | None,
) -> tuple[LeadContextAnalysis, QualificationResult]:
    analysis = _context_analysis(lead, icp, routing, provider)
    result = qualify_lead(lead, icp, routing, analysis)
    summary = analysis.summary.model_copy(
        update={
            "qualification_result": (
                f"{result.classification.replace('_', ' ').title()} ({result.total_score}/100)"
            )
        }
    )
    return analysis.model_copy(update={"summary": summary}), result


def _queue_reasoning_analysis(workspace_id: UUID, lead_id: UUID) -> None:
    """Queue a post-commit idempotent check for the active workspace model."""
    try:
        from lead_agent.worker.celery_app import analyze_workspace_lead

        analyze_workspace_lead.delay(str(workspace_id), str(lead_id))
    except Exception:
        logging.getLogger(__name__).exception(
            "reasoning.analysis_enqueue_failed",
            extra={"fields": {"workspace_id": str(workspace_id), "lead_id": str(lead_id)}},
        )


def process_lead(
    session: Session,
    payload: dict[str, object],
    icp: IcpConfig,
    routing: RoutingConfig,
    provider: ReasoningProvider | None = None,
    notification_provider: NotificationProvider | None = None,
    workspace_id: UUID = DEFAULT_WORKSPACE_ID,
) -> tuple[NormalizedLead, QualificationResult, LeadORM]:
    from lead_agent.application.governance import assert_residency

    assert_residency(session, workspace_id)
    resolved_icp = resolve_workspace_icp(session, workspace_id, icp)
    lead = normalize_lead(payload, uuid4())
    active_provider = provider or resolve_workspace_reasoning_provider(session, workspace_id)
    analysis, result = _analyze_and_qualify(
        lead, resolved_icp.configuration, routing, active_provider
    )
    result = resolved_icp.trace_result(result)
    row = create_lead_with_result(
        session, lead, result, analysis=analysis, workspace_id=workspace_id
    )
    if get_current_routing(session, row.id, workspace_id) is None:
        _record_pre_routing_history(session, row, analysis, result)
    route_and_notify(session, row, routing, notification_provider=notification_provider)
    _send_lead_confirmation_email(session, row)
    if row.source_event is not None and row.source_event.status == "received":
        row.source_event.status = (
            "manual_review" if analysis.requires_manual_review else "processed"
        )
        row.source_event.error_message = analysis.error_message
        row.source_event.processed_at = datetime.now(UTC)
        row.source_event.updated_at = datetime.now(UTC)
        session.commit()
    if provider is None and getattr(active_provider, "provider_name", "") not in {
        "", "deterministic-safeguard", "deterministic"
    }:
        _queue_reasoning_analysis(workspace_id, row.id)
    return lead, result, row


def process_source_event(
    session: Session,
    event: SourceEvent,
    icp: IcpConfig,
    routing: RoutingConfig,
    provider: ReasoningProvider | None = None,
    notification_provider: NotificationProvider | None = None,
    workspace_id: UUID = DEFAULT_WORKSPACE_ID,
    send_confirmation_email: bool = True,
    manual_review_reason: str | None = None,
) -> IntakeResult:
    """Persist, process, and replay one channel-agnostic source event."""

    from lead_agent.application.governance import assert_residency

    assert_residency(session, workspace_id)
    source_event = get_source_event(session, event.channel, event.external_id, workspace_id)
    if source_event and source_event.status in {"processed", "manual_review"}:
        existing_lead = source_event.lead
        if existing_lead is not None:
            # A provider outage can leave the lead processed while its
            # confirmation email is retryable. Re-check the idempotent email
            # record on duplicate webhook deliveries so a later retry can
            # complete the confirmation without duplicating the lead.
            if send_confirmation_email:
                _send_lead_confirmation_email(session, existing_lead)
            return IntakeResult(
                str(source_event.id), source_event.status, existing_lead, duplicate=True
            )

    if source_event is None:
        source_event = create_source_event(
            session,
            event.channel,
            event.external_id,
            event.raw_payload,
            event.received_at,
            workspace_id=workspace_id,
        )
    if source_event is None:
        raise RuntimeError("source event could not be created")
    source_event_id = source_event.id

    update_source_event(session, source_event, status="processing", increment_attempt=True)
    try:
        resolved_icp = resolve_workspace_icp(session, workspace_id, icp)
        lead = normalize_lead(event.normalized_payload, uuid4())
        active_provider = provider or resolve_workspace_reasoning_provider(session, workspace_id)
        analysis, result = _analyze_and_qualify(
            lead, resolved_icp.configuration, routing, active_provider
        )
        if manual_review_reason:
            review_update: dict[str, Any] = {"requires_manual_review": True}
            if analysis.status == "completed":
                review_update["status"] = "manual_review"
            analysis = analysis.model_copy(update=review_update)
        result = resolved_icp.trace_result(result)
        try:
            row = create_lead_with_result(
                session,
                lead,
                result,
                analysis=analysis,
                source_event=source_event,
                workspace_id=workspace_id,
            )
        except IntegrityError:
            # A second worker may have completed the same source event after
            # the initial read. The unique source-event boundary makes that
            # race safe; continue with the committed canonical lead.
            session.rollback()
            source_event = get_source_event(session, event.channel, event.external_id, workspace_id)
            if source_event is None or source_event.lead is None:
                raise
            row = source_event.lead
        if get_current_routing(session, row.id, workspace_id) is None:
            _record_pre_routing_history(session, row, analysis, result)
        route_and_notify(
            session,
            row,
            routing,
            notification_provider=notification_provider,
            manual_review_reason=manual_review_reason,
        )
        now = datetime.now(UTC)
        source_event.status = "manual_review" if analysis.requires_manual_review else "processed"
        source_event.error_message = analysis.error_message
        source_event.processed_at = now
        source_event.updated_at = now
        session.commit()
        # Commit the lead/source event before touching Gmail or its delivery
        # ledger. This makes intake durable even when email storage/provider
        # work is temporarily locked or unavailable.
        if send_confirmation_email:
            _send_lead_confirmation_email(session, row)
        if provider is None and getattr(active_provider, "provider_name", "") not in {
            "", "deterministic-safeguard", "deterministic"
        }:
            _queue_reasoning_analysis(workspace_id, row.id)
        return IntakeResult(str(source_event.id), source_event.status, row)
    except Exception as error:
        session.rollback()
        failed = session.get(SourceEventORM, source_event_id)
        if failed is not None:
            update_source_event(
                session,
                failed,
                status="failed",
                error_message=str(error)[:2000],
            )
        raise


def reprocess_lead(
    session: Session,
    row: LeadORM,
    payload: dict[str, object],
    icp: IcpConfig,
    routing: RoutingConfig,
    provider: ReasoningProvider | None = None,
    notification_provider: NotificationProvider | None = None,
    send_notification: bool = True,
    reroute_reason: str = "Meaningful CRM source fields changed.",
    require_provider_success: bool = False,
    send_confirmation_email: bool = True,
    preserve_inquiry: bool = False,
    manual_review_reason: str | None = None,
) -> tuple[NormalizedLead, QualificationResult, LeadORM]:
    """Run a meaningful source update through the canonical qualification pipeline."""

    from lead_agent.application.governance import assert_residency

    assert_residency(session, row.workspace_id)
    resolved_icp = resolve_workspace_icp(session, row.workspace_id, icp)
    normalized = normalize_lead(payload, row.id)
    active_provider = provider or resolve_workspace_reasoning_provider(session, row.workspace_id)
    analysis, result = _analyze_and_qualify(
        normalized, resolved_icp.configuration, routing, active_provider
    )
    if manual_review_reason:
        review_update: dict[str, Any] = {"requires_manual_review": True}
        if analysis.status == "completed":
            review_update["status"] = "manual_review"
        analysis = analysis.model_copy(update=review_update)
    if require_provider_success and analysis.status == "provider_error":
        if analysis.error_code == ReasoningProviderTimeout.code:
            raise ReasoningProviderTimeout(
                analysis.error_message or "Workspace reasoning provider timed out"
            )
        if analysis.error_code == ReasoningProviderUnavailable.code:
            raise ReasoningProviderUnavailable(
                analysis.error_message or "Workspace reasoning provider is unavailable"
            )
        raise ReasoningProviderError(
            analysis.error_message or "Workspace reasoning provider failed"
        )
    result = resolved_icp.trace_result(result)
    previous = (
        {
            "score": row.qualification.total_score,
            "band": row.qualification.classification,
        }
        if row.qualification is not None
        else None
    )
    row = update_lead_with_result(
        session, row, normalized, result, analysis, preserve_inquiry=preserve_inquiry
    )
    record_pipeline_event(
        session,
        row.id,
        "source_updated",
        "completed",
        {
            "previous_qualification": previous,
            "source": row.source,
            "normalization_version": row.normalization_version,
        },
    )
    _record_pre_routing_history(session, row, analysis, result)
    session.commit()
    route_and_notify(
        session,
        row,
        routing,
        notification_provider=notification_provider,
        force=True,
        reroute_reason=reroute_reason,
        send_notification=send_notification,
        manual_review_reason=manual_review_reason,
    )
    if send_confirmation_email:
        _send_lead_confirmation_email(session, row)
    if provider is None and getattr(active_provider, "provider_name", "") not in {
        "", "deterministic-safeguard", "deterministic"
    }:
        _queue_reasoning_analysis(row.workspace_id, row.id)
    return normalized, result, row


def mark_source_event_manual_review(session: Session, event_id: str, reason: str) -> None:
    row = session.get(SourceEventORM, UUID(event_id))
    if row is not None:
        update_source_event(session, row, status="manual_review", error_message=reason[:2000])
