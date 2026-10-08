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
