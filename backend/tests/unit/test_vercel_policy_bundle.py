from __future__ import annotations

from pathlib import Path

from lead_agent.settings.config import Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


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
