from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from lead_agent.domain.analysis import (
    LeadContextAnalysis,
    ReasoningOutput,
    ReasoningProvider,
    ReasoningProviderError,
)
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.intake import SourceEvent
from lead_agent.domain.models import NormalizedLead, QualificationResult
from lead_agent.domain.normalization import normalize_lead
from lead_agent.domain.scoring import find_hard_disqualifier, qualify_lead
from lead_agent.infrastructure.db.models import LeadORM, SourceEventORM
from lead_agent.infrastructure.reasoning.deterministic import DeterministicReasoningProvider
from lead_agent.infrastructure.repositories import (
    create_lead_with_result,
    create_source_event,
    get_source_event,
    update_source_event,
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


def process_lead(
    session: Session,
    payload: dict[str, object],
    icp: IcpConfig,
    routing: RoutingConfig,
    provider: ReasoningProvider | None = None,
) -> tuple[NormalizedLead, QualificationResult, LeadORM]:
    lead = normalize_lead(payload, uuid4())
    analysis, result = _analyze_and_qualify(lead, icp, routing, provider)
    row = create_lead_with_result(session, lead, result, analysis=analysis)
    if row.source_event is not None and row.source_event.status == "received":
        row.source_event.status = (
            "manual_review" if analysis.requires_manual_review else "processed"
        )
        row.source_event.error_message = analysis.error_message
        row.source_event.processed_at = datetime.now(UTC)
        row.source_event.updated_at = datetime.now(UTC)
        session.commit()
    return lead, result, row


def process_source_event(
    session: Session,
    event: SourceEvent,
    icp: IcpConfig,
    routing: RoutingConfig,
    provider: ReasoningProvider | None = None,
) -> IntakeResult:
    """Persist, process, and replay one channel-agnostic source event."""

    source_event = get_source_event(session, event.channel, event.external_id)
    if source_event and source_event.status in {"processed", "manual_review"}:
        existing_lead = source_event.lead
        if existing_lead is not None:
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
        )

    update_source_event(session, source_event, status="processing", increment_attempt=True)
    try:
        lead = normalize_lead(event.normalized_payload, uuid4())
        analysis, result = _analyze_and_qualify(lead, icp, routing, provider)
        row = create_lead_with_result(
            session, lead, result, analysis=analysis, source_event=source_event
        )
        now = datetime.now(UTC)
        source_event.status = "manual_review" if analysis.requires_manual_review else "processed"
        source_event.error_message = analysis.error_message
        source_event.processed_at = now
        source_event.updated_at = now
        session.commit()
        return IntakeResult(str(source_event.id), source_event.status, row)
    except Exception as error:
        session.rollback()
        failed = session.get(type(source_event), source_event.id)
        if failed is not None:
            update_source_event(
                session,
                failed,
                status="failed",
                error_message=str(error)[:2000],
            )
        raise


def mark_source_event_manual_review(session: Session, event_id: str, reason: str) -> None:
    row = session.get(SourceEventORM, UUID(event_id))
    if row is not None:
        update_source_event(session, row, status="manual_review", error_message=reason[:2000])
