"""Preview-only provider scheduler, without requiring a separate Redis service."""

from __future__ import annotations

import logging
import signal
import threading

from lead_agent.application.crm_inbound import claim_due_inbound_sync_states
from lead_agent.infrastructure.db.session import get_session_factory
from lead_agent.worker.celery_app import (
    process_pending_meeting_email_confirmations,
    run_scheduled_crm_inbound_sync,
    settings,
)

logger = logging.getLogger(__name__)


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
