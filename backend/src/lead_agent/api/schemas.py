from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LeadCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = "website"
    source_detail: str | None = None
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
    rationale: list[str]
    score_breakdown: list[ScoreBreakdownResponse]
    component_scores: dict[str, int]
    policy_version: str
    confidence: float
    created_at: datetime | None = None


class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
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


class LeadListResponse(BaseModel):
    items: list[LeadResponse]
    total: int


class HealthResponse(BaseModel):
    status: str
    version: str


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
        rationale=row.rationale,
        score_breakdown=row.score_breakdown,
        component_scores=row.component_scores,
        policy_version=row.policy_version,
        confidence=float(row.confidence),
        created_at=row.created_at,
    )


def lead_response(row: Any) -> LeadResponse:
    return LeadResponse(
        id=row.id,
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
    )
