"""Run scheduled draft preparation and opted-in agents without external sends."""

from __future__ import annotations

import logging
import signal
import threading

from lead_agent.application.automations import poll_operations_once
from lead_agent.infrastructure.db.session import get_session_factory

logger = logging.getLogger(__name__)


def main() -> None:
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    while not stopped.is_set():
        try:
            with get_session_factory()() as session:
                poll_operations_once(session)
        except Exception:
            logger.exception("operations.poll_failed")
        stopped.wait(30)


if __name__ == "__main__":
    main()
