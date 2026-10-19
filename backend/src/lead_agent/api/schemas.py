from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from lead_agent.domain.analysis import LeadContextAnalysis
from lead_agent.infrastructure.reasoning.configuration import REASONING_PROVIDERS


class LeadCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = "website"
    source_detail: str | None = None
    event_id: str | None = None
    external_id: str | None = None
    full_name: str
    email: str
    phone: str | None = None
    job_title: str | None = None
    company_name: str
    industry: str | None = None
    employee_count: int | None = Field(default=None, ge=0)
    annual_revenue: float | None = Field(default=None, ge=0)
    geography: str | None = None
    budget: float | None = Field(default=None, ge=0)
    product_or_service: str
    message: str
    received_at: datetime | None = None


class EmailIntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str | None = None
    external_message_id: str | None = None
    sender: str
    sender_name: str | None = None
    subject: str
    body: str
    timestamp: datetime | None = None
    received_at: datetime | None = None
    provider: str = "fixture"
    company: str | None = None
    job_title: str | None = None
    geography: str | None = None
    phone: str | None = None
    industry: str | None = None
    budget: float | None = Field(default=None, ge=0)


class LinkedInIntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str | None = None
    lead_id: str | None = None
    message_id: str | None = None
    sender_name: str
    email: str | None = None
    message: str
    company_name: str | None = None
    job_title: str | None = None
    product_or_service: str | None = None
    source_detail: str = "official-fixture"
    timestamp: datetime | None = None


class ClutchIntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inquiry_id: str | None = None
    external_message_id: str | None = None
    sender: str
    sender_name: str | None = None
    subject: str
    body: str
    timestamp: datetime | None = None
    company: str | None = None


class ScoreBreakdownResponse(BaseModel):
    component: str
    key: str
    points: int
    max_points: int
    reason: str
    evidence: list[str]


class QualificationResponse(BaseModel):
    id: UUID | None = None
    total_score: int
    classification: str
    routing_queue: str
    routing_action: str
    hard_disqualified: bool
    disqualifier_code: str | None
    disqualifier_evidence: list[str]
    rationale: list[str]
    score_breakdown: list[ScoreBreakdownResponse]
    component_scores: dict[str, int]
    policy_version: str
    icp_version_id: UUID | None
    icp_source: str
    icp_profile_version: str
    icp_configuration_hash: str
    confidence: float
    created_at: datetime | None = None


class ActionPlanResponse(BaseModel):
    immediate_action: str
    response_priority: str
    talking_points: list[str]
    unanswered_questions: list[str]
    known_pain_point: str
    urgency_timeline: str
    potential_objections: list[str]
    next_step_recommendation: str
    supporting_evidence: list[str]
    follow_up_strategy: list[str] = Field(default_factory=list)
    conversion_goal: str = ""


class NotificationResponse(BaseModel):
    id: UUID
    provider: str
    status: str
    attempts: int
    external_message_id: str | None
    error_message: str | None
    created_at: datetime
    sent_at: datetime | None
    updated_at: datetime


class RoutingDecisionResponse(BaseModel):
    id: UUID
    lead_id: UUID
    qualification_id: UUID
    qualification_band: str
    assigned_queue: str
    assigned_rep_id: str | None
    assigned_rep_name: str | None
    routing_reason: str
    matched_rules: list[str]
    action_plan: ActionPlanResponse
    priority: str
    sla_target_at: datetime | None
    notification_state: str
    manual_review: bool
    routing_timestamp: datetime
    notifications: list[NotificationResponse] = Field(default_factory=list)


class PipelineEventResponse(BaseModel):
    id: UUID
    stage: str
    status: str
    details: dict[str, Any]
    created_at: datetime


class CrmSyncResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    lead_id: UUID
    routing_decision_id: UUID
    provider_connection_id: UUID | None
    provider: str
    requested_by: str
    request_origin: str
    operation: str
    status: str
    attempts: int
    attempt_history: list[dict[str, Any]]
    provider_entity_ids: dict[str, str | None]
    payload_version: str
    payload_hash: str
    error_category: str | None
    error_code: str | None
    last_error: str | None
    retryable: bool
    requested_at: datetime
    completed_at: datetime | None
    updated_at: datetime
    is_current: bool


class AnalysisResponse(LeadContextAnalysis):
    id: UUID
    created_at: datetime


class ConfirmationEmailResponse(BaseModel):
    status: str
    recipient: str
    attempts: int
    external_message_id: str | None = None
    error_message: str | None = None
    sent_at: datetime | None = None


class LeadOutcomeResponse(BaseModel):
    pipeline_stage: str
    conversion_type: str | None
    send_direct_message: bool = False
    credited_user_id: UUID | None
    credited_name: str | None
    closed_revenue: float | None
    currency: str
    closed_at: datetime | None
    notifications: list[dict[str, Any]]


class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lead_number: int
    workspace_id: UUID
    source: str
    source_detail: str | None
    external_id: str | None
    full_name: str
    email: str
    phone: str | None
    job_title: str | None
    company_name: str
    industry: str | None
    employee_count: int | None
    annual_revenue: float | None
    geography: str | None
    budget: float | None
    product_or_service: str
    message: str
    received_at: datetime
    normalization_version: str
    created_at: datetime
    qualification: QualificationResponse | None
    analysis: AnalysisResponse | None
    routing: RoutingDecisionResponse | None = None
    crm_sync: CrmSyncResponse | None = None
    confirmation_email: ConfirmationEmailResponse | None = None
    outcome: LeadOutcomeResponse | None = None


class LeadListResponse(BaseModel):
    items: list[LeadResponse]
    total: int
    limit: int = 50
    offset: int = 0
    has_more: bool = False


class HealthResponse(BaseModel):
    status: str
    version: str
    components: dict[str, str] | None = None


class ProviderConnectionResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    provider_type: str
    external_account_id: str | None
    account_name: str
    auth_type: str
    status: str
    verification_status: str
    scopes: list[str]
    credential_state: str
    configured: bool
    selected_model: str | None = None
    last_validation_at: datetime | None
    last_validation_result: dict[str, Any]
    metadata: dict[str, Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class IntegrationCardResponse(BaseModel):
    provider: str
    display_name: str
    category: str
    availability: str
    preferred_auth: str
    supported_auth: list[str]
    dashboard_path: str
    credential_help: str
    required_scopes: list[str]
    permission_reason: str
    documentation_url: str
    manual_fields: list[str]
    connection_mode: str
    supported_models: list[str] = Field(default_factory=list)
    default_model: str | None = None
    directionality: dict[str, Any]
    connection: ProviderConnectionResponse | None
    connections: list[ProviderConnectionResponse] = Field(default_factory=list)


class CrmInboundSyncStateResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    connection_id: UUID
    provider: str
    object_type: str
    enabled: bool
    initial_import_completed: bool
    sync_interval_seconds: int
    next_sync_at: datetime | None
    status: str
    last_successful_cursor: dict[str, Any] | None
    resume_cursor: dict[str, Any] | None
    last_successful_sync_at: datetime | None
    records_processed: int
    error_category: str | None
    error_code: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class CrmInboundSyncRunResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    connection_id: UUID
    state_id: UUID
    provider: str
    object_type: str
    mode: str
    status: str
    requested_by: str
    correlation_id: str
    records_fetched: int
    records_created: int
    records_updated: int
    records_unchanged: int
    records_skipped: int
    records_failed: int
    records_processed: int
    records_succeeded: int
    error_category: str | None
    error_code: str | None
    last_error: str | None
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CrmInboundSyncAcceptedResponse(BaseModel):
    status: Literal["queued"] = "queued"
    queued_at: datetime
    message: str


class CrmInboundSyncCancellationResponse(BaseModel):
    status: Literal["cancellation_requested", "not_running"]
    message: str


def crm_inbound_state_response(row: Any) -> CrmInboundSyncStateResponse:
    return CrmInboundSyncStateResponse(
        id=row.id,
        workspace_id=row.workspace_id,
        connection_id=row.connection_id,
        provider=row.provider,
        object_type=row.object_type,
        enabled=row.enabled,
        initial_import_completed=row.initial_import_completed,
        sync_interval_seconds=row.sync_interval_seconds,
        next_sync_at=row.next_sync_at,
        status=row.status,
        last_successful_cursor=row.last_successful_cursor,
        resume_cursor=row.resume_cursor,
        last_successful_sync_at=row.last_successful_sync_at,
        records_processed=row.records_processed,
        error_category=row.error_category,
        error_code=row.error_code,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def crm_inbound_run_response(row: Any) -> CrmInboundSyncRunResponse:
    succeeded = row.records_created + row.records_updated + row.records_unchanged
    return CrmInboundSyncRunResponse(
        id=row.id,
        workspace_id=row.workspace_id,
        connection_id=row.connection_id,
        state_id=row.state_id,
        provider=row.provider,
        object_type=row.object_type,
        mode=row.mode,
        status=row.status,
        requested_by=row.requested_by,
        correlation_id=row.correlation_id,
        records_fetched=row.records_fetched,
        records_created=row.records_created,
        records_updated=row.records_updated,
        records_unchanged=row.records_unchanged,
        records_skipped=row.records_skipped,
        records_failed=row.records_failed,
        records_processed=succeeded + row.records_skipped + row.records_failed,
        records_succeeded=succeeded,
        error_category=row.error_category,
        error_code=row.error_code,
        last_error=row.last_error,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def provider_connection_response(row: Any) -> ProviderConnectionResponse:
    forbidden_keys = {
        "api_key",
        "access_token",
        "refresh_token",
        "client_secret",
        "authorization",
        "ciphertext",
        "secret_reference",
    }

    def public_mapping(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: public_mapping(item)
                for key, item in value.items()
                if str(key).lower() not in forbidden_keys
            }
        if isinstance(value, list):
            return [public_mapping(item) for item in value]
        return value

    validation_status = row.last_validation_result.get("status")
    verification_status = (
        "verified"
        if row.status == "verified"
        else "reauthorization_required"
        if row.status in {"expired", "needs_reauthorization"}
        else "failed"
        if row.status in {"error", "connection_failed"}
        else "not_verified"
    )
    if validation_status == "connected" and row.last_validation_result.get("verified"):
        verification_status = "verified"
    return ProviderConnectionResponse(
        id=row.id,
        workspace_id=row.workspace_id,
        provider_type=row.provider_type,
        external_account_id=row.external_account_id,
        account_name=row.account_name,
        auth_type=row.auth_type,
        status=row.status,
        verification_status=verification_status,
        scopes=row.scopes,
        credential_state=row.credential_state,
        configured=row.credential_state == "configured" and row.secret_reference is not None,
        selected_model=(
            str(row.connection_metadata["selected_model"])
            if row.provider_type in REASONING_PROVIDERS
            and isinstance(row.connection_metadata.get("selected_model"), str)
            else None
        ),
        last_validation_at=row.last_validation_at,
        last_validation_result=public_mapping(row.last_validation_result),
        metadata={
            key: public_mapping(value)
            for key, value in row.connection_metadata.items()
            if "oauth_state" not in key and key.lower() not in forbidden_keys
        },
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def qualification_response(row: Any) -> QualificationResponse | None:
    if row is None:
        return None
    return QualificationResponse(
        id=row.id,
        total_score=row.total_score,
        classification=row.classification,
        routing_queue=row.routing_queue,
        routing_action=row.routing_action,
        hard_disqualified=row.hard_disqualified,
        disqualifier_code=row.disqualifier_code,
        disqualifier_evidence=row.disqualifier_evidence,
        rationale=row.rationale,
        score_breakdown=row.score_breakdown,
        component_scores=row.component_scores,
        policy_version=row.policy_version,
        icp_version_id=row.icp_version_id,
        icp_source=row.icp_source,
        icp_profile_version=row.icp_profile_version,
        icp_configuration_hash=row.icp_configuration_hash,
        confidence=float(row.confidence),
        created_at=row.created_at,
    )


def analysis_response(row: Any) -> AnalysisResponse | None:
    if row is None:
        return None
    analysis = LeadContextAnalysis.model_validate(row.structured_output)
    return AnalysisResponse(
        **analysis.model_dump(),
        id=row.id,
        created_at=row.created_at,
    )


def notification_response(row: Any) -> NotificationResponse:
    return NotificationResponse(
        id=row.id,
        provider=row.provider,
        status=row.status,
        attempts=row.attempts,
        external_message_id=row.external_message_id,
        error_message=row.error_message,
        created_at=row.created_at,
        sent_at=row.sent_at,
        updated_at=row.updated_at,
    )


def routing_response(row: Any) -> RoutingDecisionResponse | None:
    if row is None:
        return None
    return RoutingDecisionResponse(
        id=row.id,
        lead_id=row.lead_id,
        qualification_id=row.qualification_id,
        qualification_band=row.qualification_band,
        assigned_queue=row.assigned_queue,
        assigned_rep_id=row.assigned_rep_id,
        assigned_rep_name=row.assigned_rep_name,
        routing_reason=row.routing_reason,
        matched_rules=row.matched_rules,
        action_plan=ActionPlanResponse.model_validate(row.action_plan),
        priority=row.priority,
        sla_target_at=row.sla_target_at,
        notification_state=row.notification_state,
        manual_review=row.manual_review,
        routing_timestamp=row.created_at,
        notifications=[notification_response(item) for item in row.notifications],
    )


def crm_sync_response(row: Any) -> CrmSyncResponse | None:
    if row is None:
        return None
    return CrmSyncResponse(
        id=row.id,
        workspace_id=row.workspace_id,
        lead_id=row.lead_id,
        routing_decision_id=row.routing_decision_id,
        provider_connection_id=row.provider_connection_id,
        provider=row.provider,
        requested_by=row.requested_by,
        request_origin=row.request_origin,
        operation=row.operation,
        status=row.status,
        attempts=row.attempts,
        attempt_history=row.attempt_history,
        provider_entity_ids=row.provider_entity_ids,
        payload_version=row.payload_version,
        payload_hash=row.payload_hash,
        error_category=row.error_category,
        error_code=row.error_code,
        last_error=row.last_error,
        retryable=row.retryable,
        requested_at=row.requested_at,
        completed_at=row.completed_at,
        updated_at=row.updated_at,
        is_current=row.is_current,
    )


def confirmation_email_response(row: Any) -> ConfirmationEmailResponse | None:
    if row is None:
        return None
    return ConfirmationEmailResponse(
        status=row.status,
        recipient=row.recipient,
        attempts=row.attempts,
        external_message_id=row.external_message_id,
        error_message=row.error_message,
        sent_at=row.sent_at,
    )


def lead_response(
    row: Any, routing: Any = None, crm_sync: Any = None, confirmation_email: Any = None
) -> LeadResponse:
    return LeadResponse(
        id=row.id,
        lead_number=row.lead_number,
        workspace_id=row.workspace_id,
        source=row.source,
        source_detail=row.source_detail,
        external_id=row.external_id,
        full_name=row.full_name,
        email=row.email,
        phone=row.phone,
        job_title=row.job_title,
        company_name=row.company_name,
        industry=row.industry,
        employee_count=row.employee_count,
        annual_revenue=float(row.annual_revenue) if row.annual_revenue is not None else None,
        geography=row.geography,
        budget=float(row.budget) if row.budget is not None else None,
        product_or_service=row.product_or_service,
        message=row.message,
        received_at=row.received_at,
        normalization_version=row.normalization_version,
        created_at=row.created_at,
        qualification=qualification_response(row.qualification),
        analysis=analysis_response(row.analysis),
        routing=routing_response(routing),
        crm_sync=crm_sync_response(crm_sync),
        confirmation_email=confirmation_email_response(confirmation_email),
        outcome=outcome_response(row.outcome),
    )


def outcome_response(row: Any) -> LeadOutcomeResponse | None:
    if row is None:
        return None
    return LeadOutcomeResponse(
        pipeline_stage=row.pipeline_stage, conversion_type=row.conversion_type,
        send_direct_message=row.send_direct_message,
        credited_user_id=row.credited_user_id,
        credited_name=row.credited_user.display_name if row.credited_user else None,
        closed_revenue=float(row.closed_revenue) if row.closed_revenue is not None else None,
        currency=row.currency, closed_at=row.closed_at,
        notifications=[
            {"destination": item.destination, "status": item.status,
             "error_message": item.error_message, "attempts": item.attempts}
            for item in sorted(row.win_notifications, key=lambda item: item.destination)
        ],
    )
