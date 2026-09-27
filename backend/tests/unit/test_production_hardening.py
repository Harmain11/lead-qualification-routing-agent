from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event, Lock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.responses import Response

from lead_agent.api.routes.auth import _set_session_cookies
from lead_agent.application.auth import IssuedSession, hash_password, verify_password
from lead_agent.application.integrations import (
    begin_oauth_connection,
    complete_oauth_connection,
)
from lead_agent.domain.auth import AuthenticatedIdentity
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.oauth import DevelopmentOAuthBroker, pkce_challenge
from lead_agent.infrastructure.secrets import DevelopmentSecretStore, ManagedSecretStore
from lead_agent.settings.config import Settings


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "log_format": "json",
        "auth_development_fallback": False,
        "auth_cookie_secure": True,
        "secret_key": "s" * 48,
        "webhook_signing_secret": "w" * 48,
        "secret_store_provider": "managed",
        "managed_secret_store_backend": "deployment.secrets:create_backend",
        "oauth_broker": "provider",
        "llm_provider": "workspace",
        "crm_provider": "hubspot",
        "hubspot_access_token": "production-hubspot-token",
        "hubspot_client_id": "production-hubspot-client",
        "hubspot_client_secret": "production-hubspot-client-secret",
        "notification_provider": "slack",
        "slack_bot_token": "production-slack-token",
        "slack_channel_id": "C0123456789",
        "slack_client_id": "production-slack-client",
        "slack_client_secret": "production-slack-client-secret",
        "database_url": "postgresql+psycopg://app@db.internal/lead_agent",
        "database_ssl_mode": "require",
        "redis_url": "rediss://redis.internal:6380/0",
        "rate_limit_backend": "redis",
        "cors_allowed_origins": "https://app.example.com",
        "integration_public_base_url": "https://api.example.com",
        "frontend_public_base_url": "https://app.example.com",
        "demo_data_enabled": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_production_configuration_fails_closed_on_development_defaults() -> None:
    with pytest.raises(ValidationError) as error:
        Settings(app_env="production", _env_file=None)
    message = str(error.value)
    assert "AUTH_DEVELOPMENT_FALLBACK must be false" in message
    assert "AUTH_COOKIE_SECURE must be true" in message
    assert "SECRET_STORE_PROVIDER must be managed" in message
    assert "LLM_PROVIDER must be workspace or deterministic" not in message
    assert "CRM_PROVIDER cannot be mock" in message
    assert "NOTIFICATION_PROVIDER cannot be mock" in message


def test_valid_production_configuration_has_explicit_security_boundaries() -> None:
    settings = production_settings()
    assert settings.app_env == "production"
    assert settings.cors_origins == ["https://app.example.com"]
    assert settings.secret_store_provider == "managed"


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"auth_development_fallback": True}, "AUTH_DEVELOPMENT_FALLBACK"),
        ({"cors_allowed_origins": "*"}, "wildcard production CORS"),
        ({"secret_store_provider": "development"}, "SECRET_STORE_PROVIDER"),
        ({"llm_provider": "openai"}, "LLM_PROVIDER"),
        ({"demo_data_enabled": True}, "DEMO_DATA_ENABLED"),
        ({"salesforce_login_base_url": "http://login.salesforce.com"}, "SALESFORCE_LOGIN_BASE_URL"),
        ({"hubspot_api_base_url": "https://127.0.0.1"}, "HUBSPOT_API_BASE_URL"),
    ],
)
def test_production_rejects_insecure_overrides(override: dict[str, object], expected: str) -> None:
    with pytest.raises(ValidationError, match=expected):
        production_settings(**override)


def test_production_session_cookie_is_secure_bounded_and_http_only() -> None:
    now = datetime.now(UTC)
    identity = AuthenticatedIdentity(
        user_id=uuid4(),
        email="owner@example.test",
        display_name="Owner",
        session_id=uuid4(),
        current_workspace_id=uuid4(),
    )
    issued = IssuedSession(identity, "opaque-session", "csrf-token", now + timedelta(hours=1))
    response = Response()
    _set_session_cookies(response, issued, production_settings(auth_session_hours=1))
    cookies = [
        value.decode() for key, value in response.raw_headers if key.lower() == b"set-cookie"
    ]
    session_cookie = next(value for value in cookies if value.startswith("lead_agent_session="))
    csrf_cookie = next(value for value in cookies if value.startswith("lead_agent_csrf="))
    assert "HttpOnly" in session_cookie
    assert "Secure" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert "Max-Age=3600" in session_cookie
    assert "expires=" in session_cookie.lower()
    assert "HttpOnly" not in csrf_cookie
    assert "Secure" in csrf_cookie


def test_password_hashes_use_unique_salts_and_never_contain_plaintext() -> None:
    password = "correct-horse-battery-staple"
    first = hash_password(password)
    second = hash_password(password)
    assert first != second
    assert first.startswith("scrypt$16384$8$5$")
    assert password not in first
    assert verify_password(password, first)
    assert not verify_password("incorrect-password", first)


def test_existing_scrypt_hashes_remain_verifiable_after_work_factor_increase() -> None:
    password = "existing-account-password"
    salt = bytes.fromhex("00112233445566778899aabbccddeeff")
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    encoded = f"scrypt$16384$8$1${salt.hex()}${derived.hex()}"
    assert verify_password(password, encoded)


def test_development_secret_lifecycle_replaces_invalidates_and_deletes() -> None:
    store = DevelopmentSecretStore()
    reference = store.store(uuid4(), "hubspot", {"access_token": "initial"})
    assert store.retrieve(reference) == {"access_token": "initial"}
    assert store.replace(reference, {"access_token": "rotated"}) == reference
    assert store.retrieve(reference) == {"access_token": "rotated"}
    store.invalidate(reference)
    assert store.retrieve(reference) is None
    store.delete(reference)
    assert store.retrieve(reference) is None


def test_managed_secret_store_persists_only_opaque_reference() -> None:
    class Backend:
        def __init__(self) -> None:
            self.values: dict[str, dict[str, str]] = {}
            self.owners = {}

        def store(self, workspace_id, provider, values):
            reference = f"managed://secret/{uuid4()}"
            self.values[reference] = dict(values)
            self.owners[reference] = (workspace_id, provider)
            return reference

        def retrieve(self, reference):
            return self.values.get(reference)

        def retrieve_for_workspace(self, workspace_id, provider, reference):
            if self.owners.get(reference) != (workspace_id, provider):
                return None
            return self.retrieve(reference)

        def replace(self, reference, values):
            self.values[reference] = dict(values)
            return reference

        def replace_for_workspace(self, workspace_id, provider, reference, values):
            if self.owners.get(reference) != (workspace_id, provider):
                raise KeyError(reference)
            return self.replace(reference, values)

        def invalidate(self, reference):
            self.values.pop(reference, None)

        def delete(self, reference):
            self.values.pop(reference, None)
            self.owners.pop(reference, None)

    backend = Backend()
    store = ManagedSecretStore(backend)
    reference = store.store(uuid4(), "openai", {"api_key": "not-in-reference"})
    assert reference.startswith("managed://")
    assert "not-in-reference" not in reference
    assert store.retrieve(reference) == {"api_key": "not-in-reference"}


def test_pkce_uses_rfc7636_s256_challenge() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_oauth_state_is_actor_bound(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'actor-state.db'}")
    Base.metadata.create_all(engine)
    store = DevelopmentSecretStore()
    broker = DevelopmentOAuthBroker()
    workspace_id = uuid4()
    with Session(engine) as session:
        connection, started = begin_oauth_connection(
            session,
            workspace_id,
            "hubspot",
            "HubSpot account",
            [],
            broker,
            store,
            "http://localhost:8000",
            "actor-one",
        )
        with pytest.raises(ValueError, match="different actor"):
            complete_oauth_connection(
                session,
                workspace_id,
                connection.id,
                started.state,
                "authorization-code",
                store,
                broker,
                "actor-two",
            )
        session.refresh(connection)
        assert connection.status == "awaiting_authorization"
        assert len(started.state.split(".", 1)[1]) >= 40
        assert started.state not in str(connection.connection_metadata)


def test_oauth_without_expiry_uses_shared_refresh_lifetime(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'oauth-expiry.db'}")
    Base.metadata.create_all(engine)
    store = DevelopmentSecretStore()
    workspace_id = uuid4()

    class NoExpiryBroker(DevelopmentOAuthBroker):
        def exchange(self, provider, authorization_code, code_verifier, redirect_uri):
            del authorization_code, code_verifier, redirect_uri
            return {"access_token": "access-token", "refresh_token": "refresh-token"}

    broker = NoExpiryBroker()
    with Session(engine) as session:
        connection, started = begin_oauth_connection(
            session,
            workspace_id,
            "salesforce",
            "Salesforce account",
            ["api", "refresh_token"],
            broker,
            store,
            "http://localhost:8000",
            "actor-one",
        )
        completed = complete_oauth_connection(
            session,
            workspace_id,
            connection.id,
            started.state,
            "authorization-code",
            store,
            broker,
            "actor-one",
        )
        expires_at = datetime.fromisoformat(
            str(completed.connection_metadata["token_expires_at"])
        )
        remaining = (expires_at - datetime.now(UTC)).total_seconds()
        assert 3590 <= remaining <= 3600
        assert completed.connection_metadata["refresh_available"] is True


def test_expired_oauth_state_is_rejected_and_pkce_secret_is_discarded(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'expired-state.db'}")
    Base.metadata.create_all(engine)
    store = DevelopmentSecretStore()
    broker = DevelopmentOAuthBroker()
    workspace_id = uuid4()
    with Session(engine) as session:
        connection, started = begin_oauth_connection(
            session,
            workspace_id,
            "hubspot",
            "HubSpot account",
            [],
            broker,
            store,
            "http://localhost:8000",
            "actor-one",
        )
        reference = connection.secret_reference
        connection.connection_metadata = {
            **connection.connection_metadata,
            "oauth_state_expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        }
        session.commit()
        with pytest.raises(ValueError, match="expired"):
            complete_oauth_connection(
                session,
                workspace_id,
                connection.id,
                started.state,
                "authorization-code",
                store,
                broker,
                "actor-one",
            )
        session.refresh(connection)
        assert connection.status == "authorization_expired"
        assert connection.secret_reference is None
        assert store.retrieve(reference) is None


def test_concurrent_oauth_state_redemption_exchanges_code_once(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-state.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    store = DevelopmentSecretStore()
    workspace_id = uuid4()

    class BlockingBroker(DevelopmentOAuthBroker):
        def __init__(self) -> None:
            self.exchange_started = Event()
            self.release_exchange = Event()
            self.exchange_calls = 0
            self.lock = Lock()

        def exchange(self, provider, authorization_code, code_verifier, redirect_uri):
            with self.lock:
                self.exchange_calls += 1
            self.exchange_started.set()
            assert self.release_exchange.wait(timeout=5)
            return super().exchange(provider, authorization_code, code_verifier, redirect_uri)

    broker = BlockingBroker()
    with Session(engine) as session:
        connection, started = begin_oauth_connection(
            session,
            workspace_id,
            "hubspot",
            "HubSpot account",
            [],
            broker,
            store,
            "http://localhost:8000",
            "actor-one",
        )
        connection_id = connection.id

    def redeem():
        with Session(engine) as session:
            return complete_oauth_connection(
                session,
                workspace_id,
                connection_id,
                started.state,
                "authorization-code",
                store,
                broker,
                "actor-one",
            ).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(redeem)
        assert broker.exchange_started.wait(timeout=5)
        second = executor.submit(redeem)
        with pytest.raises(ValueError):
            second.result(timeout=5)
        broker.release_exchange.set()
        assert first.result(timeout=5) == "connected"
    assert broker.exchange_calls == 1
