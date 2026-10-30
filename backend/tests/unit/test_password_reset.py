from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from sqlalchemy import select

from lead_agent.api.main import app
from lead_agent.infrastructure.db.models import PasswordResetTokenORM
from lead_agent.infrastructure.email import get_email_provider

from test_authentication import auth_harness, signup

NEW_PASSWORD = "a-brand-new-passphrase"


def reset_token(provider) -> str:
    message = provider.messages[-1]
    assert message.purpose == "reset_password"
    return parse_qs(urlsplit(message.verification_url).query)["reset_password"][0]


def test_reset_link_sets_password_signs_out_everywhere_and_is_single_use(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client, TestClient(app) as other_device:
            assert signup(client).status_code == 200
            assert other_device.post(
                "/v1/auth/signin",
                json={"email": "owner@example.test", "password": "correct-horse-battery-staple"},
            ).status_code == 200
            provider = app.dependency_overrides[get_email_provider]()

            requested = client.post(
                "/v1/auth/password-reset/request", json={"email": "Owner@Example.test"}
            )
            assert requested.status_code == 202
            token = reset_token(provider)
            stored = session.scalar(select(PasswordResetTokenORM))
            assert stored is not None and token not in stored.token_hash

            confirmed = client.post(
                "/v1/auth/password-reset/confirm",
                json={"token": token, "password": NEW_PASSWORD},
            )
            assert confirmed.status_code == 200
            # Existing sessions on every device are revoked.
            assert client.get("/v1/auth/session").status_code == 401
            assert other_device.get("/v1/auth/session").status_code == 401
            assert client.post(
                "/v1/auth/signin",
                json={"email": "owner@example.test", "password": "correct-horse-battery-staple"},
            ).status_code == 401
            assert client.post(
                "/v1/auth/signin",
                json={"email": "owner@example.test", "password": NEW_PASSWORD},
            ).status_code == 200
            # The link cannot be replayed.
            assert client.post(
                "/v1/auth/password-reset/confirm",
                json={"token": token, "password": "yet-another-passphrase"},
            ).status_code == 400
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_reset_request_never_reveals_whether_an_account_exists(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            assert signup(client).status_code == 200
            provider = app.dependency_overrides[get_email_provider]()
            sent_before = len(provider.messages)
            known = client.post("/v1/auth/password-reset/request", json={"email": "owner@example.test"})
            unknown = client.post("/v1/auth/password-reset/request", json={"email": "nobody@example.test"})
            assert known.status_code == unknown.status_code == 202
            assert known.json() == unknown.json()
            assert len(provider.messages) == sent_before + 1
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_expired_or_unknown_reset_tokens_are_rejected(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            assert signup(client).status_code == 200
            provider = app.dependency_overrides[get_email_provider]()
            client.post("/v1/auth/password-reset/request", json={"email": "owner@example.test"})
            token = reset_token(provider)
            stored = session.scalar(select(PasswordResetTokenORM))
            assert stored is not None
            stored.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            session.commit()
            assert client.post(
                "/v1/auth/password-reset/confirm",
                json={"token": token, "password": NEW_PASSWORD},
            ).status_code == 400
            assert client.post(
                "/v1/auth/password-reset/confirm",
                json={"token": "x" * 40, "password": NEW_PASSWORD},
            ).status_code == 400
            assert client.post(
                "/v1/auth/password-reset/confirm",
                json={"token": token, "password": "short"},
            ).status_code == 422
    finally:
        app.dependency_overrides.clear()
        session.close()
