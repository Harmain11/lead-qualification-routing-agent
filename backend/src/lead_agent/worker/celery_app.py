from celery import Celery  # type: ignore[import-untyped]

from lead_agent.settings.config import get_settings

settings = get_settings()
celery_app = Celery(
    "lead_agent",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    task_default_queue=settings.queue_name,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
)

# No application jobs are registered in Phase 1. The boundary exists so future
# asynchronous stages can be added without moving domain/application code.
