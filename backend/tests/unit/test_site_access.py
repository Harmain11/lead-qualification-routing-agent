from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.responses import Response

from lead_agent.api.dependencies import get_runtime_settings
from lead_agent.api.middleware import install_request_middleware
from lead_agent.api.routes import site_access as site_access_routes
from lead_agent.api.routes.site_access import (
    SiteAccessUnlockRequest,
    unlock_site_access,
)
from lead_agent.api.site_access_security import (
    SITE_ACCESS_COOKIE_MAX_AGE,
    SITE_ACCESS_COOKIE_NAME,
    create_site_access_cookie,
    site_access_cookie_is_valid,
)
from lead_agent.settings.config import Settings


def gate_settings(**overrides: object) -> Settings:
    return Settings(
        app_env="test",
        secret_key="test-signing-secret-that-is-long-enough",
        corral_site_access_password="local-test-site-password-123",
        _env_file=None,
        **overrides,
    )


def test_site_access_cookie_is_signed_and_expires() -> None:
    settings = gate_settings()
    token = create_site_access_cookie(settings, now=100)

    assert site_access_cookie_is_valid(token, settings, now=101)
    assert not site_access_cookie_is_valid(token + "tampered", settings, now=101)
    assert not site_access_cookie_is_valid(
        token,
        settings,
        now=100 + SITE_ACCESS_COOKIE_MAX_AGE,
    )


def test_unlock_rejects_wrong_password_and_sets_http_only_cookie() -> None:
    settings = gate_settings()
    with pytest.raises(HTTPException) as error:
        unlock_site_access(
            SiteAccessUnlockRequest(password="incorrect"),
            Response(),
            settings,
        )
    assert error.value.status_code == 401

    response = Response()
    result = unlock_site_access(
        SiteAccessUnlockRequest(password=settings.corral_site_access_password),
        response,
        settings,
    )
    assert result.unlocked
    assert SITE_ACCESS_COOKIE_NAME in response.headers["set-cookie"]
    assert "httponly" in response.headers["set-cookie"].lower()


def test_middleware_gates_private_api_and_preserves_public_callbacks() -> None:
    settings = gate_settings()
    app = FastAPI()
    install_request_middleware(app, settings)
    app.include_router(site_access_routes.router)
    app.dependency_overrides[get_runtime_settings] = lambda: settings
    unlock_route = next(
        route
        for route in site_access_routes.router.routes
        if getattr(route, "path", None) == "/v1/site-access/unlock"
    )
    app.dependency_overrides[unlock_route.dependencies[0].dependency] = lambda: None

    @app.get("/v1/private")
    def private_api() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/v1/intake/public/{token}/connection")
    def public_intake(token: str) -> dict[str, str]:
        return {"token": token}

    @app.get("/v1/webhooks/meta")
    def public_webhook() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/health/live")
    def health() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        assert client.get("/v1/site-access/status").json() == {
            "enabled": True,
            "unlocked": False,
        }
        blocked = client.get("/v1/private")
        assert blocked.status_code == 423
        assert blocked.json()["code"] == "site_access_required"
        assert client.get("/v1/intake/public/form-token/connection").status_code == 200
        assert client.get("/v1/webhooks/meta").status_code == 200
        assert client.get("/health/live").status_code == 200

        assert client.post("/v1/site-access/unlock", json={"password": "incorrect"}).status_code == 401
        unlocked = client.post(
            "/v1/site-access/unlock",
            json={"password": settings.corral_site_access_password},
        )
        assert unlocked.status_code == 200
        assert "httponly" in unlocked.headers["set-cookie"].lower()
        assert client.get("/v1/site-access/status").json() == {
            "enabled": True,
            "unlocked": True,
        }
        assert client.get("/v1/private").status_code == 200
