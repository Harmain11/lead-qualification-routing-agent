from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_runtime_settings
from lead_agent.api.main import app
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    ProviderConnectionORM,
    WorkspaceMembershipORM,
    WorkspaceORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.secrets import DevelopmentSecretStore, get_secret_store
from lead_agent.settings.config import Settings


def auth_harness(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'auth.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    settings = Settings(
        app_env="production",
        auth_development_fallback=False,
        auth_cookie_secure=False,
        database_url=f"sqlite:///{tmp_path / 'auth.db'}",
    )

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_runtime_settings] = lambda: settings
    app.dependency_overrides[get_secret_store] = lambda: DevelopmentSecretStore()
    return session


def signup(client: TestClient, email: str = "owner@example.test"):
    return client.post(
        "/v1/auth/signup",
        json={
            "email": email,
            "password": "correct-horse-battery-staple",
            "display_name": "Workspace Owner",
            "workspace_name": f"{email} workspace",
        },
    )


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("lead_agent_csrf") or ""}


def test_production_api_requires_authentication(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            status_response = client.get("/v1/auth/status")
            assert status_response.status_code == 200
            assert status_response.json() is None
            assert client.get("/v1/leads").status_code == 401
            assert client.get("/v1/analytics/dashboard?range=monthly").status_code == 401
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_signup_session_and_signout_lifecycle(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            created = signup(client)
            assert created.status_code == 201
            body = created.json()
            assert body["user"]["email"] == "owner@example.test"
            assert len(body["workspaces"]) == 1
            assert body["expires_at"] is not None
            assert client.cookies.get("lead_agent_session")
            assert client.cookies.get("lead_agent_csrf")
            restored = client.get("/v1/auth/session")
            assert restored.status_code == 200
            assert restored.json()["expires_at"] is not None
            assert client.get("/v1/leads").status_code == 200
            signed_out = client.post("/v1/auth/signout", headers=csrf(client))
            assert signed_out.status_code == 204
            assert client.get("/v1/auth/session").status_code == 401
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_signin_rejects_bad_password_and_restores_session(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as first:
            assert signup(first).status_code == 201
        with TestClient(app) as client:
            denied = client.post(
                "/v1/auth/signin",
                json={"email": "owner@example.test", "password": "incorrect"},
            )
            accepted = client.post(
                "/v1/auth/signin",
                json={
                    "email": "owner@example.test",
                    "password": "correct-horse-battery-staple",
                },
            )
            assert denied.status_code == 401
            assert accepted.status_code == 200
            assert client.get("/v1/auth/session").status_code == 200
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_cookie_mutation_requires_csrf(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            body = signup(client).json()
            workspace_id = body["current_workspace_id"]
            denied = client.post(
                "/v1/auth/workspace",
                json={"workspace_id": workspace_id},
            )
            allowed = client.post(
                "/v1/auth/workspace",
                headers=csrf(client),
                json={"workspace_id": workspace_id},
            )
            assert denied.status_code == 403
            assert allowed.status_code == 200
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_workspace_header_is_verified_against_membership(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client_a, TestClient(app) as client_b:
            workspace_a = signup(client_a, "a@example.test").json()["current_workspace_id"]
            workspace_b = signup(client_b, "b@example.test").json()["current_workspace_id"]
            assert client_a.get(
                "/v1/leads", headers={"X-Workspace-ID": workspace_a}
            ).status_code == 200
            assert client_a.get(
                "/v1/leads", headers={"X-Workspace-ID": workspace_b}
            ).status_code == 403
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_inactive_membership_cannot_select_workspace(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            signup_body = signup(client).json()
            user_membership = session.scalar(select(WorkspaceMembershipORM))
            assert user_membership is not None
            other = WorkspaceORM(
                id=uuid4(),
                name="Revoked workspace",
                slug=f"revoked-{uuid4().hex}",
                status="active",
                is_demo=False,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(other)
            session.flush()
            session.add(
                WorkspaceMembershipORM(
                    id=uuid4(),
                    workspace_id=other.id,
                    user_id=user_membership.user_id,
                    role="member",
                    status="inactive",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            session.commit()
            denied = client.post(
                "/v1/auth/workspace",
                headers={**csrf(client), "X-Workspace-ID": signup_body["current_workspace_id"]},
                json={"workspace_id": str(other.id)},
            )
            assert denied.status_code == 403
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_inactive_workspace_rejects_access_with_active_membership(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            signup_body = signup(client).json()
            workspace = session.get(WorkspaceORM, UUID(signup_body["current_workspace_id"]))
            assert workspace is not None
            workspace.status = "inactive"
            session.commit()

            denied = client.get(
                "/v1/leads",
                headers={"X-Workspace-ID": signup_body["current_workspace_id"]},
            )
            assert denied.status_code == 403
            assert client.post("/v1/auth/signout", headers=csrf(client)).status_code == 204
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_revoked_current_membership_does_not_block_switch_or_signout(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            signup(client)
            current = session.scalar(select(WorkspaceMembershipORM))
            assert current is not None
            other = WorkspaceORM(
                id=uuid4(),
                name="Available workspace",
                slug=f"available-{uuid4().hex}",
                status="active",
                is_demo=False,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(other)
            session.flush()
            session.add(
                WorkspaceMembershipORM(
                    id=uuid4(),
                    workspace_id=other.id,
                    user_id=current.user_id,
                    role="member",
                    status="active",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            current.status = "inactive"
            session.commit()

            switched = client.post(
                "/v1/auth/workspace",
                headers=csrf(client),
                json={"workspace_id": str(other.id)},
            )
            assert switched.status_code == 200
            assert switched.json()["current_workspace_id"] == str(other.id)

            other_membership = session.scalar(
                select(WorkspaceMembershipORM).where(
                    WorkspaceMembershipORM.workspace_id == other.id
                )
            )
            assert other_membership is not None
            other_membership.status = "inactive"
            session.commit()
            assert client.post("/v1/auth/signout", headers=csrf(client)).status_code == 204
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_development_session_rejects_malformed_workspace_header(tmp_path) -> None:
    session = auth_harness(tmp_path)
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        app_env="development",
        auth_development_fallback=True,
        auth_cookie_secure=False,
        database_url=f"sqlite:///{tmp_path / 'auth.db'}",
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/auth/development-session",
                headers={"X-Workspace-ID": "not-a-uuid"},
            )
            assert response.status_code == 422
            assert response.json()["detail"] == "X-Workspace-ID must be a UUID"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_oauth_state_is_one_time_scoped_and_secrets_are_not_exposed(tmp_path) -> None:
    session = auth_harness(tmp_path)
    store = DevelopmentSecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    try:
        with TestClient(app) as client:
            assert signup(client).status_code == 201
            started = client.post(
                "/v1/integrations/hubspot/connect",
                headers=csrf(client),
                json={"account_name": "HubSpot account", "scopes": []},
            )
            assert started.status_code == 200
            payload = started.json()
            completed = client.post(
                "/v1/integrations/hubspot/callback",
                headers=csrf(client),
                json={
                    "connection_id": payload["connection"]["id"],
                    "state": payload["state"],
                    "authorization_code": "single-use-code",
                },
            )
            connection = session.get(
                ProviderConnectionORM, UUID(payload["connection"]["id"])
            )
            assert connection is not None
            connection.connection_metadata = {
                **connection.connection_metadata,
                "token_expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            }
            session.commit()
            refreshed = client.post(
                f"/v1/integrations/connections/{payload['connection']['id']}/test",
                headers=csrf(client),
            )
            replay = client.post(
                "/v1/integrations/hubspot/callback",
                headers=csrf(client),
                json={
                    "connection_id": payload["connection"]["id"],
                    "state": payload["state"],
                    "authorization_code": "single-use-code",
                },
            )
            serialized = str(completed.json()).lower()
            assert completed.status_code == 200
            assert refreshed.status_code == 200
            assert refreshed.json()["valid"] is True
            assert refreshed.json()["connection"]["status"] == "connected"
            assert completed.json()["status"] == "connected"
            assert replay.status_code == 422
            assert "access_token" not in serialized
            assert "refresh_token" not in serialized
            assert "secret_reference" not in serialized
            assert "oauth_state" not in serialized
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_browser_oauth_callback_uses_connection_workspace_after_workspace_switch(
    tmp_path,
) -> None:
    session = auth_harness(tmp_path)
    store = DevelopmentSecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    try:
        with TestClient(app) as client:
            signup_body = signup(client).json()
            started = client.post(
                "/v1/integrations/hubspot/connect",
                headers=csrf(client),
                json={"account_name": "HubSpot account", "scopes": []},
            )
            assert started.status_code == 200

            membership = session.scalar(select(WorkspaceMembershipORM))
            assert membership is not None
            other = WorkspaceORM(
                id=uuid4(),
                name="Other workspace",
                slug=f"other-{uuid4().hex}",
                status="active",
                is_demo=False,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(other)
            session.flush()
            session.add(
                WorkspaceMembershipORM(
                    id=uuid4(),
                    workspace_id=other.id,
                    user_id=membership.user_id,
                    role="member",
                    status="active",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            session.commit()
            switched = client.post(
                "/v1/auth/workspace",
                headers=csrf(client),
                json={"workspace_id": str(other.id)},
            )
            assert switched.status_code == 200

            payload = started.json()
            callback = client.get(
                "/v1/integrations/hubspot/oauth/callback",
                params={"state": payload["state"], "code": "browser-code"},
                follow_redirects=False,
            )
            connection = session.get(
                ProviderConnectionORM, UUID(payload["connection"]["id"])
            )
            assert callback.status_code == 307
            assert callback.headers["location"].endswith(
                "?integration=hubspot&status=connected"
            )
            assert connection is not None
            assert connection.workspace_id == UUID(signup_body["current_workspace_id"])
            assert connection.status == "connected"
    finally:
        app.dependency_overrides.clear()
        session.close()
