from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from lead_agent.api.schemas import provider_connection_response
from lead_agent.application.integrations import (
    create_manual_connection,
    disconnect_connection,
    reconnect_connection,
    update_openai_model,
    validate_connection,
)
from lead_agent.domain.integrations import ConnectionValidation, ManualConnectionInput
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import ProviderLifecycleAuditORM
from lead_agent.infrastructure.reasoning import factory, openai_connection
from lead_agent.infrastructure.reasoning.configuration import validate_openai_model
from lead_agent.infrastructure.reasoning.deterministic import DeterministicReasoningProvider
from lead_agent.infrastructure.reasoning.factory import resolve_workspace_reasoning_provider
from lead_agent.infrastructure.reasoning.openai_connection import OpenAIConnectionValidator
from lead_agent.infrastructure.provider_lifecycle import WorkspaceAuthorizedConnectionResolver
from lead_agent.infrastructure.secrets import DevelopmentSecretStore
from lead_agent.settings.config import Settings

API_KEY_A = "sk-proj-workspace-a-secret"
API_KEY_B = "sk-proj-workspace-b-secret"
MODEL_A = "gpt-4.1-mini"
MODEL_B = "gpt-4.1"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        llm_provider="workspace",
        openai_supported_models=f"{MODEL_A},{MODEL_B}",
        openai_default_model=MODEL_A,
    )


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'openai-byok.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _request(api_key: str, model: str = MODEL_A) -> ManualConnectionInput:
    return ManualConnectionInput(
        account_name="Dedicated OpenAI project",
        auth_type="api_key",
        scopes=["Responses API access"],
        credentials={"api_key": api_key},
        metadata={"selected_model": model},
    )


def test_openai_key_is_workspace_scoped_never_returned_and_audited(tmp_path: Path) -> None:
    sessions = _factory(tmp_path)
    store = DevelopmentSecretStore()
    workspace = uuid4()
    with sessions() as session:
        connection = create_manual_connection(
            session,
            workspace,
            "openai",
            _request(API_KEY_A),
            store,
            actor_id="owner-a",
        )
        payload = provider_connection_response(connection).model_dump(mode="json")
        rendered = str(payload)
        assert payload["configured"] is True
        assert payload["selected_model"] == MODEL_A
        assert API_KEY_A not in rendered
        assert connection.secret_reference not in rendered
        assert "ciphertext" not in rendered
        assert store.retrieve_for_workspace(
            workspace, "openai", connection.secret_reference or ""
        ) == {"api_key": API_KEY_A}
        assert (
            store.retrieve_for_workspace(
                uuid4(), "openai", connection.secret_reference or ""
            )
            is None
        )

        audit = session.scalar(
            select(ProviderLifecycleAuditORM).where(
                ProviderLifecycleAuditORM.action == "openai_credential_configured"
            )
        )
        assert audit is not None
        assert audit.actor_id == "owner-a"
        assert audit.details == {"selected_model": MODEL_A}
        assert API_KEY_A not in str(audit.details)


@pytest.mark.parametrize("padding", [" ", "\r\n\t", "\u00a0"])
def test_openai_setup_and_replacement_trim_boundary_whitespace(
    tmp_path: Path, padding: str,
) -> None:
    sessions = _factory(tmp_path)
    store = DevelopmentSecretStore()
    with sessions() as session:
        for _ in range(2):
            workspace = uuid4()
            request = _request(f"{padding}{API_KEY_A}{padding}")
            connection = create_manual_connection(
                session, workspace, "openai", request, store
            )
            reference = connection.secret_reference or ""
            assert store.retrieve_for_workspace(workspace, "openai", reference) == {
                "api_key": API_KEY_A
            }
            # Do not mutate caller-owned input while normalizing the stored key.
            assert request.credentials["api_key"] == f"{padding}{API_KEY_A}{padding}"
            reconnect_connection(
                session, workspace, connection.id,
                {"api_key": f"{padding}{API_KEY_B}{padding}"}, store,
            )
            assert store.retrieve_for_workspace(workspace, "openai", reference) == {
                "api_key": API_KEY_B
            }


@pytest.mark.parametrize("separator", [" ", "\n", "\t", "\u00a0"])
def test_openai_malformed_paste_does_not_replace_existing_key(
    tmp_path: Path, separator: str,
) -> None:
    sessions = _factory(tmp_path)
    store = DevelopmentSecretStore()
    malformed_key = f"{API_KEY_A}{separator}extra"
    with sessions() as session:
        workspace = uuid4()
        with pytest.raises(ValueError, match="contains spaces or line breaks") as error:
            create_manual_connection(
                session, workspace, "openai", _request(malformed_key), store
            )
        assert malformed_key not in str(error.value)
        connection = create_manual_connection(
            session, workspace, "openai", _request(API_KEY_A), store
        )
        with pytest.raises(ValueError, match="contains spaces or line breaks"):
            reconnect_connection(
                session, workspace, connection.id, {"api_key": malformed_key}, store
            )
        assert store.retrieve_for_workspace(
            workspace, "openai", connection.secret_reference or ""
        ) == {"api_key": API_KEY_A}


def test_openai_replace_model_test_and_disconnect_have_safe_audits(tmp_path: Path) -> None:
    sessions = _factory(tmp_path)
    store = DevelopmentSecretStore()
    workspace = uuid4()

    class Validator:
        def validate(self, provider, credentials, scopes):
            assert provider == "openai"
            assert credentials == {"api_key": API_KEY_B, "model": MODEL_B}
            assert scopes == ["Responses API access"]
            return ConnectionValidation(
                valid=True,
                verified=True,
                status="connected",
                credential_state="configured",
                message="OpenAI Responses API and structured output access verified.",
                metadata={"selected_model": MODEL_B, "responses_api": True},
            )

    with sessions() as session:
        connection = create_manual_connection(
            session, workspace, "openai", _request(API_KEY_A), store, actor_id="owner-a"
        )
        secret_reference = connection.secret_reference or ""
        replaced = reconnect_connection(
            session,
            workspace,
            connection.id,
            {"api_key": API_KEY_B},
            store,
            actor_id="owner-a",
        )
        assert replaced.status == "connected"
        configured = update_openai_model(
            session, workspace, connection.id, MODEL_B, actor_id="owner-a"
        )
        assert configured.connection_metadata["selected_model"] == MODEL_B
        verified, result = validate_connection(
            session,
            workspace,
            connection.id,
            store,
            validator=Validator(),
            actor_id="owner-a",
        )
        assert result.verified is True
        assert verified.status == "verified"
        disconnected = disconnect_connection(
            session, workspace, connection.id, store, actor_id="owner-a"
        )
        assert disconnected.status == "disconnected"
        assert disconnected.secret_reference is None
        assert store.retrieve_for_workspace(workspace, "openai", secret_reference) is None

        events = list(
            session.scalars(
                select(ProviderLifecycleAuditORM).where(
                    ProviderLifecycleAuditORM.connection_id == connection.id
                )
            )
        )
        actions = {event.action for event in events}
        assert {
            "openai_credential_configured",
            "openai_credential_replaced",
            "openai_model_changed",
            "openai_test_connection",
            "openai_connection_verified",
            "openai_disconnected",
        } <= actions
        assert API_KEY_A not in str(events)
        assert API_KEY_B not in str(events)


def test_openai_connection_retest_preserves_auth_failure_after_error_state(
    tmp_path: Path,
) -> None:
    sessions = _factory(tmp_path)
    store = DevelopmentSecretStore()
    workspace = uuid4()

    class AuthenticationFailure:
        def validate(self, provider, credentials, scopes):
            assert provider == "openai"
            assert credentials == {"api_key": API_KEY_A, "model": MODEL_A}
            assert scopes == ["Responses API access"]
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="invalid_credentials",
                credential_state="invalid",
                message="OpenAI rejected the workspace credential.",
                error_code="openai_invalid_credentials",
            )

    with sessions() as session:
        connection = create_manual_connection(
            session, workspace, "openai", _request(API_KEY_A), store
        )
        connection.status = "error"
        connection.credential_state = "invalid"
        connection.is_active = False
        session.commit()

        persisted, result = validate_connection(
            session,
            workspace,
            connection.id,
            store,
            validator=AuthenticationFailure(),
        )

        assert result.status == "invalid_credentials"
        assert result.error_code == "openai_invalid_credentials"
        assert persisted.status == "needs_reauthorization"
        assert persisted.last_validation_result["message"] == (
            "OpenAI rejected the workspace credential."
        )


def test_workspace_b_cannot_replace_test_or_reconfigure_workspace_a(tmp_path: Path) -> None:
    sessions = _factory(tmp_path)
    store = DevelopmentSecretStore()
    workspace_a, workspace_b = uuid4(), uuid4()
    with sessions() as session:
        connection = create_manual_connection(
            session, workspace_a, "openai", _request(API_KEY_A), store
        )
        with pytest.raises(LookupError):
            reconnect_connection(
                session,
                workspace_b,
                connection.id,
                {"api_key": API_KEY_B},
                store,
            )
        with pytest.raises(LookupError):
            update_openai_model(session, workspace_b, connection.id, MODEL_B)
        with pytest.raises(LookupError):
            validate_connection(
                session,
                workspace_b,
                connection.id,
                store,
                validator=OpenAIConnectionValidator(timeout_seconds=1),
            )
        assert store.retrieve_for_workspace(
            workspace_a, "openai", connection.secret_reference or ""
        ) == {"api_key": API_KEY_A}


def test_server_model_allowlist_rejects_arbitrary_values_before_storage(tmp_path: Path) -> None:
    sessions = _factory(tmp_path)
    store = DevelopmentSecretStore()
    with pytest.raises(ValueError, match="not supported"):
        validate_openai_model("user-invented-model", _settings())
    with sessions() as session, pytest.raises(ValueError, match="not supported"):
        create_manual_connection(
            session,
            uuid4(),
            "openai",
            _request(API_KEY_A, "user-invented-model"),
            store,
        )


def test_concurrent_workspace_resolution_never_crosses_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = _factory(tmp_path)
    store = DevelopmentSecretStore()
    workspace_a, workspace_b = uuid4(), uuid4()
    with sessions() as session:
        connection_a = create_manual_connection(
            session, workspace_a, "openai", _request(API_KEY_A, MODEL_A), store
        )
        connection_b = create_manual_connection(
            session, workspace_b, "openai", _request(API_KEY_B, MODEL_B), store
        )
        connection_a.status = connection_b.status = "verified"
        session.commit()

    class CapturedProvider:
        provider_name = "openai"
        prompt_version = "test"

        def __init__(self, *, api_key, model, **kwargs):
            del kwargs
            self.api_key = api_key
            self.model_name = model

    monkeypatch.setattr(factory, "OpenAIReasoningProvider", CapturedProvider)

    def resolve(workspace_id: UUID) -> tuple[str, str]:
        with sessions() as session:
            provider = resolve_workspace_reasoning_provider(
                session,
                workspace_id,
                settings=_settings(),
                secret_store=store,
            )
            return provider.api_key, provider.model_name  # type: ignore[attr-defined]

    with ThreadPoolExecutor(max_workers=2) as pool:
        resolved = list(pool.map(resolve, [workspace_a, workspace_b]))
    assert resolved == [(API_KEY_A, MODEL_A), (API_KEY_B, MODEL_B)]

    with sessions() as session:
        fallback = resolve_workspace_reasoning_provider(
            session,
            uuid4(),
            settings=_settings(),
            secret_store=store,
        )
    assert isinstance(fallback, DeterministicReasoningProvider)


class _Responses:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def parse(self, **kwargs):
        assert kwargs["store"] is False
        assert kwargs["max_output_tokens"] == 16
        if self.error:
            raise self.error
        return SimpleNamespace(output_parsed=self.result)


class _Client:
    def __init__(self, responses: _Responses) -> None:
        self.responses = responses


def _patch_openai(monkeypatch: pytest.MonkeyPatch, responses: _Responses) -> None:
    monkeypatch.setattr(
        openai_connection,
        "import_module",
        lambda _name: SimpleNamespace(OpenAI=lambda **_kwargs: _Client(responses)),
    )


def test_openai_connection_probe_uses_responses_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_openai(monkeypatch, _Responses({"status": "ok"}))
    result = OpenAIConnectionValidator(timeout_seconds=1).validate(
        "openai", {"api_key": API_KEY_A, "model": MODEL_A}, []
    )
    assert result.verified is True
    assert result.metadata["responses_api"] is True
    assert result.metadata["structured_output"] is True


@pytest.mark.parametrize(
    ("class_name", "status_code", "code", "expected_status", "retryable"),
    [
        ("AuthenticationError", 401, None, "invalid_credentials", False),
        ("PermissionDeniedError", 403, None, "insufficient_permissions", False),
        ("RateLimitError", 429, "credit_balance_exhausted", "quota_exhausted", False),
        ("NotFoundError", 404, "model_not_found", "model_unavailable", False),
        ("RateLimitError", 429, "rate_limit_exceeded", "rate_limited", True),
        ("InternalServerError", 500, None, "provider_outage", True),
        ("APIConnectionError", None, None, "transport_failure", True),
    ],
)
def test_openai_connection_failures_are_classified_without_raw_errors(
    monkeypatch: pytest.MonkeyPatch,
    class_name: str,
    status_code: int | None,
    code: str | None,
    expected_status: str,
    retryable: bool,
) -> None:
    error_type = type(class_name, (Exception,), {})
    error = error_type("raw provider detail containing secret")
    error.status_code = status_code  # type: ignore[attr-defined]
    error.code = code  # type: ignore[attr-defined]
    _patch_openai(monkeypatch, _Responses(error=error))
    result = OpenAIConnectionValidator(timeout_seconds=1).validate(
        "openai", {"api_key": API_KEY_A, "model": MODEL_A}, []
    )
    assert result.status == expected_status
    assert result.retryable is retryable
    assert "raw provider detail" not in result.message
    assert API_KEY_A not in result.message


def test_frontend_masks_and_does_not_persist_openai_key() -> None:
    source = (
        Path(__file__).parents[3] / "frontend" / "src" / "views" / "IntegrationsView.tsx"
    ).read_text(encoding="utf-8")
    assert 'type="password"' in source
    assert 'autoComplete="new-password"' in source
    assert "setCredentials((current) => ({ ...current, [provider]: {} }))" in source
    assert "localStorage" not in source
    assert "sessionStorage" not in source
