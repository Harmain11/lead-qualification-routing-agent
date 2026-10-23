from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_authentication import auth_harness, csrf, signup

from lead_agent.api.main import app
from lead_agent.infrastructure.db.models import ExternalIdentityORM, WorkspaceORM
from lead_agent.infrastructure.google_auth import (
    GOOGLE_SCOPES,
    GoogleIdentity,
    ProviderGoogleAuthClient,
    get_google_auth_client,
)
from lead_agent.settings.config import Settings


class FakeGoogleClient:
    def __init__(self, identity: GoogleIdentity) -> None:
        self.identity = identity

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return f"https://accounts.google.test/auth?state={state}&scope={'%20'.join(GOOGLE_SCOPES)}"

    def exchange(self, *, code: str, code_verifier: str, redirect_uri: str) -> GoogleIdentity:
        assert code_verifier
        assert redirect_uri.endswith("/v1/auth/google/callback")
        return self.identity


class FailingGoogleClient(FakeGoogleClient):
    def exchange(self, *, code: str, code_verifier: str, redirect_uri: str) -> GoogleIdentity:
        raise RuntimeError("sensitive@example.test code=do-not-log")


def _complete_google(client: TestClient, identity: GoogleIdentity) -> tuple[object, str]:
    app.dependency_overrides[get_google_auth_client] = lambda: FakeGoogleClient(identity)
    started = client.get("/v1/auth/google/start", follow_redirects=False)
    assert started.status_code == 303
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    callback = client.get(
        "/v1/auth/google/callback",
        params={"state": state, "code": "google-code"},
        follow_redirects=False,
    )
    return callback, state


def test_google_new_user_gets_isolated_workspace_and_crm_session(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            callback, _ = _complete_google(
                client,
                GoogleIdentity("google-sub-1", "new-google@example.test", "Google Owner", True),
            )
            assert callback.status_code == 303
            assert (
                client.get("/v1/auth/status").json()["user"]["email"]
                == "new-google@example.test"
            )
            assert len(list(session.scalars(select(WorkspaceORM)))) == 1
            identity = session.scalar(select(ExternalIdentityORM))
            assert identity is not None
            assert identity.subject == "google-sub-1"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_google_verified_email_links_exact_existing_account_without_duplicate_user(
    tmp_path,
) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            created = signup(client, "existing@example.test")
            original_user_id = created.json()["user"]["id"]
            client.post("/v1/auth/signout", headers=csrf(client))
            callback, _ = _complete_google(
                client,
                GoogleIdentity("google-sub-existing", "existing@example.test", "Existing", True),
            )
            assert callback.status_code == 303
            body = client.get("/v1/auth/status").json()
            assert body["user"]["id"] == original_user_id
            identities = list(session.scalars(select(ExternalIdentityORM)))
            assert len(identities) == 1
            assert identities[0].user_id == session.scalar(select(ExternalIdentityORM)).user_id
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_google_state_is_single_use_and_does_not_request_gmail_scope(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            callback, state = _complete_google(
                client,
                GoogleIdentity("google-sub-replay", "replay@example.test", "Replay", True),
            )
            assert callback.status_code == 303
            replay = client.get(
                "/v1/auth/google/callback",
                params={"state": state, "code": "google-code"},
                follow_redirects=False,
            )
            assert replay.status_code == 400
            assert "gmail.readonly" not in callback.headers.get("location", "")
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_google_callback_logs_failure_stage_without_exception_details(tmp_path, caplog) -> None:
    session = auth_harness(tmp_path)
    try:
        app.dependency_overrides[get_google_auth_client] = lambda: FailingGoogleClient(
            GoogleIdentity("unused", "unused@example.test", "Unused", True)
        )
        caplog.set_level(logging.WARNING, logger="lead_agent.api.auth")
        with TestClient(app) as client:
            started = client.get("/v1/auth/google/start", follow_redirects=False)
            state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
            callback = client.get(
                "/v1/auth/google/callback",
                params={"state": state, "code": "sensitive-google-code"},
                follow_redirects=False,
            )

        assert callback.status_code == 303
        assert "google_authentication_failed" in callback.headers["location"]
        failure = next(
            record
            for record in caplog.records
            if record.name == "lead_agent.api.auth"
            and record.getMessage() == "google.auth.callback_failed"
        )
        assert failure.fields["stage"] == "google_identity_exchange"
        assert failure.fields["error_type"] == "RuntimeError"
        assert failure.fields["request_id"] != "-"
        assert "sensitive@example.test" not in caplog.text
        assert "do-not-log" not in caplog.text
        assert "sensitive-google-code" not in caplog.text
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_google_callback_logs_local_account_stage_for_rejected_identity(tmp_path, caplog) -> None:
    session = auth_harness(tmp_path)
    try:
        app.dependency_overrides[get_google_auth_client] = lambda: FakeGoogleClient(
            GoogleIdentity("unverified-sub", "unverified@example.test", "Unverified", False)
        )
        caplog.set_level(logging.WARNING, logger="lead_agent.api.auth")
        with TestClient(app) as client:
            started = client.get("/v1/auth/google/start", follow_redirects=False)
            state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
            callback = client.get(
                "/v1/auth/google/callback",
                params={"state": state, "code": "sensitive-google-code"},
                follow_redirects=False,
            )

        assert callback.status_code == 303
        failure = next(
            record
            for record in caplog.records
            if record.name == "lead_agent.api.auth"
            and record.getMessage() == "google.auth.callback_failed"
        )
        assert failure.fields["stage"] == "local_account_session_creation"
        assert failure.fields["error_type"] == "AuthenticationError"
        assert "unverified@example.test" not in caplog.text
        assert "sensitive-google-code" not in caplog.text
    finally:
        app.dependency_overrides.clear()
        session.close()


@pytest.mark.parametrize(
    ("failure_at", "expected_stage", "expected_status", "expected_code"),
    [
        ("token", "token_exchange", 400, "invalid_grant"),
        ("userinfo", "userinfo", 401, "invalid_token"),
    ],
)
def test_google_provider_errors_log_only_safe_status_and_code(
    caplog,
    failure_at: str,
    expected_stage: str,
    expected_status: int,
    expected_code: str,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if failure_at == "userinfo" and request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "sensitive-access-token"})
        return httpx.Response(
            expected_status,
            json={"error": expected_code, "error_description": "sensitive error details"},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    provider = ProviderGoogleAuthClient(
        Settings(google_client_id="test-client", google_client_secret="test-secret"),
        client=client,
    )
    caplog.set_level(logging.WARNING, logger="lead_agent.google_auth")

    with pytest.raises(httpx.HTTPStatusError):
        provider.exchange(
            code="sensitive-google-code",
            code_verifier="sensitive-verifier",
            redirect_uri="https://corralai.tech/v1/auth/google/callback",
        )

    failure = next(
        record
        for record in caplog.records
        if record.name == "lead_agent.google_auth"
        and record.getMessage() == "google.oauth.request_failed"
    )
    assert failure.fields["stage"] == expected_stage
    assert failure.fields["http_status"] == expected_status
    assert failure.fields["provider_error_code"] == expected_code
    assert "sensitive error details" not in caplog.text
    assert "sensitive-google-code" not in caplog.text
    assert "sensitive-verifier" not in caplog.text
    assert "sensitive-access-token" not in caplog.text
    assert "test-secret" not in caplog.text
    client.close()
