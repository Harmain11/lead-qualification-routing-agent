from concurrent.futures import Future
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from lead_agent.worker import preview_sync


def test_preview_dispatches_without_browser_or_redis(monkeypatch):
    completed = []
    monkeypatch.setattr(preview_sync, "get_session_factory", lambda: lambda: nullcontext(None))
    monkeypatch.setattr(preview_sync, "claim_due_inbound_sync_states",
                        lambda _session, limit: [
                            SimpleNamespace(id="one"), SimpleNamespace(id="two"),
                        ])
    monkeypatch.setattr(preview_sync.run_scheduled_crm_inbound_sync, "run", completed.append)
    monkeypatch.setattr(preview_sync.process_pending_meeting_email_confirmations,
                        "run", lambda: completed.append("confirmations"))
    assert preview_sync.poll_once() == 2
    assert completed == ["one", "two", "confirmations"]


def test_one_source_failure_does_not_block_other_sources(monkeypatch):
    completed = []
    monkeypatch.setattr(preview_sync, "get_session_factory", lambda: lambda: nullcontext(None))
    monkeypatch.setattr(preview_sync, "claim_due_inbound_sync_states",
                        lambda _session, limit: [
                            SimpleNamespace(id="one"), SimpleNamespace(id="two"),
                        ])

    def run(state_id):
        if state_id == "one":
            raise RuntimeError("test provider unavailable")
        completed.append(state_id)

    monkeypatch.setattr(preview_sync.run_scheduled_crm_inbound_sync, "run", run)
    monkeypatch.setattr(preview_sync.process_pending_meeting_email_confirmations, "run", lambda: 0)
    assert preview_sync.poll_once() == 2
    assert completed == ["two"]


def test_preview_scheduler_cannot_replace_production_scheduler(monkeypatch):
    monkeypatch.setattr(preview_sync, "settings", SimpleNamespace(app_env="production"))
    with pytest.raises(SystemExit, match="production Celery"):
        preview_sync.main()


def test_preview_reasoning_backfill_runs_locally_and_releases_reservation(monkeypatch):
    calls = []

    class InlineExecutor:
        def submit(self, operation, *args):
            future = Future()
            try:
                future.set_result(operation(*args))
            except Exception as error:
                future.set_exception(error)
            return future

    monkeypatch.setattr(preview_sync, "_REASONING_BACKFILL_EXECUTOR", InlineExecutor())
    monkeypatch.setattr(
        preview_sync.reanalyze_workspace_leads,
        "run",
        lambda *args: calls.append(args),
    )
    lock_key = "reasoning-backfill:test"
    job_id = "job-one"

    assert preview_sync.reserve_reasoning_backfill(lock_key, job_id) is None
    assert preview_sync.reserve_reasoning_backfill(lock_key, "job-two") == job_id
    preview_sync.enqueue_reserved_reasoning_backfill(
        lock_key, job_id, "workspace", "connection", "openai|model|prompt"
    )

    assert calls == [("workspace", "connection", job_id, "openai|model|prompt")]
    assert preview_sync.reserve_reasoning_backfill(lock_key, "job-two") is None
    preview_sync.release_reasoning_backfill(lock_key, "job-two")
