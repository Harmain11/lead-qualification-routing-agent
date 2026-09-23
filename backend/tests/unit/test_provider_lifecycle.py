from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from lead_agent.application.integrations import create_manual_connection, validate_connection
from lead_agent.domain.integrations import ConnectionValidation, ManualConnectionInput
from lead_agent.domain.provider_errors import (
    ProviderOperationError,
    provider_http_error,
    provider_malformed_response_error,
    provider_timeout_error,
)
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    EncryptedProviderSecretORM,
    ProviderLifecycleAuditORM,
)
from lead_agent.infrastructure.encrypted_secrets import EncryptedDatabaseSecretBackend
from lead_agent.infrastructure.provider_lifecycle import WorkspaceAuthorizedConnectionResolver
from lead_agent.infrastructure.repositories import ensure_workspace
from lead_agent.infrastructure.secrets import DevelopmentSecretStore


def _factory(tmp_path, name: str = "lifecycle.db") -> sessionmaker[Session]:
    engine = create_engine(
        f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _hubspot_request(token: str = "access-in-secret-store") -> ManualConnectionInput:
    return ManualConnectionInput(
        account_name="HubSpot lifecycle test",
        auth_type="private_token",
        scopes=["crm.objects.contacts.read"],
        credentials={"access_token": token},
    )


@pytest.mark.parametrize(
    ("status_code", "category", "retryable"),
    [
        (400, "invalid_request", False),
        (401, "invalid_credentials", False),
        (403, "insufficient_permissions", False),
        (404, "not_found", False),
        (409, "conflict", False),
        (429, "rate_limited", True),
        (500, "temporarily_unavailable", True),
    ],
)
def test_provider_http_errors_are_standardized_and_safe(
    status_code: int, category: str, retryable: bool
) -> None:
    error = provider_http_error("example", status_code)
    assert error.category == category
    assert error.retryable is retryable
    assert "access-in-secret-store" not in str(error)


def test_timeout_and_malformed_response_are_distinct() -> None:
    assert provider_timeout_error("example").category == "timeout"
    assert provider_timeout_error("example").retryable is True
    assert provider_malformed_response_error("example").category == "malformed_response"
    assert provider_malformed_response_error("example").retryable is False


def test_encrypted_secret_backend_is_scoped_and_supports_key_rotation(tmp_path) -> None:
    factory = _factory(tmp_path, "secrets.db")
    workspace, foreign_workspace = uuid4(), uuid4()
    with factory() as session:
        ensure_workspace(session, workspace)
        ensure_workspace(session, foreign_workspace)
        session.commit()

    key_v1, key_v2 = b"1" * 32, b"2" * 32
    first = EncryptedDatabaseSecretBackend(
        factory, active_key_version="v1", active_key=key_v1
    )
    reference = first.store(
        workspace, "hubspot", {"access_token": "access-in-secret-store"}
    )
    assert first.retrieve_for_workspace(foreign_workspace, "hubspot", reference) is None
    assert first.retrieve_for_workspace(workspace, "slack", reference) is None

    with factory() as session:
        row = session.scalar(select(EncryptedProviderSecretORM))
        assert row is not None
        assert "access-in-secret-store" not in row.ciphertext
        assert row.key_version == "v1"

    rotated = EncryptedDatabaseSecretBackend(
        factory,
        active_key_version="v2",
        active_key=key_v2,
        previous_keys={"v1": key_v1},
    )
    assert rotated.retrieve_for_workspace(workspace, "hubspot", reference) == {
        "access_token": "access-in-secret-store"
    }
    rotated.replace_for_workspace(
        workspace, "hubspot", reference, {"access_token": "rotated-secret"}
    )
    with factory() as session:
        row = session.scalar(select(EncryptedProviderSecretORM))
        assert row is not None and row.key_version == "v2"
        assert "rotated-secret" not in row.ciphertext


def test_real_validation_marks_connection_verified_and_audits(tmp_path) -> None:
    factory = _factory(tmp_path)
    store = DevelopmentSecretStore()
    workspace = uuid4()

    class Validator:
        def validate(self, provider, credentials, scopes):
            assert provider == "hubspot"
            assert credentials["access_token"] == "access-in-secret-store"
            return ConnectionValidation(
                valid=True,
                verified=True,
                status="connected",
                credential_state="configured",
                message="Provider accepted the credentials.",
            )

    with factory() as session:
        connection = create_manual_connection(
            session, workspace, "hubspot", _hubspot_request(), store
        )
        persisted, result = validate_connection(
            session,
            workspace,
            connection.id,
            store,
            validator=Validator(),
            actor_id="operator-1",
        )
        assert result.verified is True
        assert persisted.status == "verified"
        event = session.scalar(select(ProviderLifecycleAuditORM))
        assert event is not None
        assert event.actor_id == "operator-1"
        assert event.action == "connection_validation"
        assert "access-in-secret-store" not in str(event.details)


@pytest.mark.parametrize(
    ("error", "validation_status", "connection_status"),
    [
        (provider_http_error("hubspot", 401), "invalid_credentials", "needs_reauthorization"),
        (
            provider_http_error("hubspot", 403),
            "insufficient_permissions",
            "error",
        ),
        (provider_http_error("hubspot", 429), "rate_limited", "error"),
        (
            provider_http_error("hubspot", 500),
            "temporarily_unavailable",
            "error",
        ),
        (provider_timeout_error("hubspot"), "temporarily_unavailable", "error"),
        (
            provider_malformed_response_error("hubspot"),
            "malformed_response",
            "error",
        ),
        (
            ProviderOperationError(
                "Provider credential was revoked",
                provider="hubspot",
                code="hubspot_revoked",
                category="revoked",
                retryable=False,
            ),
            "revoked",
            "needs_reauthorization",
        ),
    ],
)
def test_validation_translates_provider_failures_without_raw_responses(
    tmp_path,
    error: ProviderOperationError,
    validation_status: str,
    connection_status: str,
) -> None:
    factory = _factory(tmp_path, f"validation-{validation_status}.db")
    store = DevelopmentSecretStore()
    workspace = uuid4()

    class Validator:
        def validate(self, provider, credentials, scopes):
            del provider, credentials, scopes
            raise error

    with factory() as session:
        connection = create_manual_connection(
            session, workspace, "hubspot", _hubspot_request(), store
        )
        persisted, result = validate_connection(
            session, workspace, connection.id, store, validator=Validator()
        )
        assert result.status == validation_status
        assert persisted.status == connection_status
        assert result.error_code == error.code
        assert "access-in-secret-store" not in str(persisted.last_validation_result)


class _RefreshBroker:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail
        self._lock = Lock()

    def refresh(self, provider: str, refresh_token: str) -> dict[str, str]:
        with self._lock:
            self.calls += 1
        if self.fail:
            raise RuntimeError("provider included sensitive response details")
        assert provider == "hubspot"
        assert refresh_token == "refresh-old"
        return {
            "access_token": "access-new",
            "refresh_token": "refresh-new",
            "expires_in": "3600",
        }


def test_concurrent_expired_token_refreshes_once_and_rotates_secret(tmp_path) -> None:
    factory = _factory(tmp_path, "concurrent.db")
    store = DevelopmentSecretStore()
    broker = _RefreshBroker()
    workspace = uuid4()
    with factory() as session:
        connection = create_manual_connection(
            session,
            workspace,
            "hubspot",
            _hubspot_request("access-old"),
            store,
        )
        reference = connection.secret_reference
        assert reference is not None
        store.replace_for_workspace(
            workspace,
            "hubspot",
            reference,
            {"access_token": "access-old", "refresh_token": "refresh-old"},
        )
        connection.auth_type = "oauth2"
        connection.connection_metadata = {
            "token_expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        }
        session.commit()
        connection_id = connection.id

    resolver = WorkspaceAuthorizedConnectionResolver(store, broker)  # type: ignore[arg-type]

    def resolve() -> str:
        with factory() as session:
            result = resolver.resolve_connection(session, workspace, connection_id)
            assert result is not None
            return result.credentials["access_token"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        tokens = list(executor.map(lambda _: resolve(), range(2)))
    assert tokens == ["access-new", "access-new"]
    assert broker.calls == 1
    assert store.retrieve_for_workspace(workspace, "hubspot", reference) == {
        "access_token": "access-new",
        "refresh_token": "refresh-new",
        "expires_in": "3600",
    }


def test_refresh_failure_is_safe_and_requires_retry(tmp_path) -> None:
    factory = _factory(tmp_path, "refresh-failure.db")
    store = DevelopmentSecretStore()
    workspace = uuid4()
    with factory() as session:
        connection = create_manual_connection(
            session, workspace, "hubspot", _hubspot_request("access-old"), store
        )
        reference = connection.secret_reference
        assert reference is not None
        store.replace_for_workspace(
            workspace,
            "hubspot",
            reference,
            {"access_token": "access-old", "refresh_token": "refresh-old"},
        )
        connection.auth_type = "oauth2"
        connection.connection_metadata = {
            "token_expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        }
        session.commit()
        resolver = WorkspaceAuthorizedConnectionResolver(
            store, _RefreshBroker(fail=True)  # type: ignore[arg-type]
        )
        with pytest.raises(ProviderOperationError) as captured:
            resolver.resolve_connection(session, workspace, connection.id, actor_id="worker-1")
        assert captured.value.code == "provider_refresh_failed"
        assert captured.value.retryable is True
        assert "sensitive response details" not in str(captured.value)
        event = session.scalar(select(ProviderLifecycleAuditORM))
        assert event is not None
        assert event.action == "token_refresh"
        assert event.error_code == "provider_refresh_failed"


def test_secret_reference_swap_is_rejected(tmp_path) -> None:
    factory = _factory(tmp_path, "failure.db")
    store = DevelopmentSecretStore()
    workspace_a, workspace_b = uuid4(), uuid4()
    with factory() as session:
        connection_a = create_manual_connection(
            session, workspace_a, "hubspot", _hubspot_request("token-a"), store
        )
        connection_b = create_manual_connection(
            session, workspace_b, "hubspot", _hubspot_request("token-b"), store
        )
        reference_b = connection_b.secret_reference
        assert reference_b is not None
        connection_a.secret_reference = reference_b
        session.commit()
        with pytest.raises(ProviderOperationError) as captured:
            WorkspaceAuthorizedConnectionResolver(store).resolve_connection(
                session, workspace_a, connection_a.id
            )
        assert captured.value.code == "connection_secret_unavailable"
        assert store.retrieve_for_workspace(workspace_b, "hubspot", reference_b) == {
            "access_token": "token-b"
        }
