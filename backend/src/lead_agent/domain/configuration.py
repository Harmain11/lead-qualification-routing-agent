from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CompanySizeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employees_min: int = 0
    employees_max: int = 0
    revenue_currency: str = "USD"
    annual_revenue_min: float = 0
    annual_revenue_max: float = 0

    @model_validator(mode="after")
    def ranges_are_valid(self) -> CompanySizeConfig:
        if self.employees_min < 0 or self.employees_max < 0:
            raise ValueError("employee ranges cannot be negative")
        if self.employees_max and self.employees_min > self.employees_max:
            raise ValueError("employees_min must not exceed employees_max")
        if self.annual_revenue_min < 0 or self.annual_revenue_max < 0:
            raise ValueError("annual revenue ranges cannot be negative")
        if self.annual_revenue_max and self.annual_revenue_min > self.annual_revenue_max:
            raise ValueError("annual_revenue_min must not exceed annual_revenue_max")
        return self


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = "USD"
    minimum: float = 0
    typical: float = 0

    @model_validator(mode="after")
    def range_is_valid(self) -> BudgetConfig:
        if self.minimum < 0 or self.typical < 0:
            raise ValueError("budget values cannot be negative")
        if self.typical and self.typical < self.minimum:
            raise ValueError("typical budget must not be lower than minimum budget")
        return self


class BuyerProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_titles: list[str] = Field(default_factory=list)
    seniorities: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    decision_maker_indicators: list[str] = Field(default_factory=list)
    excluded_personas: list[str] = Field(default_factory=list)


class CommercialFitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_interests: list[str] = Field(default_factory=list)
    purchasing_timelines: list[str] = Field(default_factory=list)


class TechnologyFitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    technology_stack: list[str] = Field(default_factory=list)
    platform_requirements: list[str] = Field(default_factory=list)


class FitSignalsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: list[str] = Field(default_factory=list)
    preferred: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)


class DisqualifierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    description: str
    severity: Literal["hard"] = "hard"
    field: Literal[
        "searchable",
        "industry",
        "geography",
        "job_title",
        "email",
        "company_name",
        "product_or_service",
    ] = "searchable"
    operator: Literal["contains_any", "matches_any"] = "contains_any"
    keywords: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def has_match_values(self) -> DisqualifierConfig:
        if not self.code.strip():
            raise ValueError("hard disqualifier code is required")
        if not self.description.strip():
            raise ValueError("hard disqualifier description is required")
        if not [item for item in [*self.keywords, *self.values] if item.strip()]:
            raise ValueError("hard disqualifier requires at least one keyword or value")
        return self


ICP_CRITERIA: frozenset[str] = frozenset(
    {
        "industry_match",
        "company_size_fit",
        "geography_match",
        "buyer_role_match",
        "decision_maker",
        "budget_fit",
        "revenue_fit",
        "business_type_match",
        "strategic_account",
        "technology_fit",
        "product_interest",
        "purchasing_timeline",
        "department_match",
        "seniority_match",
        "required_signals",
        "preferred_signals",
    }
)


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
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
        if self.weights != {
            "icp_fit": 40,
            "intent_urgency": 35,
            "engagement_quality": 25,
        }:
            raise ValueError("component weights must remain ICP 40, Intent 35, Engagement 25")
        if sum(self.weights.values()) != 100:
            raise ValueError("scoring weights must total 100")
        for component, weight in self.weights.items():
            configured = sum(self.component_points.get(component, {}).values())
            if self.component_points and configured != weight:
                raise ValueError(f"{component} point allocations must total {weight}")
        unknown = set(self.component_points.get("icp_fit", {})) - ICP_CRITERIA
        if unknown:
            raise ValueError(f"unsupported ICP scoring criteria: {', '.join(sorted(unknown))}")
        if any(
            points < 0 or points > 40
            for points in self.component_points.get("icp_fit", {}).values()
        ):
            raise ValueError("ICP criterion points must be between 0 and 40")
        return self


class DraftScoringConfig(BaseModel):
    """Structurally safe draft scoring that may be incomplete until publication."""

    model_config = ConfigDict(extra="forbid")

    weights: dict[str, int] = Field(
        default_factory=lambda: {
            "icp_fit": 40,
            "intent_urgency": 35,
            "engagement_quality": 25,
        }
    )
    component_points: dict[str, dict[str, int]] = Field(default_factory=dict)
    signals: dict[str, list[str]] = Field(default_factory=dict)
    message_detail_chars: int = Field(default=240, ge=1, le=10000)

    @model_validator(mode="after")
    def structure_is_safe(self) -> DraftScoringConfig:
        if self.weights != {
            "icp_fit": 40,
            "intent_urgency": 35,
            "engagement_quality": 25,
        }:
            raise ValueError("component weights must remain ICP 40, Intent 35, Engagement 25")
        unknown = set(self.component_points.get("icp_fit", {})) - ICP_CRITERIA
        if unknown:
            raise ValueError(f"unsupported ICP scoring criteria: {', '.join(sorted(unknown))}")
        for component, criteria in self.component_points.items():
            if component not in self.weights:
                raise ValueError(f"unsupported scoring component: {component}")
            if any(points < 0 or points > self.weights[component] for points in criteria.values()):
                raise ValueError(f"{component} criterion points are outside safe bounds")
        return self


class IcpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    profile_name: str
    profile_version: str
    target_industries: list[str] = Field(default_factory=list)
    excluded_industries: list[str] = Field(default_factory=list)
    target_geographies: list[str] = Field(default_factory=list)
    target_business_types: list[str] = Field(default_factory=list)
    strategic_accounts: list[str] = Field(default_factory=list)
    company_size: CompanySizeConfig = Field(default_factory=CompanySizeConfig)
    buyer_roles: dict[str, list[str]] = Field(default_factory=dict)
    buyer: BuyerProfileConfig = Field(default_factory=BuyerProfileConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    commercial: CommercialFitConfig = Field(default_factory=CommercialFitConfig)
    technology: TechnologyFitConfig = Field(default_factory=TechnologyFitConfig)
    fit_signals: FitSignalsConfig = Field(default_factory=FitSignalsConfig)
    pain_points: list[str] = Field(default_factory=list)
    disqualifiers: list[DisqualifierConfig] = Field(default_factory=list)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)


class WorkspaceIcpDraftConfig(BaseModel):
    """The editable representation; publication converts this to strict IcpConfig."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    profile_name: str = Field(min_length=1, max_length=255)
    profile_version: str = Field(min_length=1, max_length=64)
    target_industries: list[str] = Field(default_factory=list)
    excluded_industries: list[str] = Field(default_factory=list)
    target_geographies: list[str] = Field(default_factory=list)
    target_business_types: list[str] = Field(default_factory=list)
    strategic_accounts: list[str] = Field(default_factory=list)
    company_size: CompanySizeConfig = Field(default_factory=CompanySizeConfig)
    buyer_roles: dict[str, list[str]] = Field(default_factory=dict)
    buyer: BuyerProfileConfig = Field(default_factory=BuyerProfileConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    commercial: CommercialFitConfig = Field(default_factory=CommercialFitConfig)
    technology: TechnologyFitConfig = Field(default_factory=TechnologyFitConfig)
    fit_signals: FitSignalsConfig = Field(default_factory=FitSignalsConfig)
    pain_points: list[str] = Field(default_factory=list)
    disqualifiers: list[DisqualifierConfig] = Field(default_factory=list)
    scoring: DraftScoringConfig = Field(default_factory=DraftScoringConfig)

    def publication_problems(self) -> list[str]:
        problems: list[str] = []
        points = self.scoring.component_points
        for component, maximum in self.scoring.weights.items():
            actual = sum(points.get(component, {}).values())
            if actual != maximum:
                problems.append(
                    f"{component.replace('_', ' ').title()} points must total exactly "
                    f"{maximum}; currently {actual}."
                )
        industry_overlap = {
            item.casefold() for item in self.target_industries
        } & {item.casefold() for item in self.excluded_industries}
        if industry_overlap:
            problems.append(
                "Industries cannot be both targeted and excluded: "
                + ", ".join(sorted(industry_overlap))
                + "."
            )
        codes = [rule.code.casefold() for rule in self.disqualifiers]
        duplicates = sorted({code for code in codes if codes.count(code) > 1})
        if duplicates:
            problems.append(
                "Hard disqualifier codes must be unique: " + ", ".join(duplicates) + "."
            )
        for collection_name, values in (
            ("target industries", self.target_industries),
            ("excluded industries", self.excluded_industries),
            ("target geographies", self.target_geographies),
        ):
            normalized = [value.strip().casefold() for value in values if value.strip()]
            if len(normalized) != len(set(normalized)):
                problems.append(f"Duplicate {collection_name} are not allowed.")
        return problems

    def to_published(self) -> IcpConfig:
        problems = self.publication_problems()
        if problems:
            raise ValueError(" ".join(problems))
        return IcpConfig.model_validate(self.model_dump(mode="json"))


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
