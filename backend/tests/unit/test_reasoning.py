from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from openai import OpenAI

from lead_agent.application.services import _context_analysis
from lead_agent.domain.analysis import (
    InvalidReasoningOutput,
    ReasoningOutput,
    ReasoningProviderTimeout,
    ReasoningProviderUnavailable,
)
from lead_agent.domain.gmail import GmailLeadExtraction
from lead_agent.domain.meeting_reply import MeetingReplyClassification
from lead_agent.domain.models import NormalizedLead
from lead_agent.domain.operations import WorkspaceBusinessProfile
from lead_agent.domain.scoring import qualify_lead
from lead_agent.infrastructure.observability import reset_request_id, set_request_id
from lead_agent.infrastructure.reasoning import openai_provider
from lead_agent.infrastructure.reasoning.assistant_prompts import (
    LEAD_ASSISTANT_SYSTEM_PROMPT,
    WORKSPACE_ASSISTANT_SYSTEM_PROMPT,
)
from lead_agent.infrastructure.reasoning.deterministic import DeterministicReasoningProvider
from lead_agent.infrastructure.reasoning.factory import build_reasoning_provider
from lead_agent.infrastructure.reasoning.openai_provider import OpenAIReasoningProvider
from lead_agent.settings.config import Settings


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


def test_shared_assistant_prompts_stay_bounded_and_keep_safety_rules() -> None:
    # The detailed workflows fit within the configured request budget.
    assert len(LEAD_ASSISTANT_SYSTEM_PROMPT) < 4500
    assert len(WORKSPACE_ASSISTANT_SYSTEM_PROMPT) < 12000
    assert "latest user message directly asks" in LEAD_ASSISTANT_SYSTEM_PROMPT
    assert "never starts a call" in LEAD_ASSISTANT_SYSTEM_PROMPT
    assert "bounded matches, not complete history" in WORKSPACE_ASSISTANT_SYSTEM_PROMPT
    assert "Never execute or claim a change until the user explicitly approves it" in (
        WORKSPACE_ASSISTANT_SYSTEM_PROMPT
    )
    assert "Never send automatically or claim sent" in WORKSPACE_ASSISTANT_SYSTEM_PROMPT


def test_openai_reasoning_prompt_defines_timed_meeting_urgency() -> None:
    assert "concrete request to meet, talk, schedule, or decide" in openai_provider.SYSTEM_PROMPT
    assert "exact phrase in explicit_timeline" in openai_provider.SYSTEM_PROMPT


def test_openai_workspace_assistant_uses_compact_prompt_and_output_cap() -> None:
    request: dict[str, object] = {}

    class Responses:
        def parse(self, **kwargs: object) -> SimpleNamespace:
            request.update(kwargs)
            return SimpleNamespace(output_parsed={"reply": "Hello."}, usage=None)

    provider = OpenAIReasoningProvider(
        api_key="test-only-key",
        model="test-model",
        timeout_seconds=10,
        max_retries=0,
        store_responses=False,
        client=SimpleNamespace(responses=Responses()),
    )
    result = provider.assist_with_workspace({"messages": []})

    assert result.reply == "Hello."
    assert request["max_output_tokens"] == 1800
    assert request["input"][0]["content"] == WORKSPACE_ASSISTANT_SYSTEM_PROMPT
    assert request["input"][1]["content"] == '{"messages":[]}'


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


@pytest.mark.parametrize(
    ("message", "intent_level", "expected_points"),
    [
        (
            "We have shortlisted your platform and intend to select a vendor this quarter.",
            "high",
            10,
        ),
        ("We are evaluating several approaches before deciding.", "medium", 5),
        ("Please share general information about your service.", "low", 0),
    ],
)
def test_grounded_ai_intent_changes_deterministic_buying_stage_points(
    icp, routing, message: str, intent_level: str, expected_points: int
) -> None:
    lead = make_lead(
        message=message,
        product_or_service="",
        budget=None,
    )
    analysis = _context_analysis(lead, icp, routing, DeterministicReasoningProvider())
    assert analysis is not None
    analysis = analysis.model_copy(
        update={
            "intent_level": intent_level,
            "overall_confidence": 0.95,
            "requires_manual_review": False,
        }
    )

    result = qualify_lead(lead, icp, routing, analysis)

    buying_stage = next(line for line in result.score_breakdown if line.key == "buying_stage")
    assert buying_stage.points == expected_points


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


class FakeSecretError(Exception):
    status_code = 429

    def __str__(self) -> str:
        return "Authorization: Bearer private error body"


class FakeServerError(Exception):
    status_code = 503

    def __str__(self) -> str:
        return "private server response body"


class FakeModelUnavailableError(Exception):
    status_code = 404
    code = "model_not_found"
    type = "invalid_request_error"

    def __str__(self) -> str:
        return "private provider body with secret-token"


class APIConnectionError(Exception):
    pass


class APITimeoutError(TimeoutError):
    pass


class AuthenticationError(Exception):
    status_code = 401
    code = "invalid_api_key"
    type = "authentication_error"


class PermissionDeniedError(Exception):
    status_code = 403
    code = "permission_denied"
    type = "permission_error"


class FakeQuotaError(Exception):
    status_code = 429
    code = "credit_balance_exhausted"
    type = "insufficient_quota"


class FakeRequestIdError(Exception):
    status_code = 403
    request_id = "req-safe-permission-id"

    def __init__(self) -> None:
        super().__init__("Authorization: Bearer private-secret")
        self.body = {
            "error": {
                "code": "permission_denied",
                "type": "permission_error",
                "message": "Authorization: Bearer private-secret",
            }
        }


class LengthFinishReasonError(Exception):
    pass


class UnexpectedProviderError(Exception):
    def __str__(self) -> str:
        return "Authorization: Bearer private-unexpected-secret"


@pytest.mark.parametrize(
    ("provider", "expected_code"),
    [
        (FailingProvider(), "provider_timeout"),
        (MalformedProvider(), "invalid_provider_output"),
        (UnavailableProvider(), "provider_unavailable"),
    ],
)
def test_provider_failures_keep_error_status_without_default_manual_review(
    icp, routing, provider, expected_code: str
) -> None:
    analysis = _context_analysis(make_lead(), icp, routing, provider)
    assert analysis.status == "provider_error"
    assert analysis.requires_manual_review is False
    assert analysis.error_code == expected_code
    assert analysis.summary.need


def test_default_review_rules_only_use_personal_email_or_missing_identity(icp, routing) -> None:
    base_lead = make_lead()
    baseline = DeterministicReasoningProvider().analyze(base_lead, icp)
    low = baseline.model_copy(update={"overall_confidence": 0.2})
    contradictory = baseline.model_copy(
        update={"contradictions": ["Timeline says both this week and no planned date."]}
    )

    assert not _context_analysis(
        base_lead, icp, routing, StaticProvider(low)
    ).requires_manual_review
    assert not _context_analysis(
        base_lead, icp, routing, StaticProvider(contradictory)
    ).requires_manual_review
    assert _context_analysis(
        make_lead(email="avery@gmail.com"), icp, routing, StaticProvider(baseline)
    ).requires_manual_review
    assert _context_analysis(
        make_lead(full_name="Name not provided"), icp, routing, StaticProvider(baseline)
    ).requires_manual_review
    assert _context_analysis(
        make_lead(company_name="Unknown"), icp, routing, StaticProvider(baseline)
    ).requires_manual_review


def test_optional_analysis_review_rules_can_be_enabled(icp, routing) -> None:
    lead = make_lead()
    baseline = DeterministicReasoningProvider().analyze(lead, icp)
    low = baseline.model_copy(update={"overall_confidence": 0.2})
    contradictory = baseline.model_copy(
        update={"contradictions": ["Timeline says both this week and no planned date."]}
    )
    enabled = routing.model_copy(
        update={
            "manual_review": routing.manual_review.model_copy(
                update={"low_confidence": True, "contradictory_evidence": True}
            )
        }
    )

    assert _context_analysis(lead, icp, enabled, StaticProvider(low)).requires_manual_review
    assert _context_analysis(
        lead, icp, enabled, StaticProvider(contradictory)
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


def test_manual_context_can_be_analyzed_even_for_a_hard_disqualifier(icp, routing) -> None:
    lead = make_lead(
        source_detail="manual-control-center",
        job_title="Student",
        message="Discovery completed; technical review is the next step.",
    )
    output = DeterministicReasoningProvider().analyze(lead, icp)

    class RecordingProvider(StaticProvider):
        received_message: str | None = None

        def analyze(self, analyzed_lead, config):
            self.received_message = analyzed_lead.message
            return super().analyze(analyzed_lead, config)

    provider = RecordingProvider(output)
    analysis = _context_analysis(
        lead,
        icp,
        routing,
        provider,
        analyze_hard_disqualified=True,
    )

    assert provider.received_message == lead.message
    assert analysis.provider == "test-provider"
    assert analysis.status != "skipped_hard_disqualifier"


def test_missing_openai_key_is_a_safe_configuration_event(
    icp, caplog: pytest.LogCaptureFixture
) -> None:
    provider = OpenAIReasoningProvider(
        api_key="",
        model="test-model",
        timeout_seconds=1,
        max_retries=0,
        store_responses=False,
    )
    with caplog.at_level("INFO", logger="lead_agent.openai"):
        with pytest.raises(ReasoningProviderUnavailable, match="configuration is invalid"):
            provider.analyze(make_lead(), icp)
    operation = next(record for record in caplog.records if record.msg == "openai.analysis")
    assert operation.fields["outcome"] == "configuration_error"
    assert operation.fields["provider_error_category"] == "configuration"
    assert operation.fields["retryable"] is False
    assert "test-only-key" not in caplog.text
    assert "OPENAI_API_KEY" not in caplog.text


def test_openai_sdk_initialization_error_is_classified_safely(
    icp, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def invalid_client(**_kwargs: object) -> None:
        raise UnexpectedProviderError()

    monkeypatch.setattr(
        openai_provider,
        "import_module",
        lambda _name: SimpleNamespace(OpenAI=invalid_client),
    )
    provider = OpenAIReasoningProvider(
        api_key="test-only-key",
        model="test-model",
        timeout_seconds=7,
        max_retries=0,
        store_responses=False,
    )
    with caplog.at_level("INFO", logger="lead_agent.openai"):
        with pytest.raises(ReasoningProviderUnavailable, match="configuration is invalid"):
            provider.analyze(make_lead(), icp)

    operation = next(record for record in caplog.records if record.msg == "openai.analysis")
    assert operation.fields["outcome"] == "configuration_error"
    assert operation.fields["provider_exception_class"] == "UnexpectedProviderError"
    assert operation.fields["retryable"] is False
    assert "private-unexpected-secret" not in caplog.text
    assert "test-only-key" not in caplog.text


def test_openai_request_is_bounded_correlated_and_records_safe_usage(
    icp, caplog: pytest.LogCaptureFixture
) -> None:
    lead = make_lead(
        source_detail="manual-control-center",
        message="Discovery is complete; technical review is next week.",
    )
    business_profile = WorkspaceBusinessProfile(
        business_name="Corral Systems",
        alternate_names=["Corral", "Ingenuity Systems"],
        website="https://corral.example",
        email_domains=["corral.example"],
        description="Sales operations software.",
        products_services="Lead qualification and routing.",
        lead_distinction_notes="The lead's company is the prospective customer.",
    )
    output = DeterministicReasoningProvider().analyze(lead, icp)

    class Responses:
        request: dict[str, object] = {}

        def parse(self, **kwargs: object) -> SimpleNamespace:
            self.request = kwargs
            return SimpleNamespace(
                output_parsed=output,
                _request_id="req-provider-safe-id",
                usage=SimpleNamespace(input_tokens=123, output_tokens=45, total_tokens=168),
            )

    responses = Responses()
    token = set_request_id("app-request-123")
    try:
        provider = OpenAIReasoningProvider(
            api_key="test-only-key",
            model="test-model",
            timeout_seconds=7,
            max_retries=1,
            store_responses=False,
            client=SimpleNamespace(responses=responses),
        )
        with caplog.at_level("INFO", logger="lead_agent.openai"):
            parsed = provider.analyze(lead, icp, business_profile)
    finally:
        reset_request_id(token)

    request = responses.request
    assert parsed == output
    assert request["extra_headers"] == {"X-Client-Request-Id": "app-request-123"}
    assert request["model"] == "test-model"
    assert request["text_format"] is ReasoningOutput
    assert "not guaranteed to be the prospect's exact wording" in request["input"][0]["content"]
    user_payload = json.loads(request["input"][1]["content"])
    assert user_payload["normalized_lead"]["email_domain"] == "example.com"
    assert user_payload["normalized_lead"]["source_detail"] == "manual-control-center"
    assert user_payload["normalized_lead"]["message"] == lead.message
    assert user_payload["workspace_business_profile"] == business_profile.model_dump(
        mode="json"
    )
    assert "email" not in user_payload["normalized_lead"]
    assert "phone" not in user_payload["normalized_lead"]
    assert "raw_payload" not in user_payload["normalized_lead"]
    operation = next(record for record in caplog.records if record.msg == "openai.analysis")
    assert operation.fields["provider"] == "openai"
    assert operation.fields["operation"] == "responses.parse"
    assert operation.fields["outcome"] == "success"
    assert operation.fields["model"] == "test-model"
    assert operation.fields["provider_request_id"] == "req-provider-safe-id"
    assert operation.fields["input_tokens"] == 123
    assert operation.fields["output_tokens"] == 45
    assert operation.fields["total_tokens"] == 168
    assert "test-only-key" not in caplog.text
    assert lead.email not in caplog.text
    assert lead.message not in caplog.text


def test_context_analysis_passes_nonempty_business_profile_to_provider(icp, routing) -> None:
    profile = WorkspaceBusinessProfile(business_name="Corral Systems")

    class ProfileCaptureProvider:
        provider_name = "profile-test"
        model_name = "test-model"
        prompt_version = "test-v1"
        received_profile: WorkspaceBusinessProfile | None = None

        def analyze(self, lead, config, business_profile=None):
            self.received_profile = business_profile
            return DeterministicReasoningProvider().analyze(lead, config)

    provider = ProfileCaptureProvider()
    _context_analysis(
        make_lead(),
        icp,
        routing,
        provider,
        business_profile=profile,
    )

    assert provider.received_profile is profile


@pytest.mark.parametrize(
    ("error", "expected_error", "expected_outcome", "expected_category", "retryable"),
    [
        (
            APIConnectionError("private transport details"),
            ReasoningProviderUnavailable,
            "connection_error",
            "transport",
            True,
        ),
        (
            APITimeoutError("private timeout details"),
            ReasoningProviderTimeout,
            "timeout",
            "timeout",
            True,
        ),
        (
            AuthenticationError("private auth details"),
            ReasoningProviderUnavailable,
            "authentication_error",
            "authentication",
            False,
        ),
        (
            PermissionDeniedError("private permission details"),
            ReasoningProviderUnavailable,
            "permission_error",
            "permission",
            False,
        ),
        (
            FakeSecretError("private error body"),
            ReasoningProviderUnavailable,
            "rate_limited",
            "rate_limit",
            True,
        ),
        (
            FakeQuotaError("private quota details"),
            ReasoningProviderUnavailable,
            "quota_error",
            "quota",
            False,
        ),
        (
            FakeServerError("private server response body"),
            ReasoningProviderUnavailable,
            "server_error",
            "server",
            True,
        ),
        (
            FakeModelUnavailableError(),
            ReasoningProviderUnavailable,
            "model_access_error",
            "model_access",
            False,
        ),
        (
            LengthFinishReasonError("private structured output details"),
            InvalidReasoningOutput,
            "invalid_output",
            "structured_output",
            False,
        ),
        (
            UnexpectedProviderError(),
            ReasoningProviderUnavailable,
            "unexpected_error",
            "unexpected",
            False,
        ),
    ],
)
def test_openai_failures_are_translated_without_provider_details(
    icp,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    expected_error: type[Exception],
    expected_outcome: str,
    expected_category: str,
    retryable: bool,
) -> None:
    class Responses:
        def parse(self, **_kwargs: object) -> None:
            raise error

    provider = OpenAIReasoningProvider(
        api_key="test-only-key",
        model="test-model",
        timeout_seconds=7,
        max_retries=1,
        store_responses=False,
        client=SimpleNamespace(responses=Responses()),
    )
    with caplog.at_level("INFO", logger="lead_agent.openai"):
        with pytest.raises(expected_error) as raised:
            provider.analyze(make_lead(), icp)
    assert "private error body" not in str(raised.value)
    assert "test-only-key" not in caplog.text
    operation = next(record for record in caplog.records if record.msg == "openai.analysis")
    assert operation.fields["outcome"] == expected_outcome
    assert operation.fields["provider_error_category"] == expected_category
    assert operation.fields["retryable"] is retryable
    assert operation.fields["provider_exception_class"] == error.__class__.__name__
    assert str(error) not in caplog.text
    assert "private-secret" not in caplog.text
    if getattr(error, "status_code", None) is not None:
        assert operation.fields["provider_status_code"] == error.status_code


def test_openai_schema_failure_is_explicit_and_safe(icp, caplog: pytest.LogCaptureFixture) -> None:
    provider = OpenAIReasoningProvider(
        api_key="test-only-key",
        model="test-model",
        timeout_seconds=7,
        max_retries=1,
        store_responses=False,
        client=SimpleNamespace(
            responses=SimpleNamespace(
                parse=lambda **_kwargs: SimpleNamespace(
                    output_parsed={"unexpected": "private output"}, usage=None
                )
            )
        ),
    )
    with caplog.at_level("INFO", logger="lead_agent.openai"):
        with pytest.raises(InvalidReasoningOutput, match="schema validation"):
            provider.analyze(make_lead(), icp)
    assert "private output" not in caplog.text
    operation = next(record for record in caplog.records if record.msg == "openai.analysis")
    assert operation.fields["outcome"] == "invalid_output"
    assert operation.fields["provider_error_category"] == "structured_output"
    assert operation.fields["retryable"] is False


def test_openai_meeting_reply_classifier_uses_strict_schema_without_storing_response() -> None:
    request: dict[str, object] = {}

    class Responses:
        def parse(self, **kwargs: object) -> SimpleNamespace:
            request.update(kwargs)
            return SimpleNamespace(
                output_parsed={
                    "intent": "confirm",
                    "option_number": 2,
                    "confidence": 0.99,
                    "evidence": "the second one",
                },
                usage=None,
            )

    provider = OpenAIReasoningProvider(
        api_key="test-only-key",
        model="test-model",
        timeout_seconds=7,
        max_retries=1,
        store_responses=True,
        client=SimpleNamespace(responses=Responses()),
    )

    result = provider.classify_meeting_reply(
        {"reply": "Sure, the second one.", "options": [{"number": 1}, {"number": 2}]}
    )

    assert isinstance(result, MeetingReplyClassification)
    assert result.intent == "confirm"
    assert result.option_number == 2
    assert request["text_format"] is MeetingReplyClassification
    assert request["store"] is False
    assert request["max_output_tokens"] == 300


def test_openai_sdk_retries_mocked_server_errors_to_configured_limit(
    icp, caplog: pytest.LogCaptureFixture
) -> None:
    attempts = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            503,
            json={"error": {"message": "private response body", "type": "server_error"}},
        )

    sdk_client = OpenAI(
        api_key="test-only-key",
        max_retries=2,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    provider = OpenAIReasoningProvider(
        api_key="test-only-key",
        model="test-model",
        timeout_seconds=7,
        max_retries=2,
        store_responses=False,
        client=sdk_client,
    )
    with caplog.at_level("INFO", logger="lead_agent.openai"):
        with pytest.raises(ReasoningProviderUnavailable, match="unavailable"):
            provider.analyze(make_lead(), icp)

    assert attempts == 3
    assert "private response body" not in caplog.text
    assert "test-only-key" not in caplog.text
    operation = next(record for record in caplog.records if record.msg == "openai.analysis")
    assert operation.fields["outcome"] == "server_error"
    assert operation.fields["provider_status_code"] == 503
    assert operation.fields["provider_error_type"] == "server_error"
    assert operation.fields["retryable"] is True


def test_openai_failure_logs_only_safe_api_status_and_code(
    icp, caplog: pytest.LogCaptureFixture
) -> None:
    class Responses:
        def parse(self, **_kwargs: object) -> None:
            raise FakeModelUnavailableError()

    provider = OpenAIReasoningProvider(
        api_key="test-only-key",
        model="gpt-5.6-luna",
        timeout_seconds=7,
        max_retries=0,
        store_responses=False,
        client=SimpleNamespace(responses=Responses()),
    )
    with caplog.at_level("INFO", logger="lead_agent.openai"):
        with pytest.raises(ReasoningProviderUnavailable):
            provider.analyze(make_lead(), icp)

    operation = next(record for record in caplog.records if record.msg == "openai.analysis")
    assert operation.fields["provider_status_code"] == 404
    assert operation.fields["provider_error_code"] == "model_not_found"
    assert operation.fields["provider_error_type"] == "invalid_request_error"
    assert operation.fields["provider_exception_class"] == "FakeModelUnavailableError"
    assert "private provider body" not in caplog.text
    assert "secret-token" not in caplog.text
    assert "test-only-key" not in caplog.text


def test_openai_error_logs_safe_provider_request_id(icp, caplog: pytest.LogCaptureFixture) -> None:
    class Responses:
        def parse(self, **_kwargs: object) -> None:
            raise FakeRequestIdError()

    provider = OpenAIReasoningProvider(
        api_key="test-only-key",
        model="test-model",
        timeout_seconds=7,
        max_retries=0,
        store_responses=False,
        client=SimpleNamespace(responses=Responses()),
    )
    with caplog.at_level("INFO", logger="lead_agent.openai"):
        with pytest.raises(ReasoningProviderUnavailable):
            provider.analyze(make_lead(), icp)

    operation = next(record for record in caplog.records if record.msg == "openai.analysis")
    assert operation.fields["provider_request_id"] == "req-safe-permission-id"
    assert operation.fields["provider_error_code"] == "permission_denied"
    assert operation.fields["provider_error_type"] == "permission_error"
    assert "private-secret" not in caplog.text
    assert "test-only-key" not in caplog.text


def test_provider_factory_selects_openai_or_offline_and_passes_runtime_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            observed.update(kwargs)
            self.responses = SimpleNamespace()

    monkeypatch.setattr(
        openai_provider,
        "import_module",
        lambda _name: SimpleNamespace(OpenAI=FakeClient),
    )
    settings = Settings.model_construct(
        llm_provider="openai",
        openai_api_key="test-only-key",
        openai_model="test-model",
        openai_org_id="test-org",
        openai_timeout_seconds=7,
        openai_max_retries=4,
        openai_store_responses=False,
    )

    provider = build_reasoning_provider(settings)
    assert isinstance(provider, OpenAIReasoningProvider)
    assert observed["timeout"] == 7
    assert observed["max_retries"] == 4
    assert observed["organization"] == "test-org"
    offline = build_reasoning_provider(Settings.model_construct(llm_provider="deterministic"))
    mock = build_reasoning_provider(Settings.model_construct(llm_provider="mock"))
    assert offline.provider_name == "deterministic"
    assert mock.provider_name == "deterministic"


def test_evaluation_dataset_covers_all_supported_channels() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "phase3_evaluation.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    assert {case["channel"] for case in cases} == {"website", "email", "linkedin", "clutch"}
    assert all(case["expected"]["qualification_category"] for case in cases)


def test_gmail_extraction_timeout_has_bounded_timeout_and_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    options: dict[str, object] = {}

    class APITimeoutError(Exception):
        pass

    class Responses:
        def parse(self, **_kwargs: object) -> None:
            nonlocal attempts
            attempts += 1
            raise APITimeoutError("private email payload")

    class Client:
        responses = Responses()

        def with_options(self, **kwargs: object) -> Client:
            options.update(kwargs)
            return self

    monkeypatch.setattr(openai_provider, "sleep", lambda _seconds: None)
    provider = OpenAIReasoningProvider(
        api_key="test-only-key",
        model="test-model",
        timeout_seconds=90,
        max_retries=8,
        store_responses=False,
        client=Client(),
    )
    with pytest.raises(ReasoningProviderTimeout):
        provider.extract_gmail_lead(headers={}, subject="Question", body="Need help.")
    assert attempts == 3
    assert options == {"timeout": 15, "max_retries": 0}


def test_gmail_extraction_retries_transient_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    options: dict[str, object] = {}

    class APIConnectionError(Exception):
        pass

    class Responses:
        def parse(self, **_kwargs: object) -> SimpleNamespace:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise APIConnectionError("transient")
            return SimpleNamespace(
                output_parsed=GmailLeadExtraction(
                    is_genuine_inquiry=True,
                    reason="A clear request.",
                    inquiry_evidence="Need help",
                ),
                usage=None,
            )

    class Client:
        responses = Responses()

        def with_options(self, **kwargs: object) -> Client:
            options.update(kwargs)
            return self

    monkeypatch.setattr(openai_provider, "sleep", lambda _seconds: None)
    provider = OpenAIReasoningProvider(
        api_key="test-only-key",
        model="test-model",
        timeout_seconds=90,
        max_retries=8,
        store_responses=False,
        client=Client(),
    )
    result = provider.extract_gmail_lead(
        headers={}, subject="Question", body="Need help."
    )
    assert result.is_genuine_inquiry
    assert attempts == 2
    assert options == {"timeout": 15, "max_retries": 0}


def test_gmail_extraction_uses_workspace_profile_and_excludes_routine_marketing() -> None:
    business_profile = WorkspaceBusinessProfile(
        business_name="Corral Systems",
        alternate_names=["Corral"],
        email_domains=["corral.example"],
        description="CRM and sales operations software.",
    )
    request: dict[str, object] = {}

    class Responses:
        def parse(self, **kwargs: object) -> SimpleNamespace:
            request.update(kwargs)
            return SimpleNamespace(
                output_parsed=GmailLeadExtraction(
                    is_genuine_inquiry=False,
                    reason="Routine promotional message, not a prospect inquiry.",
                ),
                usage=None,
            )

    provider = OpenAIReasoningProvider(
        api_key="test-only-key",
        model="test-model",
        timeout_seconds=10,
        max_retries=0,
        store_responses=False,
        client=SimpleNamespace(responses=Responses()),
    )
    result = provider.extract_gmail_lead(
        headers={
            "workspace-business-profile": business_profile.model_dump_json(),
            "internal-domains": "corral.example",
        },
        subject="Weekly promotions",
        body="A vendor promotion sent to the workspace.",
    )

    assert not result.is_genuine_inquiry
    user_payload = json.loads(request["input"][1]["content"])
    assert user_payload["workspace_business_profile"] == business_profile.model_dump(
        mode="json"
    )
    system_prompt = request["input"][0]["content"]
    assert "Exclude routine promotional or marketing messages sent to the workspace" in (
        system_prompt
    )
    assert "Keep relevant replies in existing sales conversations eligible" in system_prompt


def test_gmail_extraction_maps_unknown_provider_errors_to_safe_unavailable() -> None:
    class Responses:
        def parse(self, **_kwargs: object) -> None:
            raise RuntimeError("private email body")

    class Client:
        responses = Responses()

    provider = OpenAIReasoningProvider(
        api_key="test-only-key",
        model="test-model",
        timeout_seconds=90,
        max_retries=8,
        store_responses=False,
        client=Client(),
    )
    with pytest.raises(
        ReasoningProviderUnavailable, match="OpenAI Gmail extraction failed"
    ) as error:
        provider.extract_gmail_lead(headers={}, subject="Question", body="Need help.")
    assert "private email body" not in str(error.value)
