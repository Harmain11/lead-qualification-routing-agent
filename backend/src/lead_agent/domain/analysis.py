from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from lead_agent.domain.configuration import IcpConfig, ManualReviewConfig
from lead_agent.domain.email import is_personal_email_address
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


_MISSING_PERSON_NAMES = {
    "unknown",
    "name not provided",
    "not provided",
    "n/a",
    "na",
    "none",
    "null",
}
_MISSING_COMPANY_NAMES = {
    "unknown",
    "unknown company",
    "unknown sender company",
    "company not provided",
    "not provided",
    "n/a",
    "na",
    "none",
    "null",
}


def manual_review_reasons(
    *,
    email: str | None,
    full_name: str | None,
    company_name: str | None,
    settings: ManualReviewConfig,
    overall_confidence: float | None = None,
    contradictions: list[str] | None = None,
    unknown_fields: list[str] | None = None,
    provider_failure: bool = False,
) -> list[str]:
    """Return only the configured reasons for holding a lead for human review."""

    if not settings.enabled:
        return []

    reasons: list[str] = []
    if settings.personal_email and is_personal_email_address(email or ""):
        reasons.append(
            f"Email field ({(email or '').strip() or 'blank'}) uses a personal email domain; "
            "verify the prospect's business affiliation."
        )

    normalized_name = " ".join((full_name or "").casefold().split())
    if settings.missing_person_name and (
        not normalized_name
        or "@" in normalized_name
        or normalized_name in _MISSING_PERSON_NAMES
    ):
        reasons.append(
            f"Full name field ({(full_name or '').strip() or 'blank'}) is missing or contains "
            "a placeholder value."
        )

    normalized_company = " ".join((company_name or "").casefold().split())
    if settings.missing_company_name and (
        not normalized_company or normalized_company in _MISSING_COMPANY_NAMES
    ):
        reasons.append(
            f"Company name field ({(company_name or '').strip() or 'blank'}) is missing or "
            "contains a placeholder value."
        )

    if (
        settings.low_confidence
        and overall_confidence is not None
        and overall_confidence < settings.minimum_confidence
    ):
        reasons.append(
            f"Analysis confidence ({overall_confidence:.0%}) is below the workspace threshold "
            f"({settings.minimum_confidence:.0%})."
        )
    if settings.contradictory_evidence and contradictions:
        reasons.append(
            "Contradictory evidence: "
            + "; ".join(item.strip() for item in contradictions if item.strip())
        )
    if settings.missing_qualification_information and unknown_fields:
        reasons.append(
            "Missing qualification fields: "
            + ", ".join(unknown_fields)
            + "."
        )
    if settings.provider_failure and provider_failure:
        reasons.append(
            "Analysis provider: the provider failed, so a fallback assessment was used."
        )
    return reasons


class InvalidReasoningOutput(ReasoningProviderError):
    code = "invalid_provider_output"
