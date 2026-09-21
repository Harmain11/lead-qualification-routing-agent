from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from lead_agent.domain.analysis import LeadContextAnalysis
from lead_agent.domain.models import NormalizedLead, QualificationResult
from lead_agent.domain.workspaces import DEFAULT_WORKSPACE_ID
from lead_agent.infrastructure.db.models import (
    AnalysisRunORM,
    CrmSyncORM,
    LeadORM,
    NotificationORM,
    OutboxMessageORM,
    PipelineEventORM,
    ProviderConnectionORM,
    QualificationResultORM,
    RoutingDecisionORM,
    SourceEventORM,
    WorkspaceORM,
)


def ensure_workspace(
    session: Session,
    workspace_id: UUID = DEFAULT_WORKSPACE_ID,
    *,
    name: str = "Default Workspace",
) -> WorkspaceORM:
    existing = session.get(WorkspaceORM, workspace_id)
    if existing is not None:
        return existing
    now = datetime.now(UTC)
    row = WorkspaceORM(
        id=workspace_id,
        name=name,
        slug=("default" if workspace_id == DEFAULT_WORKSPACE_ID else f"workspace-{workspace_id}"),
        status="active",
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


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
    analysis: LeadContextAnalysis | None = None,
    source_event: SourceEventORM | None = None,
    workspace_id: UUID = DEFAULT_WORKSPACE_ID,
) -> LeadORM:
    now = datetime.now(UTC)
    ensure_workspace(session, workspace_id)
    if source_event is not None and source_event.workspace_id != workspace_id:
        raise ValueError("source event does not belong to the requested workspace")
    if source_event is None and lead.external_id:
        source_event = session.scalar(
            select(SourceEventORM).where(
                SourceEventORM.provider == lead.source,
                SourceEventORM.external_event_id == lead.external_id,
                SourceEventORM.workspace_id == workspace_id,
            )
        )
        if source_event and source_event.lead:
            return source_event.lead
    if source_event is None:
        source_event = SourceEventORM(
            id=uuid4(),
            workspace_id=workspace_id,
            provider=lead.source,
            external_event_id=lead.external_id,
            raw_payload=lead.raw_payload,
            received_at=lead.received_at,
            created_at=now,
            updated_at=now,
        )
        session.add(source_event)

    row = LeadORM(
        id=lead.id,
        workspace_id=workspace_id,
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
    if analysis is not None:
        row.analysis = AnalysisRunORM(
            id=uuid4(),
            lead_id=lead.id,
            status=analysis.status,
            provider=analysis.provider,
            model=analysis.model,
            prompt_version=analysis.prompt_version,
            confidence=analysis.overall_confidence,
            requires_manual_review=analysis.requires_manual_review,
            error_code=analysis.error_code,
            error_message=analysis.error_message,
            structured_output=analysis.model_dump(mode="json"),
            created_at=now,
        )
    session.add(row)
    session.add(
        OutboxMessageORM(
            id=uuid4(),
            workspace_id=workspace_id,
            event_type="lead.qualified",
            aggregate_id=lead.id,
            payload={"lead_id": str(lead.id), "classification": result.classification},
            status="pending",
            created_at=now,
        )
    )
    session.commit()
    return get_lead(session, lead.id, workspace_id)  # type: ignore[return-value]


def get_source_event(
    session: Session,
    provider: str,
    external_event_id: str,
    workspace_id: UUID = DEFAULT_WORKSPACE_ID,
) -> SourceEventORM | None:
    return session.scalar(
        select(SourceEventORM).where(
            SourceEventORM.provider == provider,
            SourceEventORM.external_event_id == external_event_id,
            SourceEventORM.workspace_id == workspace_id,
        )
    )


def create_source_event(
    session: Session,
    provider: str,
    external_event_id: str,
    raw_payload: dict[str, Any],
    received_at: datetime,
    now: datetime | None = None,
    workspace_id: UUID = DEFAULT_WORKSPACE_ID,
) -> SourceEventORM:
    timestamp = now or datetime.now(UTC)
    ensure_workspace(session, workspace_id)
    row = SourceEventORM(
        id=uuid4(),
        workspace_id=workspace_id,
        provider=provider,
        external_event_id=external_event_id,
        raw_payload=raw_payload,
        received_at=received_at,
        created_at=timestamp,
        updated_at=timestamp,
        status="received",
        processing_attempts=0,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_source_event(
    session: Session,
    row: SourceEventORM,
    *,
    status: str,
    error_message: str | None = None,
    processed_at: datetime | None = None,
    increment_attempt: bool = False,
) -> SourceEventORM:
    row.status = status
    row.error_message = error_message
    row.processed_at = processed_at
    row.updated_at = datetime.now(UTC)
    if increment_attempt:
        row.processing_attempts += 1
    session.commit()
    session.refresh(row)
    return row


def list_leads(session: Session, workspace_id: UUID = DEFAULT_WORKSPACE_ID) -> list[LeadORM]:
    return list(
        session.scalars(
            select(LeadORM)
            .options(joinedload(LeadORM.qualification), joinedload(LeadORM.analysis))
            .where(LeadORM.workspace_id == workspace_id)
            .order_by(LeadORM.created_at.desc())
        ).unique()
    )


def get_lead(
    session: Session, lead_id: UUID, workspace_id: UUID = DEFAULT_WORKSPACE_ID
) -> LeadORM | None:
    return session.scalar(
        select(LeadORM)
        .options(joinedload(LeadORM.qualification), joinedload(LeadORM.analysis))
        .where(LeadORM.id == lead_id, LeadORM.workspace_id == workspace_id)
    )


def get_qualification(
    session: Session, lead_id: UUID, workspace_id: UUID = DEFAULT_WORKSPACE_ID
) -> QualificationResultORM | None:
    return session.scalar(
        select(QualificationResultORM)
        .join(LeadORM)
        .where(
            QualificationResultORM.lead_id == lead_id,
            LeadORM.workspace_id == workspace_id,
        )
    )


def get_current_routing(
    session: Session, lead_id: UUID, workspace_id: UUID = DEFAULT_WORKSPACE_ID
) -> RoutingDecisionORM | None:
    return session.scalar(
        select(RoutingDecisionORM)
        .options(joinedload(RoutingDecisionORM.notifications))
        .where(
            RoutingDecisionORM.lead_id == lead_id,
            RoutingDecisionORM.workspace_id == workspace_id,
            RoutingDecisionORM.is_current.is_(True),
        )
        .order_by(RoutingDecisionORM.created_at.desc())
    )


def list_routing_history(
    session: Session, lead_id: UUID, workspace_id: UUID = DEFAULT_WORKSPACE_ID
) -> list[RoutingDecisionORM]:
    return list(
        session.scalars(
            select(RoutingDecisionORM)
            .options(joinedload(RoutingDecisionORM.notifications))
            .where(
                RoutingDecisionORM.lead_id == lead_id,
                RoutingDecisionORM.workspace_id == workspace_id,
            )
            .order_by(RoutingDecisionORM.created_at.desc())
        ).unique()
    )


def list_notifications(
    session: Session, lead_id: UUID, workspace_id: UUID = DEFAULT_WORKSPACE_ID
) -> list[NotificationORM]:
    return list(
        session.scalars(
            select(NotificationORM)
            .join(RoutingDecisionORM)
            .where(
                RoutingDecisionORM.lead_id == lead_id,
                NotificationORM.workspace_id == workspace_id,
                RoutingDecisionORM.workspace_id == workspace_id,
            )
            .order_by(NotificationORM.created_at.desc())
        )
    )


def list_pipeline_events(
    session: Session, lead_id: UUID, workspace_id: UUID = DEFAULT_WORKSPACE_ID
) -> list[PipelineEventORM]:
    return list(
        session.scalars(
            select(PipelineEventORM)
            .where(
                PipelineEventORM.lead_id == lead_id,
                PipelineEventORM.workspace_id == workspace_id,
            )
            .order_by(PipelineEventORM.created_at.asc())
        )
    )


def get_current_crm_sync(
    session: Session,
    lead_id: UUID,
    provider: str | None = None,
    workspace_id: UUID = DEFAULT_WORKSPACE_ID,
    provider_connection_id: UUID | None = None,
) -> CrmSyncORM | None:
    statement = select(CrmSyncORM).where(
        CrmSyncORM.lead_id == lead_id,
        CrmSyncORM.workspace_id == workspace_id,
        CrmSyncORM.is_current.is_(True),
    )
    if provider is not None:
        statement = statement.where(CrmSyncORM.provider == provider)
    if provider_connection_id is not None:
        statement = statement.where(CrmSyncORM.provider_connection_id == provider_connection_id)
    return session.scalar(statement.order_by(CrmSyncORM.requested_at.desc()))


def list_crm_sync_history(
    session: Session, lead_id: UUID, workspace_id: UUID = DEFAULT_WORKSPACE_ID
) -> list[CrmSyncORM]:
    return list(
        session.scalars(
            select(CrmSyncORM)
            .where(
                CrmSyncORM.lead_id == lead_id,
                CrmSyncORM.workspace_id == workspace_id,
            )
            .order_by(CrmSyncORM.requested_at.desc())
        )
    )


def get_provider_connection(
    session: Session, workspace_id: UUID, connection_id: UUID
) -> ProviderConnectionORM | None:
    return session.scalar(
        select(ProviderConnectionORM).where(
            ProviderConnectionORM.id == connection_id,
            ProviderConnectionORM.workspace_id == workspace_id,
        )
    )


def list_provider_connections(session: Session, workspace_id: UUID) -> list[ProviderConnectionORM]:
    return list(
        session.scalars(
            select(ProviderConnectionORM)
            .where(ProviderConnectionORM.workspace_id == workspace_id)
            .order_by(ProviderConnectionORM.created_at.desc())
        )
    )


def get_active_provider_connection(
    session: Session, workspace_id: UUID, provider_types: set[str]
) -> ProviderConnectionORM | None:
    return session.scalar(
        select(ProviderConnectionORM)
        .where(
            ProviderConnectionORM.workspace_id == workspace_id,
            ProviderConnectionORM.provider_type.in_(provider_types),
            ProviderConnectionORM.is_active.is_(True),
            ProviderConnectionORM.status == "connected",
        )
        .order_by(ProviderConnectionORM.updated_at.desc())
    )
