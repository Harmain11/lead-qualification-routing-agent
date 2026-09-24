import logging
from uuid import UUID

from celery import Celery  # type: ignore[import-untyped]
from celery.signals import task_failure, task_retry  # type: ignore[import-untyped]

from lead_agent.application.crm_inbound import (
    claim_due_inbound_sync_states,
    run_inbound_sync,
)
from lead_agent.infrastructure.crm.inbound_factory import get_workspace_inbound_crm_resolver
from lead_agent.infrastructure.db.models import CrmInboundSyncStateORM, ProviderConnectionORM
from lead_agent.infrastructure.db.session import get_session_factory
from lead_agent.infrastructure.notifications.factory import get_notification_provider
from lead_agent.infrastructure.observability import configure_logging
from lead_agent.infrastructure.reasoning.factory import get_reasoning_provider
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
    beat_schedule={
        "dispatch-due-crm-inbound-syncs": {
            "task": "lead_agent.dispatch_due_crm_inbound_syncs",
            "schedule": settings.crm_inbound_scheduler_interval_seconds,
        }
    },
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


@celery_app.task(  # type: ignore[untyped-decorator]
    name="lead_agent.dispatch_due_crm_inbound_syncs"
)
def dispatch_due_crm_inbound_syncs() -> int:
    """Fan out due provider states so one workspace failure cannot block another."""

    with get_session_factory()() as session:
        states = claim_due_inbound_sync_states(session, limit=100)
        state_ids = [str(state.id) for state in states]
    for state_id in state_ids:
        run_scheduled_crm_inbound_sync.delay(state_id)
    return len(state_ids)


@celery_app.task(
    bind=True,
    name="lead_agent.run_scheduled_crm_inbound_sync",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
)  # type: ignore[untyped-decorator]
def run_scheduled_crm_inbound_sync(self: object, state_id: str) -> str:
    del self
    with get_session_factory()() as session:
        state = session.get(CrmInboundSyncStateORM, UUID(state_id))
        if state is None or not state.enabled:
            return "skipped"
        connection = session.get(ProviderConnectionORM, state.connection_id)
        if (
            connection is None
            or connection.workspace_id != state.workspace_id
            or not connection.is_active
            or connection.status not in {"connected", "verified"}
        ):
            return "skipped"
        provider = get_workspace_inbound_crm_resolver().resolve(
            session,
            state.workspace_id,
            state.connection_id,
            actor_id="system:crm-inbound-scheduler",
        )
        icp, routing = settings.load_policies()
        run = run_inbound_sync(
            session,
            connection,
            provider,
            mode="incremental" if state.initial_import_completed else "initial",
            object_type=state.object_type,
            limit=settings.crm_inbound_batch_limit,
            page_size=settings.crm_inbound_page_size,
            actor_id="system:crm-inbound-scheduler",
            icp=icp,
            routing=routing,
            reasoning_provider=get_reasoning_provider(),
            notification_provider=get_notification_provider(),
        )
        return run.status
