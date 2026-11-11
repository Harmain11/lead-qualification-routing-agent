from __future__ import annotations

import json
import tomllib
from pathlib import Path

from lead_agent.settings.config import Settings
from lead_agent.worker.celery_app import _resolve_celery_transport


def test_vercel_declares_a_hobby_safe_scheduled_sync() -> None:
    import json
    from pathlib import Path

    config = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    assert "crons" not in config
    assert config["schedules"] == {
        "inbound-sync": {
            "schedule": "*/15 * * * *",
            "path": "/v1/internal/scheduled-sync",
            "requestMethod": "GET",
        }
    }

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_vercel_services_route_api_before_spa_fallback() -> None:
    config = json.loads((REPOSITORY_ROOT / "vercel.json").read_text(encoding="utf-8"))

    assert config["services"]["web"] == {"root": "frontend/", "framework": "vite"}
    assert config["services"]["api"] == {
        "root": "backend/",
        "framework": "fastapi",
        "entrypoint": "main:app",
    }
    assert config["rewrites"][0] == {
        "source": "/v1/(.*)",
        "destination": {"service": "api"},
    }
    assert config["rewrites"][1] == {
        "source": "/health/(.*)",
        "destination": {"service": "api"},
    }
    assert config["rewrites"][-1] == {
        "source": "/(.*)",
        "destination": {"service": "web", "path": "/index.html"},
    }
    assert "domains" not in config
    assert "target" not in config


def test_vercel_python_entrypoint_and_celery_subscriber_are_declared() -> None:
    pyproject = tomllib.loads(
        (REPOSITORY_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert pyproject["tool"]["vercel"]["entrypoint"] == "main:app"
    assert pyproject["tool"]["vercel"]["subscribers"] == [
        {"entrypoint": "worker:app"}
    ]

    assert (REPOSITORY_ROOT / "backend" / "main.py").is_file()
    assert (REPOSITORY_ROOT / "backend" / "worker.py").is_file()


def test_vercel_runtime_uses_managed_celery_transport() -> None:
    transport = _resolve_celery_transport(
        "rediss://example.invalid:6380/0",
        environ={"VERCEL": "1"},
    )

    assert transport.broker_url == "vercel://"
    assert transport.result_backend == "vercel-runtime-cache://"
    assert transport.broker_options == {
        "consumer_group": "corral-workers",
        "lease_duration": 300,
        "requeue_delay_seconds": 60,
    }
    assert transport.result_options == {
        "namespace": "corral-celery-results",
        "ttl": 86400,
    }


def test_non_vercel_runtime_keeps_redis_celery_transport() -> None:
    redis_url = "redis://127.0.0.1:6380/0"
    transport = _resolve_celery_transport(redis_url, environ={})

    assert transport.broker_url == redis_url
    assert transport.result_backend == redis_url
    assert transport.broker_options == {"visibility_timeout": 3600}
    assert transport.result_options == {"visibility_timeout": 3600}


def test_vercel_backend_service_bundles_default_policy_files(monkeypatch) -> None:
    """The FastAPI service deploys from backend/, not the repository root."""
    monkeypatch.chdir(REPOSITORY_ROOT / "backend")
    settings = Settings()

    assert settings.resolve_path(settings.icp_config_path) == (
        REPOSITORY_ROOT / "backend" / "config" / "icp.example.yaml"
    )
    assert settings.resolve_path(settings.routing_config_path) == (
        REPOSITORY_ROOT / "backend" / "config" / "routing.example.yaml"
    )
    assert settings.resolve_path(settings.crm_config_path) == (
        REPOSITORY_ROOT / "backend" / "config" / "crm.example.yaml"
    )

    icp, routing = settings.load_policies()
    assert icp.profile_name == "Example ICP"
    assert routing.policy_version == "2026-09-21.example.1"
    assert settings.load_crm_config().payload_version == "1.0"
