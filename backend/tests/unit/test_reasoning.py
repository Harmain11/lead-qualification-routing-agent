from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from lead_agent.application.services import _context_analysis
from lead_agent.domain.analysis import ReasoningProviderTimeout, ReasoningProviderUnavailable
from lead_agent.domain.models import NormalizedLead
from lead_agent.domain.scoring import qualify_lead
from lead_agent.infrastructure.reasoning.deterministic import DeterministicReasoningProvider
from lead_agent.infrastructure.reasoning.openai_provider import OpenAIReasoningProvider


def make_lead(**overrides: object) -> NormalizedLead:
    values: dict[str, object] = {
        "id": uuid4(),
        "source": "website",
        "source_detail": "web-form",
        "external_id": "reasoning-001",
        "full_name": "Maya Thompson",
        "email": "maya@example.com",
        "phone": "+1 555 010 0000",
        "job_title": "VP of Operations",
        "company_name": "Northstar SaaS",
        "industry": "SaaS",
        "employee_count": 180,
        "annual_revenue": None,
        "geography": "United States",
        "budget": Decimal("30000"),
        "product_or_service": "Lead routing automation",
        "message": (
            "We are ready to implement a Salesforce API integration this month because slow "
            "response is causing missed leads. Please send a proposal this week. Our approved "
            "budget is $30,000."
        ),
        "received_at": datetime.now(UTC),
        "raw_payload": {},
    }
    values.update(overrides)
    return NormalizedLead(**values)  # type: ignore[arg-type]


def test_high_intent_context_is_grounded_and_structured(icp) -> None:
    output = DeterministicReasoningProvider().analyze(make_lead(), icp)

    assert output.intent_level == "high"
    assert output.context.urgency.value == "high"
    assert output.context.explicit_timeline.value == "this month"
    assert output.context.budget_information.information_kind == "explicit"
    assert output.context.decision_maker_status.value == "likely decision maker"
    assert output.context.technical_requirements.value
    assert output.context.competitor_or_current_tools.value
    assert output.context.urgency.evidence
    assert output.summary.need == "Lead routing automation"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("We are evaluating options and comparing providers next quarter.", "medium"),
        ("Just curious about what you do. Maybe someday and there is no rush.", "low"),
    ],
)
def test_medium_and_low_intent_are_distinguished(icp, message: str, expected: str) -> None:
    output = DeterministicReasoningProvider().analyze(make_lead(message=message, budget=None), icp)
    assert output.intent_level == expected


def test_unknown_budget_and_vague_timeline_are_not_fabricated(icp) -> None:
    output = DeterministicReasoningProvider().analyze(
        make_lead(message="Please send a general overview.", budget=None), icp
    )
    assert output.context.budget_information.information_kind == "unknown"
    assert output.context.budget_information.value is None
    assert output.context.explicit_timeline.information_kind == "unknown"
    assert "budget_information" in output.unknown_fields


def test_missing_optional_information_remains_unknown(icp) -> None:
    output = DeterministicReasoningProvider().analyze(
        make_lead(
            phone=None,
            job_title=None,
            industry=None,
            employee_count=None,
            geography=None,
            budget=None,
            message="Please send an overview of lead routing.",
        ),
        icp,
    )
    assert output.context.person_role.information_kind == "unknown"
    assert output.context.company_size_or_spend.information_kind == "unknown"
    assert output.icp_evaluation.industry_fit.status == "unknown"


def test_researcher_is_not_promoted_to_decision_maker(icp) -> None:
    output = DeterministicReasoningProvider().analyze(
        make_lead(
            job_title="Research Assistant",
            message="I am researching workflow automation for an internal report.",
            budget=None,
        ),
        icp,
    )
    assert output.context.decision_maker_status.value == "researcher or non-decision-maker"


def test_reasoning_evidence_keeps_scores_bounded(icp, routing) -> None:
    lead = make_lead()
    output = DeterministicReasoningProvider().analyze(lead, icp)
    analysis = _context_analysis(lead, icp, routing, DeterministicReasoningProvider())
    result = qualify_lead(lead, icp, routing, analysis)

    assert output.overall_confidence <= 1
    assert result.component_scores["icp_fit"] <= 40
    assert result.component_scores["intent_urgency"] <= 35
    assert result.component_scores["engagement_quality"] <= 25
    assert result.total_score <= 100
    assert any(line.evidence for line in result.score_breakdown)


class FailingProvider:
    provider_name = "test-provider"
    model_name = "failure-model"
    prompt_version = "test-v1"

    def analyze(self, lead, icp):
        raise ReasoningProviderTimeout("simulated timeout")


class MalformedProvider:
    provider_name = "test-provider"
    model_name = "malformed-model"
    prompt_version = "test-v1"

    def analyze(self, lead, icp):
        return {"unexpected": "shape"}


class UnavailableProvider:
    provider_name = "test-provider"
    model_name = "offline-model"
    prompt_version = "test-v1"

    def analyze(self, lead, icp):
        raise ReasoningProviderUnavailable("simulated provider outage")


class StaticProvider:
    provider_name = "test-provider"
    model_name = "static-model"
    prompt_version = "test-v1"

    def __init__(self, output):
        self.output = output

    def analyze(self, lead, icp):
        return self.output


@pytest.mark.parametrize(
    ("provider", "expected_code"),
    [
        (FailingProvider(), "provider_timeout"),
        (MalformedProvider(), "invalid_provider_output"),
        (UnavailableProvider(), "provider_unavailable"),
    ],
)
def test_provider_failures_fall_back_to_manual_review(
    icp, routing, provider, expected_code: str
) -> None:
    analysis = _context_analysis(make_lead(), icp, routing, provider)
    assert analysis.status == "provider_error"
    assert analysis.requires_manual_review is True
    assert analysis.error_code == expected_code
    assert analysis.summary.need


def test_low_confidence_and_contradictions_require_manual_review(icp, routing) -> None:
    lead = make_lead()
    baseline = DeterministicReasoningProvider().analyze(lead, icp)
    low = baseline.model_copy(update={"overall_confidence": 0.2})
    contradictory = baseline.model_copy(
        update={"contradictions": ["Timeline says both this week and no planned date."]}
    )

    assert _context_analysis(lead, icp, routing, StaticProvider(low)).requires_manual_review
    assert _context_analysis(
        lead, icp, routing, StaticProvider(contradictory)
    ).requires_manual_review


def test_hard_disqualifier_bypasses_configured_provider(icp, routing) -> None:
    class MustNotRun(FailingProvider):
        def analyze(self, lead, icp):
            raise AssertionError("provider must not run for a hard-disqualified lead")

    analysis = _context_analysis(
        make_lead(job_title="Student", message="This is homework for a school project."),
        icp,
        routing,
        MustNotRun(),
    )
    assert analysis.status == "skipped_hard_disqualifier"
    assert analysis.requires_manual_review is False


def test_missing_openai_key_is_a_graceful_provider_error(icp) -> None:
    provider = OpenAIReasoningProvider(
        api_key="",
        model="test-model",
        timeout_seconds=1,
        max_retries=0,
        store_responses=False,
    )
    with pytest.raises(ReasoningProviderUnavailable, match="OPENAI_API_KEY"):
        provider.analyze(make_lead(), icp)


def test_evaluation_dataset_covers_all_supported_channels() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "phase3_evaluation.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    assert {case["channel"] for case in cases} == {"website", "email", "linkedin", "clutch"}
    assert all(case["expected"]["qualification_category"] for case in cases)
