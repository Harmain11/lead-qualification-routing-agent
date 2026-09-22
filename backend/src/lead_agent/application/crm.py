from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.application.routing import record_pipeline_event
from lead_agent.domain.analysis import LeadContextAnalysis
from lead_agent.domain.crm import (
    CanonicalCrmPayload,
    CrmEntityReferences,
    CrmProvider,
    CrmProviderError,
)
from lead_agent.infrastructure.crm.mock import MockCrmProvider
from lead_agent.infrastructure.db.models import CrmSyncORM, LeadORM, RoutingDecisionORM
from lead_agent.infrastructure.observability import get_observability, provider_operation
from lead_agent.infrastructure.repositories import get_current_crm_sync, get_current_routing

_LOGGER = logging.getLogger("lead_agent.crm")


def _analysis(lead: LeadORM) -> LeadContextAnalysis | None:
    if lead.analysis is None:
        return None
    return LeadContextAnalysis.model_validate(lead.analysis.structured_output)


def _split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split(maxsplit=1)
    if len(parts) == 1:
        return "", parts[0]
    return parts[0], parts[1]


def build_crm_payload(
    lead: LeadORM,
    routing: RoutingDecisionORM,
    *,
    payload_version: str = "1.0",
) -> CanonicalCrmPayload:
    if lead.qualification is None:
        raise ValueError("lead has no qualification result")
    analysis = _analysis(lead)
    first_name, last_name = _split_name(lead.full_name)
    component_scores = lead.qualification.component_scores
    summary = ""
    urgency = "Unknown"
    if analysis is not None:
        summary_parts = [
            analysis.summary.who,
            analysis.summary.company_context,
            analysis.summary.need,
            analysis.summary.pain_point,
        ]
        summary = " | ".join(part for part in summary_parts if part and part != "Unknown")
        urgency = (
            analysis.context.explicit_timeline.value or analysis.context.urgency.value or "Unknown"
        )
    action_plan = routing.action_plan
    return CanonicalCrmPayload(
        payload_version=payload_version,
        internal_lead_id=lead.id,
        external_source_id=lead.external_id,
        full_name=lead.full_name,
        first_name=first_name,
        last_name=last_name,
        email=lead.email,
        company=lead.company_name,
        company_key=hashlib.sha256(lead.company_name.strip().lower().encode()).hexdigest()[:32],
        title=lead.job_title,
        phone=lead.phone,
        source_channel=lead.source,
        source_detail=lead.source_detail,
        requested_service=lead.product_or_service,
        summary=summary,
        qualification_score=lead.qualification.total_score,
        qualification_band=lead.qualification.classification,
        icp_score=component_scores.get("icp_fit", 0),
        intent_urgency_score=component_scores.get("intent_urgency", 0),
        engagement_score=component_scores.get("engagement_quality", 0),
        rationale=list(lead.qualification.rationale),
        recommended_action=str(
            action_plan.get("immediate_action", "Review the persisted action plan.")
        ),
        action_plan=action_plan,
        assigned_queue=routing.assigned_queue,
        assigned_rep_id=routing.assigned_rep_id,
        assigned_rep_name=routing.assigned_rep_name,
        urgency_timeline=urgency,
        lifecycle_stage=lead.qualification.classification,
    )


def _payload_hash(payload: CanonicalCrmPayload) -> str:
    serialized = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _existing_entities(
    session: Session,
    lead_id: UUID,
    provider: str,
    provider_connection_id: UUID | None = None,
) -> CrmEntityReferences:
    lead = session.get(LeadORM, lead_id)
    if lead is None:
        return CrmEntityReferences()
    statement = select(CrmSyncORM).where(
        CrmSyncORM.lead_id == lead_id,
        CrmSyncORM.workspace_id == lead.workspace_id,
        CrmSyncORM.provider == provider,
    )
    if provider_connection_id is not None:
        statement = statement.where(CrmSyncORM.provider_connection_id == provider_connection_id)
    prior = session.scalar(statement.order_by(CrmSyncORM.requested_at.desc()))
    return CrmEntityReferences.model_validate(prior.provider_entity_ids if prior else {})


def sync_lead_to_crm(
    session: Session,
    lead: LeadORM,
    provider: CrmProvider | None = None,
    *,
    payload_version: str = "1.0",
    max_attempts: int = 3,
    force: bool = False,
) -> CrmSyncORM:
    active_provider = provider or MockCrmProvider()
    provider_connection_id = getattr(active_provider, "connection_id", None)
    routing = get_current_routing(session, lead.id, lead.workspace_id)
    if routing is None:
        raise ValueError("lead has no routing decision")
    payload = build_crm_payload(lead, routing, payload_version=payload_version)
    payload_hash = _payload_hash(payload)
    current = get_current_crm_sync(
        session,
        lead.id,
        active_provider.provider_name,
        workspace_id=lead.workspace_id,
        provider_connection_id=provider_connection_id,
    )
    if current is not None and current.payload_hash == payload_hash:
        if current.status == "synced" or (not force and current.status in {"pending", "syncing"}):
            return current
        if not force and current.status == "failed" and not current.retryable:
            return current
        sync_row = current
    else:
        if current is not None:
            current.is_current = False
        now = datetime.now(UTC)
        sync_row = CrmSyncORM(
            id=uuid4(),
            workspace_id=lead.workspace_id,
            lead_id=lead.id,
            routing_decision_id=routing.id,
            provider_connection_id=provider_connection_id,
            provider=active_provider.provider_name,
            operation="upsert",
            status="pending",
            attempts=0,
            attempt_history=[],
            provider_entity_ids=_existing_entities(
                session,
                lead.id,
                active_provider.provider_name,
                provider_connection_id,
            ).model_dump(mode="json"),
            payload_version=payload.payload_version,
            payload_hash=payload_hash,
            payload=payload.model_dump(mode="json"),
            idempotency_key=(
                f"{lead.workspace_id}:{lead.id}:{provider_connection_id or 'environment'}:"
                f"{active_provider.provider_name}:{payload_hash}"
            ),
            error_category=None,
            error_code=None,
            last_error=None,
            retryable=False,
            requested_at=now,
            completed_at=None,
            updated_at=now,
            is_current=True,
        )
        session.add(sync_row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            concurrent = session.scalar(
                select(CrmSyncORM).where(CrmSyncORM.idempotency_key == sync_row.idempotency_key)
            )
            if concurrent is None:
                raise
            if concurrent.status in {"pending", "syncing", "synced"}:
                return concurrent
            sync_row = concurrent

    existing = CrmEntityReferences.model_validate(sync_row.provider_entity_ids)
    attempts_this_run = 0
    while attempts_this_run < max(1, max_attempts):
        attempts_this_run += 1
        sync_row.status = "syncing"
        sync_row.attempts += 1
        sync_row.updated_at = datetime.now(UTC)
        session.commit()
        try:
            with provider_operation(
                active_provider.provider_name,
                str(provider_connection_id) if provider_connection_id else None,
            ) as operation_context:
                _LOGGER.info(
                    "provider.operation.started",
                    extra={"fields": {**operation_context, "operation": "crm_sync"}},
                )
                result = active_provider.sync(payload, existing)
                _LOGGER.info(
                    "provider.operation.completed",
                    extra={
                        "fields": {
                            **operation_context,
                            "operation": "crm_sync",
                            "status": "success",
                        }
                    },
                )
        except CrmProviderError as error:
            get_observability().provider_failure(
                active_provider.provider_name,
                error.category,
                connection_id=str(provider_connection_id) if provider_connection_id else None,
                operation="crm_sync",
                error_code=error.code,
            )
            sync_row.error_category = error.category
            sync_row.error_code = error.code
            sync_row.last_error = str(error)[:2000]
            sync_row.retryable = error.retryable
            sync_row.status = "retryable" if error.retryable else "failed"
            sync_row.updated_at = datetime.now(UTC)
            sync_row.attempt_history = [
                *sync_row.attempt_history,
                {
                    "attempt": sync_row.attempts,
                    "status": sync_row.status,
                    "error_category": error.category,
                    "error_code": error.code,
                    "error": str(error)[:2000],
                    "completed_at": sync_row.updated_at.isoformat(),
                },
            ]
            session.commit()
            if error.retryable and attempts_this_run < max(1, max_attempts):
                continue
            if error.retryable:
                get_observability().retry_exhausted(
                    "crm_sync",
                    provider=active_provider.provider_name,
                    connection_id=str(provider_connection_id) if provider_connection_id else None,
                    attempts=sync_row.attempts,
                    error_category=error.category,
                )
            record_pipeline_event(
                session,
                lead.id,
                "crm",
                sync_row.status,
                {"provider": sync_row.provider, "crm_sync_id": str(sync_row.id)},
            )
            session.commit()
            return sync_row
        except Exception as error:
            get_observability().provider_failure(
                active_provider.provider_name,
                "transient",
                connection_id=str(provider_connection_id) if provider_connection_id else None,
                operation="crm_sync",
                error_type=type(error).__name__,
            )
            sync_row.error_category = "transient"
            sync_row.error_code = "unexpected_provider_error"
            sync_row.last_error = str(error)[:2000] or error.__class__.__name__
            sync_row.retryable = True
            sync_row.status = "retryable"
            sync_row.updated_at = datetime.now(UTC)
            sync_row.attempt_history = [
                *sync_row.attempt_history,
                {
                    "attempt": sync_row.attempts,
                    "status": "retryable",
                    "error_category": "transient",
                    "error_code": "unexpected_provider_error",
                    "error": sync_row.last_error,
                    "completed_at": sync_row.updated_at.isoformat(),
                },
            ]
            session.commit()
            if attempts_this_run < max(1, max_attempts):
                continue
            get_observability().retry_exhausted(
                "crm_sync",
                provider=active_provider.provider_name,
                connection_id=str(provider_connection_id) if provider_connection_id else None,
                attempts=sync_row.attempts,
                error_category="transient",
            )
            record_pipeline_event(
                session,
                lead.id,
                "crm",
                "retryable",
                {"provider": sync_row.provider, "crm_sync_id": str(sync_row.id)},
            )
            session.commit()
            return sync_row
        sync_row.operation = result.operation
        sync_row.provider_entity_ids = result.entities.model_dump(mode="json")
        sync_row.status = "synced"
        sync_row.error_category = None
        sync_row.error_code = None
        sync_row.last_error = None
        sync_row.retryable = False
        sync_row.completed_at = datetime.now(UTC)
        sync_row.updated_at = sync_row.completed_at
        sync_row.attempt_history = [
            *sync_row.attempt_history,
            {
                "attempt": sync_row.attempts,
                "status": "synced",
                "operation": result.operation,
                "completed_at": sync_row.completed_at.isoformat(),
            },
        ]
        session.commit()
        record_pipeline_event(
            session,
            lead.id,
            "crm",
            "synced",
            {
                "provider": sync_row.provider,
                "crm_sync_id": str(sync_row.id),
                "operation": sync_row.operation,
            },
        )
        session.commit()
        return sync_row
    raise RuntimeError("CRM synchronization loop ended unexpectedly")


def retry_crm_sync(
    session: Session,
    lead: LeadORM,
    provider: CrmProvider,
    *,
    max_attempts: int = 3,
) -> CrmSyncORM:
    current = get_current_crm_sync(
        session,
        lead.id,
        provider.provider_name,
        workspace_id=lead.workspace_id,
        provider_connection_id=getattr(provider, "connection_id", None),
    )
    if current is None:
        return sync_lead_to_crm(session, lead, provider, max_attempts=max_attempts)
    if current.status == "synced":
        return current
    if not current.retryable and current.status == "failed":
        raise ValueError("CRM synchronization failed permanently and cannot be retried")
    return sync_lead_to_crm(
        session,
        lead,
        provider,
        max_attempts=max_attempts,
        force=True,
    )
