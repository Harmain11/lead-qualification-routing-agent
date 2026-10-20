from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from lead_agent import production_check
from lead_agent.api.dependencies import get_runtime_settings
from lead_agent.api.middleware import install_request_middleware, request_id_for
from lead_agent.api.routes import health
from lead_agent.infrastructure import rate_limit
from lead_agent.infrastructure.observability import (
    JsonLogFormatter,
    LoggingObservability,
    provider_context,
    provider_operation,
    redact,
    redact_text,
    reset_request_id,
    set_request_id,
)
from lead_agent.infrastructure.rate_limit import (
    MemoryRateLimiter,
    RateLimitPolicy,
    RedisRateLimiter,
    rate_limit_dependency,
)
from lead_agent.settings.config import Settings


def test_memory_rate_limiter_has_deterministic_fixed_window() -> None:
    now = [100.0]
    limiter = MemoryRateLimiter(clock=lambda: now[0])
    policy = RateLimitPolicy(limit=2, window_seconds=10)
    assert limiter.hit("client", policy) == (True, 10)
    assert limiter.hit("client", policy) == (True, 10)
    assert limiter.hit("client", policy) == (False, 10)
    now[0] = 111.0
    assert limiter.hit("client", policy) == (True, 10)


def test_redis_limiter_uses_a_shared_atomic_counter() -> None:
    class SharedRedis:
        def __init__(self) -> None:
            self.counts: dict[str, int] = {}

        def eval(self, script: str, keys: int, key: str, window: str) -> list[int]:
            assert "INCR" in script and "EXPIRE" in script
            assert keys == 1 and window == "60"
            self.counts[key] = self.counts.get(key, 0) + 1
            return [self.counts[key], window]

    shared = SharedRedis()
    first = RedisRateLimiter(cast(Any, shared))
    second = RedisRateLimiter(cast(Any, shared))
    policy = RateLimitPolicy(limit=1, window_seconds=60)
    assert first.hit("shared", policy) == (True, 60)
    assert second.hit("shared", policy) == (False, 60)


def test_rate_limited_response_is_safe_and_has_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    class Limited:
        def hit(self, key: str, policy: RateLimitPolicy) -> tuple[bool, int]:
            return False, 17

    monkeypatch.setattr(rate_limit, "get_rate_limiter", lambda _settings: Limited())
    app = FastAPI()
    app.get("/limited", dependencies=[Depends(rate_limit_dependency("authentication"))])(
        lambda: {"ok": True}
    )
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(_env_file=None)
    with TestClient(app) as client:
        response = client.get("/limited")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "17"
    assert response.json() == {"detail": "rate limit exceeded"}
    assert "redis" not in response.text.lower()


def test_security_headers_and_correlation_id_are_centralized() -> None:
    app = FastAPI()
    install_request_middleware(app, cast(Any, SimpleNamespace(app_env="production")))
    app.get("/ok")(lambda: {"ok": True})
    with TestClient(app) as client:
        response = client.get("/ok", headers={"X-Request-ID": "upstream-42"})
        generated = client.get("/ok", headers={"X-Request-ID": "bad\r\nid"})
    assert response.headers["X-Request-ID"] == "upstream-42"
    assert generated.headers["X-Request-ID"] != "bad\r\nid"
    assert request_id_for("unsafe\nvalue") != "unsafe\nvalue"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    content_security_policy = response.headers["Content-Security-Policy"]
    assert "frame-src 'self' https://phone.cloudtalk.io" in content_security_policy
    assert "frame-ancestors 'none'" in content_security_policy
    assert response.headers["Permissions-Policy"] == (
        'camera=(), microphone=(self "https://phone.cloudtalk.io"), geolocation=()'
    )
    assert response.headers["Strict-Transport-Security"].startswith("max-age=")


def test_log_redaction_covers_structured_nested_and_header_values() -> None:
    data = redact(
        {
            "workspace_id": "workspace-safe",
            "provider": "salesforce",
            "headers": {"Authorization": "Bearer access-secret", "X-Request-ID": "req-1"},
            "error": {"message": "failed", "refresh_token": "refresh-secret"},
            "error_code": "rate_limited",
        }
    )
    rendered = json.dumps(data)
    assert "workspace-safe" in rendered and "salesforce" in rendered
    assert "req-1" in rendered and "rate_limited" in rendered
    assert "access-secret" not in rendered and "refresh-secret" not in rendered
    assert redact_text("Authorization: Bearer token-secret") == "Authorization: [redacted]"
    assert '"access_token":"[redacted]"' in redact_text(
        '{"access_token":"token-secret","error_code":"safe"}'
    )


def test_structured_formatter_and_metrics_boundary_keep_safe_fields() -> None:
    token = set_request_id("request-123")
    try:
        record = SimpleNamespace(
            created=0.0,
            levelname="INFO",
            getMessage=lambda: "provider.failure",
            fields={"provider": "hubspot", "api_key": "hidden", "status": 503},
            exc_info=None,
        )
        event = json.loads(JsonLogFormatter().format(cast(Any, record)))
        assert event["request_id"] == "request-123"
        assert event["provider"] == "hubspot"
        assert event["status"] == 503
        assert event["api_key"] == "[redacted]"
        metrics = LoggingObservability()
        with provider_operation("crm", "connection-1") as context:
            assert context["request_id"] == "request-123"
            assert context["operation_id"] != "-"
            assert context["connection_id"] == "connection-1"
            assert provider_context()["provider"] == "crm"
            metrics.provider_failure("crm", "transient", status=503)
            metrics.record_duration("crm.operation", 12.5, status="ok")
            metrics.retry_exhausted("crm_sync", attempts=3)
            metrics.job_failure("worker.task", error_type="RuntimeError")
    finally:
        reset_request_id(token)


def test_liveness_is_lightweight_and_readiness_reports_required_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert health.liveness().status == "ok"

    class HealthySession:
        def execute(self, _query: object) -> None:
            return None

    class RedisClient:
        def ping(self) -> bool:
            return True

        def close(self) -> None:
            return None

    monkeypatch.setattr(health.Redis, "from_url", lambda *args, **kwargs: RedisClient())
    settings = Settings(redis_url="redis://shared", _env_file=None)
    result = health.readiness(cast(Session, HealthySession()), settings)
    assert result.status == "ready"
    assert result.components == {"postgresql": "ok", "redis": "ok"}


def test_readiness_fails_safely_for_postgres_and_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailedSession:
        def execute(self, _query: object) -> None:
            raise RuntimeError("postgres://user:password@host")

    class FailedRedis:
        def ping(self) -> None:
            raise RuntimeError("redis://user:password@host")

        def close(self) -> None:
            return None

    monkeypatch.setattr(health.Redis, "from_url", lambda *args, **kwargs: FailedRedis())
    with pytest.raises(health.HTTPException) as error:
        health.readiness(
            cast(Session, FailedSession()),
            Settings(redis_url="redis://shared", _env_file=None),
        )
    assert error.value.status_code == 503
    assert error.value.detail == {
        "status": "not_ready",
        "components": {"postgresql": "unavailable", "redis": "unavailable"},
    }
    assert "password" not in str(error.value.detail)


def test_production_rejects_local_database_redis_and_process_limiter() -> None:
    with pytest.raises(ValidationError) as error:
        Settings(app_env="production", _env_file=None)
    messages = str(error.value)
    assert "production PostgreSQL requires" in messages
    assert "production Redis requires" in messages
    assert "RATE_LIMIT_BACKEND must be redis" in messages


def test_job_retry_limit_must_be_non_negative() -> None:
    with pytest.raises(ValidationError, match="JOB_MAX_RETRIES"):
        Settings(job_max_retries=-1, _env_file=None)


def test_configured_job_retry_limit_is_effective_for_celery_tasks() -> None:
    environment = os.environ.copy()
    environment["JOB_MAX_RETRIES"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from lead_agent.worker.celery_app import celery_app; "
                "print(celery_app.Task.max_retries)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.stdout.strip() == "1"


def test_production_check_never_prints_secret_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def invalid_settings() -> None:
        raise RuntimeError("secret-value-must-not-be-printed")

    monkeypatch.setattr(production_check, "Settings", invalid_settings)
    assert production_check.main() == 1
    output = capsys.readouterr().out
    assert "FAIL production-check" in output
    assert "secret-value-must-not-be-printed" not in output

    monkeypatch.setattr(
        production_check,
        "Settings",
        lambda: SimpleNamespace(app_env="production"),
    )
    assert production_check.main() == 0
    assert capsys.readouterr().out == "PASS production-check: configuration passed validation\n"
