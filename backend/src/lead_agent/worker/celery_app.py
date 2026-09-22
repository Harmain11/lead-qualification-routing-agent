import logging

from celery import Celery  # type: ignore[import-untyped]
from celery.signals import task_failure, task_retry  # type: ignore[import-untyped]

from lead_agent.infrastructure.observability import configure_logging
from lead_agent.settings.config import get_settings

settings = get_settings()
configure_logging(json_logs=settings.log_format == "json", level=settings.log_level)
_LOGGER = logging.getLogger("lead_agent.worker")
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
    worker_prefetch_multiplier=1,
    task_default_retry_delay=5,
    task_soft_time_limit=240,
    task_time_limit=300,
    task_publish_retry=True,
    task_publish_retry_policy={
        "max_retries": 3,
        "interval_start": 0,
        "interval_step": 0.5,
        "interval_max": 2,
    },
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=10,
    result_expires=86400,
    broker_transport_options={"visibility_timeout": 3600},
    result_backend_transport_options={"visibility_timeout": 3600},
)
# Celery has no effective global ``task_max_retries`` configuration key. Set the
# application task base so every future registered task inherits the configured bound.
celery_app.Task.max_retries = settings.job_max_retries


def _on_task_failure(
    sender: object | None = None,
    task_id: str | None = None,
    exception: BaseException | None = None,
    **_kwargs: object,
) -> None:
    _LOGGER.error(
        "background_job.failure",
        extra={
            "fields": {
                "task_name": getattr(sender, "name", "unknown"),
                "task_id": task_id,
                "error_type": type(exception).__name__ if exception else "unknown",
            }
        },
    )


def _on_task_retry(request: object | None = None, **_kwargs: object) -> None:
    task = getattr(request, "task", None)
    retries = getattr(request, "retries", None)
    limit = settings.job_max_retries
    if retries is not None and retries >= limit:
        _LOGGER.warning(
            "background_job.retry_exhausted",
            extra={
                "fields": {
                    "task_name": getattr(task, "name", "unknown"),
                    "task_id": getattr(request, "id", None),
                    "attempts": retries,
                }
            },
        )


task_failure.connect(_on_task_failure)
task_retry.connect(_on_task_retry)


# No application jobs are registered yet. New idempotent jobs should use bounded
# task retries, late acknowledgements, and the durable database state as authority.
