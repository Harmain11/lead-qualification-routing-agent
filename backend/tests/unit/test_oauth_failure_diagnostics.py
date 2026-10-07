from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.application.integrations import (
    begin_oauth_connection,
    complete_oauth_connection,
    reject_oauth_connection,
)
from lead_agent.domain.oauth_errors import OAuthProviderError
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.oauth import DevelopmentOAuthBroker, ProviderOAuthBroker
from lead_agent.infrastructure.secrets import DevelopmentSecretStore
from lead_agent.settings.config import Settings


@pytest.mark.parametrize(
    "provider,status,payload,expected",
    [
        (
            "salesforce", 400,
            {"error": "invalid_grant", "error_description": "private details"}, "invalid_grant",
        ),
        (
            "slack", 200, {"ok": False, "error": "invalid_team_for_non_distributed_app"},
            "invalid_team_for_non_distributed_app",
        ),
        ("salesforce", 401, {"error": "secret-looking-unknown-value"}, "oauth_provider_error"),
        ("salesforce", 200, {}, "missing_access_token"),
        ("salesforce", 400, {"error": "invalid_app_access"}, "invalid_app_access"),
        ("salesforce", 400, {"error": "inactive_user"}, "inactive_user"),
        ("salesforce", 400, {"error": "CSRF"}, "CSRF"),
        ("slack", 400, {"error": "invalid_app_access"}, "oauth_provider_error"),
    ],
)
def test_exchange_errors_are_actionable_and_redacted(provider, status, payload, expected):
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, json=payload)
    ))
    broker = ProviderOAuthBroker(
        Settings(
            salesforce_client_id="test-client", salesforce_client_secret="test-secret",
            slack_client_id="test-client", slack_client_secret="test-secret",
        ), client,
    )
    with pytest.raises(OAuthProviderError) as raised:
        broker.exchange(provider, "private-code", "private-verifier", "https://app.test/callback")
    assert raised.value.error_code == expected
    assert "private" not in str(raised.value)
    assert "secret-looking" not in str(raised.value)
    assert "test-secret" not in str(raised.value)
    client.close()


@pytest.mark.parametrize("provider,code", [
    ("salesforce", "access_denied"),
    ("salesforce", "invalid_app_access"),
    ("slack", "invalid_team_for_non_distributed_app"),
])
def test_rejected_callback_requires_valid_state_and_cleans_pending_secret(tmp_path, provider, code):
    engine = create_engine(f"sqlite:///{tmp_path / 'oauth.db'}")
    Base.metadata.create_all(engine)
    store = DevelopmentSecretStore()
    workspace = uuid4()
    with Session(engine) as session:
        row, started = begin_oauth_connection(
            session, workspace, provider, "Pending", [], DevelopmentOAuthBroker(),
            store, "https://app.test", "actor",
        )
        reference = row.secret_reference
        error = OAuthProviderError(provider, code)
        with pytest.raises(ValueError, match="state validation"):
            reject_oauth_connection(
                session, workspace, row.id, started.state + "-forged", store, "actor", error,
            )
        assert row.status == "awaiting_authorization"
        assert store.retrieve(reference) is not None
        with pytest.raises(ValueError, match="different actor"):
            reject_oauth_connection(
                session, workspace, row.id, started.state, store, "other-actor", error,
            )
        reject_oauth_connection(
            session, workspace, row.id, started.state, store, "actor", error,
        )
        assert row.status == "connection_failed"
        assert row.last_validation_result["error_code"] == code
        assert row.secret_reference is None
        assert store.retrieve(reference) is None
        with pytest.raises(ValueError, match="pending connection"):
            reject_oauth_connection(
                session, workspace, row.id, started.state, store, "actor", error,
            )


@pytest.mark.parametrize("typed", [True, False])
def test_exchange_failure_is_persisted_without_raw_provider_details(tmp_path, typed, caplog):
    class FailingBroker(DevelopmentOAuthBroker):
        def exchange(self, *args):
            if typed:
                raise OAuthProviderError("salesforce", "invalid_grant", 400)
            raise RuntimeError("private-token-or-provider-response")

    engine = create_engine(f"sqlite:///{tmp_path / 'exchange.db'}")
    Base.metadata.create_all(engine)
    store = DevelopmentSecretStore()
    workspace = uuid4()
    with Session(engine) as session:
        broker = FailingBroker()
        row, started = begin_oauth_connection(
            session, workspace, "salesforce", "Pending", [], broker,
            store, "https://app.test", "actor",
        )
        with pytest.raises(OAuthProviderError):
            complete_oauth_connection(
                session, workspace, row.id, started.state, "private-code", store,
                broker, "actor",
            )
        assert row.status == "connection_failed"
        assert row.secret_reference is None
        assert row.last_validation_result["error_code"] == (
            "invalid_grant" if typed else "oauth_exchange_failed"
        )
        assert "private-token" not in str(row.last_validation_result)
        assert "private-token" not in caplog.text


@pytest.mark.parametrize(
    "code,message",
    [
        ("invalid_app_access", "profiles or permission sets"),
        ("inactive_user", "activate the user"),
        ("inactive_org", "locked, closed or suspended"),
        ("CSRF", "approved Salesforce login domain"),
        ("NO_ACCESS", "active user"),
        ("rate_limit_exceeded", "Wait"),
        ("redirect_uri_missing", "registered callback URL"),
        ("immediate_unsuccessful", "interactive login"),
        ("No_OAuth_State", "do not reuse a callback link"),
        ("No_Openid_Response", "User Info Endpoint URL"),
        ("REGISTRATION_HANDLER_ERROR", "Apex registration handler"),
        ("ERROR_CREATING_USER", "license availability"),
    ],
)
def test_salesforce_specific_errors_have_static_actionable_messages(code, message):
    error = OAuthProviderError("salesforce", code)
    assert error.error_code == code
    assert message in str(error)
