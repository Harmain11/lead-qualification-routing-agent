from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from lead_agent.domain.models import NormalizedLead, QualificationResult
from lead_agent.infrastructure.db.models import (
    LeadORM,
    OutboxMessageORM,
    QualificationResultORM,
    SourceEventORM,
)


def _qualification_row(
    lead_id: UUID, result: QualificationResult, now: datetime
) -> QualificationResultORM:
    data = result.as_dict()
    return QualificationResultORM(
        id=uuid4(),
        lead_id=lead_id,
        total_score=result.total_score,
        classification=result.classification,
        routing_queue=result.routing_queue,
        routing_action=result.routing_action,
        hard_disqualified=result.hard_disqualified,
        disqualifier_code=result.disqualifier_code,
        rationale=data["rationale"],
        score_breakdown=data["score_breakdown"],
        component_scores=data["component_scores"],
        policy_version=result.policy_version,
        confidence=result.confidence,
        created_at=now,
    )


def create_lead_with_result(
    session: Session,
    lead: NormalizedLead,
    result: QualificationResult,
) -> LeadORM:
    now = datetime.now(UTC)
    source_event: SourceEventORM | None = None
    if lead.external_id:
        source_event = session.scalar(
            select(SourceEventORM).where(
                SourceEventORM.provider == lead.source,
                SourceEventORM.external_event_id == lead.external_id,
            )
        )
        if source_event and source_event.lead:
            return source_event.lead
    if not source_event:
        source_event = SourceEventORM(
            id=uuid4(),
            provider=lead.source,
            external_event_id=lead.external_id,
            raw_payload=lead.raw_payload,
            received_at=lead.received_at,
            created_at=now,
        )
        session.add(source_event)

    row = LeadORM(
        id=lead.id,
        source_event_id=source_event.id if source_event else None,
        source=lead.source,
        source_detail=lead.source_detail,
        external_id=lead.external_id,
        full_name=lead.full_name,
        email=lead.email,
        phone=lead.phone,
        job_title=lead.job_title,
        company_name=lead.company_name,
        industry=lead.industry,
        employee_count=lead.employee_count,
        annual_revenue=float(lead.annual_revenue) if lead.annual_revenue is not None else None,
        geography=lead.geography,
        budget=float(lead.budget) if lead.budget is not None else None,
        product_or_service=lead.product_or_service,
        message=lead.message,
        received_at=lead.received_at,
        raw_payload=lead.raw_payload,
        normalization_version=lead.normalization_version,
        created_at=now,
        updated_at=now,
    )
    row.qualification = _qualification_row(lead.id, result, now)
    session.add(row)
    session.add(
        OutboxMessageORM(
            id=uuid4(),
            event_type="lead.qualified",
            aggregate_id=lead.id,
            payload={"lead_id": str(lead.id), "classification": result.classification},
            status="pending",
            created_at=now,
        )
    )
    session.commit()
    return get_lead(session, lead.id)  # type: ignore[return-value]


def list_leads(session: Session) -> list[LeadORM]:
    return list(
        session.scalars(
            select(LeadORM)
            .options(joinedload(LeadORM.qualification))
            .order_by(LeadORM.created_at.desc())
        ).unique()
    )


def get_lead(session: Session, lead_id: UUID) -> LeadORM | None:
    return session.scalar(
        select(LeadORM).options(joinedload(LeadORM.qualification)).where(LeadORM.id == lead_id)
    )


def get_qualification(session: Session, lead_id: UUID) -> QualificationResultORM | None:
    return session.scalar(
        select(QualificationResultORM).where(QualificationResultORM.lead_id == lead_id)
    )
