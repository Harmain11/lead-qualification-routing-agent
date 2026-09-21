from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.intake import SourceEvent
from lead_agent.domain.models import NormalizedLead, QualificationResult
from lead_agent.domain.normalization import normalize_lead
from lead_agent.domain.scoring import qualify_lead
from lead_agent.infrastructure.db.models import LeadORM, SourceEventORM
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


def process_lead(
    session: Session,
    payload: dict[str, object],
    icp: IcpConfig,
    routing: RoutingConfig,
) -> tuple[NormalizedLead, QualificationResult, LeadORM]:
    lead = normalize_lead(payload, uuid4())
    result = qualify_lead(lead, icp, routing)
    row = create_lead_with_result(session, lead, result)
    return lead, result, row


def process_source_event(
    session: Session,
    event: SourceEvent,
    icp: IcpConfig,
    routing: RoutingConfig,
) -> IntakeResult:
    """Persist, process, and replay one channel-agnostic source event."""

    source_event = get_source_event(session, event.channel, event.external_id)
    if source_event and source_event.status == "processed":
        lead = source_event.lead
        if lead is not None:
            return IntakeResult(str(source_event.id), source_event.status, lead, duplicate=True)

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
        result = qualify_lead(lead, icp, routing)
        row = create_lead_with_result(session, lead, result, source_event=source_event)
        now = datetime.now(UTC)
        source_event.status = "processed"
        source_event.error_message = None
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


def mark_source_event_manual_review(
    session: Session, event_id: str, reason: str
) -> None:
    row = session.get(SourceEventORM, UUID(event_id))
    if row is not None:
        update_source_event(session, row, status="manual_review", error_message=reason[:2000])
