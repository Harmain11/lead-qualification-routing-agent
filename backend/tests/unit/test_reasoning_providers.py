from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from lead_agent.api.schemas import provider_connection_response
from lead_agent.application.integrations import (
    activate_reasoning_provider,
    create_manual_connection,
    validate_connection,
)
from lead_agent.domain.analysis import (
    AnalyzedField,
    ContextFields,
    IcpCriterionEvaluation,
    IcpEvaluation,
    ReasoningOutput,
    SalesSummary,
)
from lead_agent.domain.configuration import IcpConfig
from lead_agent.domain.integrations import ConnectionValidation, ManualConnectionInput
from lead_agent.domain.models import NormalizedLead
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.reasoning import factory
from lead_agent.infrastructure.reasoning.configuration import validate_reasoning_model
from lead_agent.infrastructure.reasoning.structured_api import (
    StructuredAPIConnectionValidator,
    StructuredAPIReasoningProvider,
)
from lead_agent.infrastructure.secrets import DevelopmentSecretStore
from lead_agent.settings.config import Settings

_KEYS = {
    "gemini": "gemini-workspace-key-not-for-logs",
    "claude": "claude-workspace-key-not-for-logs",
}
_MODELS = {"gemini": "gemini-3.8-flash", "claude": "claude-sonnet-4-6"}


def _reasoning_output() -> dict[str, object]:
    criterion = IcpCriterionEvaluation(
        status="unknown", confidence=0, rationale="No matching evidence was supplied."
    )
    context = ContextFields.model_validate(
        {
            name: AnalyzedField()
            for name in ContextFields.model_fields
        }
    )
    return ReasoningOutput(
        context=context,
        intent_level="unknown",
        overall_confidence=0,
        icp_evaluation=IcpEvaluation(
            profile_name="Default",
            profile_version="1",
            industry_fit=criterion,
            company_size_fit=criterion,
            geography_fit=criterion,
            buyer_role_fit=criterion,
            budget_fit=criterion,
            pain_point_relevance=criterion,
        ),
        summary=SalesSummary(
            who="Unknown",
            company_context="Unknown",
            need="Unknown",
            pain_point="Unknown",
            urgency="Unknown",
            buying_signal="Unknown",
            potential_concern="Unknown",
        ),
        recommended_action="Review the available lead details.",
    ).model_dump(mode="json")


def _provider_payload(provider: str) -> dict[str, object]:
    text = json.dumps(_reasoning_output())
    if provider == "gemini":
        return {
            "candidates": [{"content": {"parts": [{"text": text}]}}],
            "usageMetadata": {
                "promptTokenCount": 11,
                "candidatesTokenCount": 22,
                "totalTokenCount": 33,
            },
        }
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 11, "output_tokens": 22},
    }


def _lead() -> NormalizedLead:
    return NormalizedLead(
        id=uuid4(),
        source="web",
        source_detail=None,
        external_id=None,
        full_name="Avery Smith",
        email="avery@example.test",
        phone=None,
        job_title="Director",
        company_name="Example Co",
        industry="Software",
        employee_count=50,
        annual_revenue=Decimal("1000000"),
        geography="North America",
        budget=Decimal("50000"),
        product_or_service="Sales software",
        message="Please send information about the platform.",
        received_at=datetime.now(UTC),
    )


def _request(
    provider: str, *, api_key: str | None = None, activate: bool = True
) -> ManualConnectionInput:
    return ManualConnectionInput(
        account_name=f"{provider.title()} workspace",
        auth_type="api_key",
        scopes=[],
        credentials={"api_key": api_key or _KEYS[provider]},
        metadata={"selected_model": _MODELS[provider]},
        activate=activate,
    )


def _session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'reasoning-providers.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.mark.parametrize("provider", ["gemini", "claude"])
def test_structured_api_analysis_uses_vendor_schema_and_keeps_key_out_of_logs(
    provider: str, caplog: pytest.LogCaptureFixture
) -> None:
    requests: list[dict[str, object]] = []

    def post(url: str, **kwargs: object) -> SimpleNamespace:
        requests.append({"url": url, **kwargs})
        return SimpleNamespace(status_code=200, json=lambda: _provider_payload(provider))

    client = StructuredAPIReasoningProvider(
        provider=provider,
        api_key=_KEYS[provider],
        model=_MODELS[provider],
        timeout_seconds=3,
        post=post,
    )
    result = client.analyze(_lead(), IcpConfig(profile_name="Default", profile_version="1"))

    assert result.intent_level == "unknown"
    assert len(requests) == 1
    request = requests[0]
    assert _KEYS[provider] not in str(request["url"])
    assert _KEYS[provider] not in str(request["json"])
    assert _KEYS[provider] not in caplog.text
    assert isinstance(request["headers"], dict)
    headers = request["headers"]
    assert headers.get("x-goog-api-key" if provider == "gemini" else "x-api-key") == _KEYS[provider]
    body = request["json"]
    assert isinstance(body, dict)
    if provider == "gemini":
        assert str(request["url"]).endswith(f"{_MODELS[provider]}:generateContent")
        schema = body["generationConfig"]["responseSchema"]
    else:
        assert request["url"] == "https://api.anthropic.com/v1/messages"
        assert body["model"] == _MODELS[provider]
        schema = body["output_config"]["format"]["schema"]
    assert schema["type"] == "object"
    assert "$defs" not in schema


@pytest.mark.parametrize("provider", ["gemini", "claude"])
def test_structured_api_connection_validator_verifies_key_and_returns_no_secret(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, object]] = []

    def post(url: str, **kwargs: object) -> SimpleNamespace:
        seen.append({"url": url, **kwargs})
        return SimpleNamespace(
            status_code=200,
            json=lambda: (
                {"candidates": [{"content": {"parts": [{"text": '{"status":"ok"}'}]}}]}
                if provider == "gemini"
                else {"content": [{"type": "text", "text": '{"status":"ok"}'}]}
            ),
        )

    monkeypatch.setattr(
        "lead_agent.infrastructure.reasoning.structured_api.httpx.post", post
    )
    result = StructuredAPIConnectionValidator(provider, timeout_seconds=2).validate(
        provider,
        {"api_key": _KEYS[provider], "model": _MODELS[provider]},
        [],
    )
    assert result.verified is True
    assert result.metadata["selected_model"] == _MODELS[provider]
    assert _KEYS[provider] not in result.model_dump_json()
    assert len(seen) == 1
    assert _KEYS[provider] not in str(seen[0]["url"])


@pytest.mark.parametrize("provider", ["gemini", "claude"])
def test_reasoning_model_allowlist_rejects_unconfigured_models(provider: str) -> None:
    assert validate_reasoning_model(provider, _MODELS[provider]) == _MODELS[provider]
    with pytest.raises(ValueError, match="not supported"):
        validate_reasoning_model(provider, "user-supplied-arbitrary-model")


@pytest.mark.parametrize("provider", ["gemini", "claude"])
def test_connection_validator_does_not_echo_rejected_api_key(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def post(url: str, **kwargs: object) -> SimpleNamespace:
        del url, kwargs
        return SimpleNamespace(status_code=401, json=lambda: {"error": _KEYS[provider]})

    monkeypatch.setattr(
        "lead_agent.infrastructure.reasoning.structured_api.httpx.post", post
    )
    result = StructuredAPIConnectionValidator(provider, timeout_seconds=2).validate(
        provider,
        {"api_key": _KEYS[provider], "model": _MODELS[provider]},
        [],
    )
    assert result.status == "invalid_credentials"
    assert _KEYS[provider] not in result.model_dump_json()


def test_workspace_resolution_isolated_and_verified_provider_can_be_switched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = _session_factory(tmp_path)
    secret_store = DevelopmentSecretStore()
    workspace_a, workspace_b = uuid4(), uuid4()
    workspace_b_key = "claude-workspace-b-distinct-key"

    with sessions() as session:
        gemini_a = create_manual_connection(
            session, workspace_a, "gemini", _request("gemini"), secret_store
        )
        claude_b = create_manual_connection(
            session,
            workspace_b,
            "claude",
            _request("claude", api_key=workspace_b_key),
            secret_store,
        )
        gemini_a.status = claude_b.status = "verified"
        session.commit()
        selected = activate_reasoning_provider(session, workspace_a, gemini_a.id)
        assert selected.is_active
        assert provider_connection_response(selected).configured is True
        claude_a = create_manual_connection(
            session, workspace_a, "claude", _request("claude"), secret_store
        )
        claude_a.status = "verified"
        session.commit()
        activate_reasoning_provider(session, workspace_a, claude_a.id)
        session.refresh(gemini_a)
        assert gemini_a.is_active is False
        assert claude_a.is_active is True

    class CapturedProvider:
        provider_name = "captured"
        model_name = ""
        api_key = ""

        def __init__(self, **kwargs: object) -> None:
            self.provider_name = str(kwargs["provider"])
            self.model_name = str(kwargs["model"])
            self.api_key = str(kwargs["api_key"])

    monkeypatch.setattr(factory, "StructuredAPIReasoningProvider", CapturedProvider)
    settings = Settings(_env_file=None, app_env="test", llm_provider="workspace")

    with sessions() as session:
        resolved_a = factory.resolve_workspace_reasoning_provider(
            session, workspace_a, settings=settings, secret_store=secret_store
        )
        resolved_b = factory.resolve_workspace_reasoning_provider(
            session, workspace_b, settings=settings, secret_store=secret_store
        )
    assert (resolved_a.provider_name, resolved_a.model_name, resolved_a.api_key) == (
        "claude",
        _MODELS["claude"],
        _KEYS["claude"],
    )
    assert (resolved_b.provider_name, resolved_b.model_name, resolved_b.api_key) == (
        "claude",
        _MODELS["claude"],
        workspace_b_key,
    )


def test_failed_provider_verification_preserves_the_current_workspace_provider(
    tmp_path: Path,
) -> None:
    sessions = _session_factory(tmp_path)
    store = DevelopmentSecretStore()
    workspace = uuid4()

    class RejectedCredentials:
        def validate(self, provider, credentials, scopes):
            assert provider == "claude"
            assert credentials["api_key"] == _KEYS["claude"]
            assert scopes == []
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="invalid_credentials",
                credential_state="invalid",
                message="The provider rejected the API key.",
            )

    with sessions() as session:
        gemini = create_manual_connection(
            session, workspace, "gemini", _request("gemini"), store
        )
        gemini.status = "verified"
        session.commit()
        activate_reasoning_provider(session, workspace, gemini.id)
        claude = create_manual_connection(
            session, workspace, "claude", _request("claude"), store
        )
        assert claude.is_active
        _, validation = validate_connection(
            session,
            workspace,
            claude.id,
            store,
            validator=RejectedCredentials(),
        )
        session.refresh(gemini)
        session.refresh(claude)
        assert validation.status == "invalid_credentials"
        assert gemini.is_active is True
        assert claude.is_active is False
