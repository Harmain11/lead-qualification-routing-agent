"""Preview-only provider scheduler, without requiring a separate Redis service."""

from __future__ import annotations

import logging
import signal
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from lead_agent.application.crm_inbound import claim_due_inbound_sync_states
from lead_agent.infrastructure.db.models import CrmInboundSyncStateORM
from lead_agent.infrastructure.db.session import get_session_factory
from lead_agent.worker.celery_app import (
    process_pending_meeting_email_confirmations,
    reanalyze_workspace_leads,
    run_scheduled_crm_inbound_sync,
    settings,
)

logger = logging.getLogger(__name__)
_MANUAL_SYNC_EXECUTOR = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="preview-gmail-sync"
)
_REASONING_BACKFILL_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="preview-reasoning-backfill"
)
_REASONING_BACKFILL_LOCK = threading.Lock()
_REASONING_BACKFILL_JOBS: dict[str, str] = {}


def reserve_reasoning_backfill(lock_key: str, job_id: str) -> str | None:
    """Deduplicate preview backfills in-process when Redis/Celery are not running."""
    with _REASONING_BACKFILL_LOCK:
        existing_job_id = _REASONING_BACKFILL_JOBS.get(lock_key)
        if existing_job_id is not None:
            return existing_job_id
        _REASONING_BACKFILL_JOBS[lock_key] = job_id
    return None


def release_reasoning_backfill(lock_key: str, job_id: str) -> None:
    with _REASONING_BACKFILL_LOCK:
        if _REASONING_BACKFILL_JOBS.get(lock_key) == job_id:
            del _REASONING_BACKFILL_JOBS[lock_key]


def enqueue_reserved_reasoning_backfill(
    lock_key: str,
    job_id: str,
    workspace_id: str,
    connection_id: str,
    config_key: str,
) -> None:
    """Run the same backfill task in a preview thread instead of a Celery broker."""
    with _REASONING_BACKFILL_LOCK:
        if _REASONING_BACKFILL_JOBS.get(lock_key) != job_id:
            raise RuntimeError("Preview reasoning backfill reservation was lost")
    future = _REASONING_BACKFILL_EXECUTOR.submit(
        _run_reasoning_backfill, workspace_id, connection_id, job_id, config_key
    )
    future.add_done_callback(
        lambda _future: release_reasoning_backfill(lock_key, job_id)
    )


def _run_reasoning_backfill(
    workspace_id: str, connection_id: str, job_id: str, config_key: str
) -> Any:
    try:
        return reanalyze_workspace_leads.run(
            workspace_id, connection_id, job_id, config_key
        )
    except Exception:
        logger.exception("preview.reasoning_backfill_failed")
        raise


def enqueue_manual_sync(state_id: str, **task_kwargs: Any) -> None:
    """Run manual preview syncs locally without requiring the production Redis queue."""
    future = _MANUAL_SYNC_EXECUTOR.submit(
        _run_manual_sync, state_id, **task_kwargs
    )
    future.add_done_callback(_log_manual_sync_failure)


def _run_manual_sync(state_id: str, **task_kwargs: Any) -> Any:
    try:
        return run_scheduled_crm_inbound_sync.run(state_id, **task_kwargs)
    except Exception:
        with get_session_factory()() as session:
            state = session.get(CrmInboundSyncStateORM, UUID(state_id))
            if state is not None and state.status in {"queued", "running"}:
                state.status = "failed"
                state.error_category = "temporarily_unavailable"
                state.error_code = "preview_worker_failed"
                state.last_error = "The Gmail sync worker failed before completing the run."
                state.lease_owner = None
                state.lease_expires_at = None
                state.updated_at = datetime.now(UTC)
                session.commit()
        raise


def _log_manual_sync_failure(future: Future[Any]) -> None:
    try:
        future.result()
    except Exception:
        logger.exception("preview.manual_inbound_sync_failed")


def poll_once() -> int:
    with get_session_factory()() as session:
        ids = [str(state.id) for state in claim_due_inbound_sync_states(session, limit=20)]
    for state_id in ids:
        try:
            # Call the task locally; production still uses its Celery worker/beat.
            run_scheduled_crm_inbound_sync.run(state_id)
        except Exception:
            logger.exception("preview.inbound_sync_failed")
    process_pending_meeting_email_confirmations.run()
    return len(ids)


def main() -> None:
    if settings.app_env == "production":
        raise SystemExit("Use the production Celery scheduler, not the preview scheduler.")
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    logger.info("preview.automatic_sync_started")
    while not stopped.is_set():
        try:
            poll_once()
        except Exception:
            logger.exception("preview.automatic_sync_cycle_failed")
        stopped.wait(settings.crm_inbound_scheduler_interval_seconds)


if __name__ == "__main__":
    main()
