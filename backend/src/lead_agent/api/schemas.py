from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from lead_agent.domain.analysis import LeadContextAnalysis


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


class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
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


class LeadListResponse(BaseModel):
    items: list[LeadResponse]
    total: int


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
    scopes: list[str]
    credential_state: str
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
    connection: ProviderConnectionResponse | None


def provider_connection_response(row: Any) -> ProviderConnectionResponse:
    return ProviderConnectionResponse(
        id=row.id,
        workspace_id=row.workspace_id,
        provider_type=row.provider_type,
        external_account_id=row.external_account_id,
        account_name=row.account_name,
        auth_type=row.auth_type,
        status=row.status,
        scopes=row.scopes,
        credential_state=row.credential_state,
        last_validation_at=row.last_validation_at,
        last_validation_result=row.last_validation_result,
        metadata={
            key: value for key, value in row.connection_metadata.items() if "oauth_state" not in key
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


def lead_response(row: Any, routing: Any = None, crm_sync: Any = None) -> LeadResponse:
    return LeadResponse(
        id=row.id,
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
    )
