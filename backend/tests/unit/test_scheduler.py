from __future__ import annotations

from fastapi.testclient import TestClient

from lead_agent.api.dependencies import get_runtime_settings
from lead_agent.api.main import app
from lead_agent.api.routes import scheduler
from lead_agent.application.scheduled_sync import ScheduledSyncSummary
from lead_agent.settings.config import Settings


def test_scheduled_sync_rejects_requests_without_the_cron_secret() -> None:
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(cron_secret="unit-secret")
    try:
        with TestClient(app) as client:
            response = client.get("/v1/internal/scheduled-sync")
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_scheduled_sync_runs_bounded_due_work_with_a_valid_cron_secret(
    monkeypatch,
) -> None:
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        cron_secret="unit-secret", vercel_scheduled_sync_limit=7
    )
    received_limits: list[int] = []

    def run_due_work(*, limit: int) -> ScheduledSyncSummary:
        received_limits.append(limit)
        return ScheduledSyncSummary(claimed=3, completed=2, skipped=1, failed=0)

    monkeypatch.setattr(scheduler, "run_due_inbound_syncs", run_due_work)
    try:
        with TestClient(app) as client:
            response = client.get(
                "/v1/internal/scheduled-sync",
                headers={"Authorization": "Bearer unit-secret"},
            )
        assert response.status_code == 200, response.text
        assert response.json() == {"claimed": 3, "completed": 2, "skipped": 1, "failed": 0}
        assert received_limits == [7]
    finally:
        app.dependency_overrides.clear()
