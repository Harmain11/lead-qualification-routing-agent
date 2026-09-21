from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CompanySizeConfig(BaseModel):
    employees_min: int = 0
    employees_max: int = 0
    revenue_currency: str = "USD"
    annual_revenue_min: float = 0
    annual_revenue_max: float = 0


class BudgetConfig(BaseModel):
    currency: str = "USD"
    minimum: float = 0
    typical: float = 0


class DisqualifierConfig(BaseModel):
    code: str
    description: str
    severity: str = "hard"
    keywords: list[str] = Field(default_factory=list)


class ScoringConfig(BaseModel):
    weights: dict[str, int] = Field(
        default_factory=lambda: {
            "icp_fit": 40,
            "intent_urgency": 35,
            "engagement_quality": 25,
        }
    )
    component_points: dict[str, dict[str, int]] = Field(default_factory=dict)
    signals: dict[str, list[str]] = Field(default_factory=dict)
    message_detail_chars: int = 240

    @model_validator(mode="after")
    def weights_must_total_one_hundred(self) -> ScoringConfig:
        if sum(self.weights.values()) != 100:
            raise ValueError("scoring weights must total 100")
        for component, weight in self.weights.items():
            configured = sum(self.component_points.get(component, {}).values())
            if self.component_points and configured != weight:
                raise ValueError(f"{component} point allocations must total {weight}")
        return self


class IcpConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    profile_name: str
    profile_version: str
    target_industries: list[str] = Field(default_factory=list)
    target_geographies: list[str] = Field(default_factory=list)
    company_size: CompanySizeConfig = Field(default_factory=CompanySizeConfig)
    buyer_roles: dict[str, list[str]] = Field(default_factory=dict)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    pain_points: list[str] = Field(default_factory=list)
    disqualifiers: list[DisqualifierConfig] = Field(default_factory=list)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)


class ScoreBand(BaseModel):
    key: str
    minimum: int = Field(ge=0, le=100)
    maximum: int = Field(ge=0, le=100)
    queue: str
    action: str

    @model_validator(mode="after")
    def range_is_valid(self) -> ScoreBand:
        if self.minimum > self.maximum:
            raise ValueError("score band minimum must not exceed maximum")
        return self


class ManualReviewConfig(BaseModel):
    enabled: bool = True
    minimum_confidence: float = Field(default=0.75, ge=0, le=1)
    retry_limit_before_review: int = Field(default=3, ge=0)


class QueueConfig(BaseModel):
    key: str
    display_name: str
    description: str = ""


class RepConfig(BaseModel):
    id: str
    name: str
    active: bool = True
    queue: str
    geographies: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    priority: int = Field(default=100, ge=0)


class RoutingRuleConfig(BaseModel):
    name: str
    precedence: int = Field(default=100, ge=0)
    qualification_bands: list[str] = Field(default_factory=list)
    geographies: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    buyer_roles: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    urgency: list[str] = Field(default_factory=list)
    technical_specializations: list[str] = Field(default_factory=list)
    company_size_min: int | None = Field(default=None, ge=0)
    company_size_max: int | None = Field(default=None, ge=0)
    queue: str
    priority: str = "normal"
    sla_hours: int | None = Field(default=None, ge=0)
    action_key: str | None = None
    reason: str


class ActionConfig(BaseModel):
    key: str
    immediate_action: str
    priority: str
    sla_hours: int | None = Field(default=None, ge=0)
    talking_points: list[str] = Field(default_factory=list)
    next_step: str


class RoutingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    policy_version: str
    score_bands: list[ScoreBand]
    manual_review: ManualReviewConfig = Field(default_factory=ManualReviewConfig)
    queues: list[QueueConfig] = Field(default_factory=list)
    reps: list[RepConfig] = Field(default_factory=list)
    rules: list[RoutingRuleConfig] = Field(default_factory=list)
    actions: dict[str, ActionConfig] = Field(default_factory=dict)
    manual_review_queue: str = "manual_review"
    safe_default_queue: str = "manual_review"

    @model_validator(mode="after")
    def bands_cover_score_range(self) -> RoutingConfig:
        ordered = sorted(self.score_bands, key=lambda band: band.minimum)
        if not ordered or ordered[0].minimum != 0 or ordered[-1].maximum != 100:
            raise ValueError("score bands must cover 0 through 100")
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if previous.maximum + 1 != current.minimum:
                raise ValueError("score bands must be contiguous")
        return self

    def band_for(self, score: int) -> ScoreBand:
        for band in self.score_bands:
            if band.minimum <= score <= band.maximum:
                return band
        raise ValueError(f"no routing band configured for score {score}")


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"configuration root must be a mapping: {path}")
    return loaded


def load_icp(path: Path) -> IcpConfig:
    return IcpConfig.model_validate(_read_yaml(path))


def load_routing(path: Path) -> RoutingConfig:
    return RoutingConfig.model_validate(_read_yaml(path))
