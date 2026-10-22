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
from lead_agent.domain.models import NormalizedLead
from lead_agent.domain.scoring import qualify_lead
from lead_agent.infrastructure.observability import reset_request_id, set_request_id
from lead_agent.infrastructure.reasoning import openai_provider
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
    lead = make_lead()
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
            parsed = provider.analyze(lead, icp)
    finally:
        reset_request_id(token)

    request = responses.request
    assert parsed == output
    assert request["extra_headers"] == {"X-Client-Request-Id": "app-request-123"}
    assert request["model"] == "test-model"
    assert request["text_format"] is ReasoningOutput
    user_payload = json.loads(request["input"][1]["content"])
    assert user_payload["normalized_lead"]["email_domain"] == "example.com"
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
