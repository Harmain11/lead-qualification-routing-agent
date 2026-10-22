from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class NormalizedLead:
    """Canonical lead contract shared by every future intake channel."""

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
    annual_revenue: Decimal | None
    geography: str | None
    budget: Decimal | None
    product_or_service: str
    message: str
    received_at: datetime
    raw_payload: dict[str, Any] = field(default_factory=dict)
    normalization_version: str = "1.0"
    original_message: str | None = None
    recorded_evidence: tuple[RecordedLeadEvidence, ...] = ()


@dataclass(frozen=True)
class RecordedLeadEvidence:
    """A dated sales update or team note used as current qualification evidence."""

    source: str
    text: str
    observed_at: datetime | None = None


@dataclass(frozen=True)
class ScoreLine:
    component: str
    key: str
    points: int
    max_points: int
    reason: str
    evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"evidence": list(self.evidence)}


@dataclass(frozen=True)
class QualificationResult:
    total_score: int
    classification: str
    routing_queue: str
    routing_action: str
    hard_disqualified: bool
    disqualifier_code: str | None
    rationale: tuple[str, ...]
    score_breakdown: tuple[ScoreLine, ...]
    policy_version: str
    confidence: float = 1.0
    disqualifier_evidence: tuple[str, ...] = ()
    icp_version_id: UUID | None = None
    icp_source: str = "system_default"
    icp_profile_version: str = "legacy"
    icp_configuration_hash: str = "legacy"

    @property
    def component_scores(self) -> dict[str, int]:
        scores = {"intent_urgency": 0, "engagement_quality": 0}
        for line in self.score_breakdown:
            scores[line.component] = scores.get(line.component, 0) + line.points
        return scores

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_score": self.total_score,
            "classification": self.classification,
            "routing_queue": self.routing_queue,
            "routing_action": self.routing_action,
            "hard_disqualified": self.hard_disqualified,
            "disqualifier_code": self.disqualifier_code,
            "disqualifier_evidence": list(self.disqualifier_evidence),
            "rationale": list(self.rationale),
            "score_breakdown": [line.as_dict() for line in self.score_breakdown],
            "component_scores": self.component_scores,
            "policy_version": self.policy_version,
            "icp_version_id": str(self.icp_version_id) if self.icp_version_id else None,
            "icp_source": self.icp_source,
            "icp_profile_version": self.icp_profile_version,
            "icp_configuration_hash": self.icp_configuration_hash,
            "confidence": self.confidence,
        }
