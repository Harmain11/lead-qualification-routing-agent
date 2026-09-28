from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_authentication import auth_harness, csrf, signup

from lead_agent.api.main import app
from lead_agent.infrastructure.db.models import ExternalIdentityORM, WorkspaceORM
from lead_agent.infrastructure.google_auth import (
    GOOGLE_SCOPES,
    GoogleIdentity,
    get_google_auth_client,
)


class FakeGoogleClient:
    def __init__(self, identity: GoogleIdentity) -> None:
        self.identity = identity

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return f"https://accounts.google.test/auth?state={state}&scope={'%20'.join(GOOGLE_SCOPES)}"

    def exchange(self, *, code: str, code_verifier: str, redirect_uri: str) -> GoogleIdentity:
        assert code_verifier
        assert redirect_uri.endswith("/v1/auth/google/callback")
        return self.identity


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
