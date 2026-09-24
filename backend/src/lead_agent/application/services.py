from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from lead_agent.application.crm import sync_lead_to_crm
from lead_agent.application.icp import resolve_workspace_icp
from lead_agent.application.routing import record_pipeline_event, route_and_notify
from lead_agent.domain.analysis import (
    LeadContextAnalysis,
    ReasoningOutput,
    ReasoningProvider,
    ReasoningProviderError,
)
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.crm import CrmProvider
from lead_agent.domain.intake import SourceEvent
from lead_agent.domain.models import NormalizedLead, QualificationResult
from lead_agent.domain.normalization import normalize_lead
from lead_agent.domain.routing import NotificationProvider
from lead_agent.domain.scoring import find_hard_disqualifier, qualify_lead
from lead_agent.domain.workspaces import DEFAULT_WORKSPACE_ID
from lead_agent.infrastructure.db.models import LeadORM, SourceEventORM
from lead_agent.infrastructure.reasoning.deterministic import DeterministicReasoningProvider
from lead_agent.infrastructure.repositories import (
    create_lead_with_result,
    create_source_event,
    get_current_routing,
    get_source_event,
    update_lead_with_result,
    update_source_event,
)


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
    notification_provider: NotificationProvider | None = None,
    crm_provider: CrmProvider | None = None,
    crm_max_attempts: int = 3,
    crm_payload_version: str = "1.0",
    workspace_id: UUID = DEFAULT_WORKSPACE_ID,
    sync_crm: bool = True,
) -> tuple[NormalizedLead, QualificationResult, LeadORM]:
    resolved_icp = resolve_workspace_icp(session, workspace_id, icp)
    lead = normalize_lead(payload, uuid4())
    analysis, result = _analyze_and_qualify(lead, resolved_icp.configuration, routing, provider)
    result = resolved_icp.trace_result(result)
    row = create_lead_with_result(
        session, lead, result, analysis=analysis, workspace_id=workspace_id
    )
    if get_current_routing(session, row.id, workspace_id) is None:
        _record_pre_routing_history(session, row, analysis, result)
    route_and_notify(session, row, routing, notification_provider=notification_provider)
    if sync_crm:
        sync_lead_to_crm(
            session,
            row,
            crm_provider,
            max_attempts=crm_max_attempts,
            payload_version=crm_payload_version,
        )
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
    notification_provider: NotificationProvider | None = None,
    crm_provider: CrmProvider | None = None,
    crm_max_attempts: int = 3,
    crm_payload_version: str = "1.0",
    workspace_id: UUID = DEFAULT_WORKSPACE_ID,
    sync_crm: bool = True,
) -> IntakeResult:
    """Persist, process, and replay one channel-agnostic source event."""

    source_event = get_source_event(session, event.channel, event.external_id, workspace_id)
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
            workspace_id=workspace_id,
        )

    update_source_event(session, source_event, status="processing", increment_attempt=True)
    try:
        resolved_icp = resolve_workspace_icp(session, workspace_id, icp)
        lead = normalize_lead(event.normalized_payload, uuid4())
        analysis, result = _analyze_and_qualify(
            lead, resolved_icp.configuration, routing, provider
        )
        result = resolved_icp.trace_result(result)
        row = create_lead_with_result(
            session,
            lead,
            result,
            analysis=analysis,
            source_event=source_event,
            workspace_id=workspace_id,
        )
        if get_current_routing(session, row.id, workspace_id) is None:
            _record_pre_routing_history(session, row, analysis, result)
        route_and_notify(session, row, routing, notification_provider=notification_provider)
        if sync_crm:
            sync_lead_to_crm(
                session,
                row,
                crm_provider,
                max_attempts=crm_max_attempts,
                payload_version=crm_payload_version,
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


def reprocess_lead(
    session: Session,
    row: LeadORM,
    payload: dict[str, object],
    icp: IcpConfig,
    routing: RoutingConfig,
    provider: ReasoningProvider | None = None,
    notification_provider: NotificationProvider | None = None,
) -> tuple[NormalizedLead, QualificationResult, LeadORM]:
    """Run a meaningful source update through the canonical qualification pipeline."""

    resolved_icp = resolve_workspace_icp(session, row.workspace_id, icp)
    normalized = normalize_lead(payload, row.id)
    analysis, result = _analyze_and_qualify(
        normalized, resolved_icp.configuration, routing, provider
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
    row = update_lead_with_result(session, row, normalized, result, analysis)
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
        reroute_reason="Meaningful CRM source fields changed.",
    )
    return normalized, result, row


def mark_source_event_manual_review(session: Session, event_id: str, reason: str) -> None:
    row = session.get(SourceEventORM, UUID(event_id))
    if row is not None:
        update_source_event(session, row, status="manual_review", error_message=reason[:2000])
