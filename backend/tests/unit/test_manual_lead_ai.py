from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from lead_agent.api.routes import leads as lead_routes
from lead_agent.domain.analysis import ReasoningProviderTimeout, ReasoningProviderUnavailable
from lead_agent.infrastructure.reasoning.deterministic import DeterministicReasoningProvider


class WorkspaceAiProvider:
    provider_name = "openai"
    model_name = "test-model"
    prompt_version = "test-v1"


class CreateRequest:
    def model_dump(self, *, mode: str) -> dict[str, str]:
        assert mode == "json"
        return {
            "source": "website",
            "source_detail": "manual-control-center",
            "message": "Discovery completed; technical review is next.",
        }


def _request_args():
    return {
        "request": CreateRequest(),
        "session": object(),
        "policies": (None, None),
        "provider": None,
        "notification_provider": object(),
        "workspace": SimpleNamespace(workspace_id=uuid4()),
    }


def test_manual_lead_create_requires_a_workspace_ai_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        lead_routes,
        "resolve_workspace_reasoning_provider",
        lambda *_args: DeterministicReasoningProvider(),
    )

    with pytest.raises(HTTPException) as error:
        lead_routes._process_create_request(**_request_args())

    assert error.value.status_code == 503
    assert "Connect and verify a workspace AI provider" in error.value.detail
    assert "The lead was not created" in error.value.detail


def test_manual_lead_create_passes_workspace_ai_and_context_analysis_flags(
    monkeypatch,
) -> None:
    provider = WorkspaceAiProvider()
    row = SimpleNamespace(id=uuid4())
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        lead_routes,
        "resolve_workspace_reasoning_provider",
        lambda *_args: provider,
    )

    def fake_process_lead(*args, **kwargs):
        captured["payload"] = args[1]
        captured.update(kwargs)
        return object(), object(), row

    monkeypatch.setattr(lead_routes, "process_lead", fake_process_lead)
    monkeypatch.setattr(lead_routes, "get_current_routing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        lead_routes, "get_current_crm_sync", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        lead_routes, "_current_confirmation_email", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        lead_routes, "lead_manual_review_reasons", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(lead_routes, "lead_response", lambda *_args, **_kwargs: "response")

    response = lead_routes._process_create_request(**_request_args())

    assert response == "response"
    assert captured["provider"] is provider
    assert captured["require_provider_success"] is True
    assert captured["analyze_hard_disqualified"] is True
    assert captured["payload"]["source_detail"] == "manual-control-center"
    assert captured["payload"]["message"] == "Discovery completed; technical review is next."


def test_manual_lead_create_reports_ai_failures_without_creating_lead(monkeypatch) -> None:
    provider = WorkspaceAiProvider()
    monkeypatch.setattr(
        lead_routes,
        "resolve_workspace_reasoning_provider",
        lambda *_args: provider,
    )

    def fail_analysis(*_args, **_kwargs):
        raise ReasoningProviderUnavailable("AI provider is unavailable")

    monkeypatch.setattr(lead_routes, "process_lead", fail_analysis)

    with pytest.raises(HTTPException) as error:
        lead_routes._process_create_request(**_request_args())

    assert error.value.status_code == 503
    assert "AI analysis failed" in error.value.detail
    assert "the lead was not created" in error.value.detail


def test_required_provider_failure_stops_before_persistence(monkeypatch) -> None:
    from lead_agent.application import governance, services

    provider_error = SimpleNamespace(
        status="provider_error",
        error_code=ReasoningProviderTimeout.code,
        error_message="The AI request timed out.",
    )
    monkeypatch.setattr(governance, "assert_residency", lambda *_args: None)
    monkeypatch.setattr(
        services, "operations_policy", lambda *_args: SimpleNamespace(business_profile=None)
    )
    monkeypatch.setattr(
        services,
        "resolve_workspace_icp",
        lambda *_args: SimpleNamespace(
            configuration=object(),
            source="system",
            trace_result=lambda result: result,
        ),
    )
    monkeypatch.setattr(services, "normalize_lead", lambda *_args: object())
    monkeypatch.setattr(services, "workspace_manual_review_config", lambda *_args: object())
    monkeypatch.setattr(
        services,
        "_analyze_and_qualify",
        lambda *_args, **_kwargs: (provider_error, object()),
    )
    monkeypatch.setattr(
        services,
        "create_lead_with_result",
        lambda *_args, **_kwargs: pytest.fail("lead must not persist before AI succeeds"),
    )

    with pytest.raises(ReasoningProviderTimeout, match="timed out"):
        services.process_lead(
            session=object(),
            payload={},
            icp=object(),
            routing=SimpleNamespace(manual_review=object()),
            provider=WorkspaceAiProvider(),
            workspace_id=uuid4(),
            require_provider_success=True,
            analyze_hard_disqualified=True,
        )
