from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from lead_agent.domain.configuration import IcpConfig
from lead_agent.domain.models import NormalizedLead

InformationKind = Literal["explicit", "inferred", "unknown"]
FitStatus = Literal["match", "partial", "no_match", "unknown", "disqualified"]
IntentLevel = Literal["high", "medium", "low", "unknown"]
AnalysisStatus = Literal[
    "completed", "manual_review", "skipped_hard_disqualifier", "provider_error"
]


class EvidenceReference(BaseModel):
    """A concise, business-facing reference to supplied lead data."""

    model_config = ConfigDict(extra="forbid")

    source_field: str
    excerpt: str = Field(max_length=500)


class AnalyzedField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str | None = None
    information_kind: InformationKind = "unknown"
    confidence: float = Field(default=0.0, ge=0, le=1)
    evidence: list[EvidenceReference] = Field(default_factory=list)


class ContextFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_product_service: AnalyzedField
    pain_point: AnalyzedField
    urgency: AnalyzedField
    explicit_timeline: AnalyzedField
    budget_information: AnalyzedField
    company_size_or_spend: AnalyzedField
    person_role: AnalyzedField
    decision_maker_status: AnalyzedField
    buying_stage: AnalyzedField
    competitor_or_current_tools: AnalyzedField
    technical_requirements: AnalyzedField
    previous_engagement: AnalyzedField
    source_channel_intent: AnalyzedField


class IcpCriterionEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: FitStatus
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(max_length=500)
    evidence: list[EvidenceReference] = Field(default_factory=list)


class IcpEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_name: str
    profile_version: str
    industry_fit: IcpCriterionEvaluation
    company_size_fit: IcpCriterionEvaluation
    geography_fit: IcpCriterionEvaluation
    buyer_role_fit: IcpCriterionEvaluation
    budget_fit: IcpCriterionEvaluation
    pain_point_relevance: IcpCriterionEvaluation
    explicit_disqualifiers: list[str] = Field(default_factory=list)


class SalesSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    who: str
    company_context: str
    need: str
    pain_point: str
    urgency: str
    buying_signal: str
    potential_concern: str
    qualification_result: str = "Pending deterministic qualification"


class ReasoningOutput(BaseModel):
    """Strict provider output. It deliberately contains no arbitrary numeric score."""

    model_config = ConfigDict(extra="forbid")

    context: ContextFields
    intent_level: IntentLevel
    overall_confidence: float = Field(ge=0, le=1)
    unknown_fields: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    icp_evaluation: IcpEvaluation
    summary: SalesSummary
    recommended_action: str = Field(max_length=500)


class LeadContextAnalysis(ReasoningOutput):
    """Durable application record wrapping one validated provider output."""

    status: AnalysisStatus = "completed"
    provider: str
    model: str
    prompt_version: str
    requires_manual_review: bool = False
    error_code: str | None = None
    error_message: str | None = None


class ReasoningProvider(Protocol):
    provider_name: str
    model_name: str
    prompt_version: str

    def analyze(self, lead: NormalizedLead, icp: IcpConfig) -> ReasoningOutput:
        """Extract grounded context and ICP signals without selecting a final score."""


class ReasoningProviderError(RuntimeError):
    code = "provider_error"


class ReasoningProviderUnavailable(ReasoningProviderError):
    code = "provider_unavailable"


class ReasoningProviderTimeout(ReasoningProviderError):
    code = "provider_timeout"


class InvalidReasoningOutput(ReasoningProviderError):
    code = "invalid_provider_output"
