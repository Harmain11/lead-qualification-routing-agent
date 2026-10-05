import logging
from decimal import Decimal
from uuid import UUID

from celery import Celery  # type: ignore[import-untyped]
from celery.signals import task_failure, task_retry  # type: ignore[import-untyped]
from sqlalchemy import select

from lead_agent.application.crm_inbound import (
    claim_due_inbound_sync_states,
    run_inbound_sync,
)
from lead_agent.application.services import reprocess_lead
from lead_agent.infrastructure.crm.inbound_factory import get_workspace_inbound_crm_resolver
from lead_agent.infrastructure.db.models import (
    AnalysisRunORM,
    CrmInboundSyncStateORM,
    LeadORM,
    ProviderConnectionORM,
)
from lead_agent.infrastructure.db.session import get_session_factory
from lead_agent.infrastructure.notifications.factory import get_notification_provider
from lead_agent.infrastructure.observability import configure_logging
from lead_agent.infrastructure.reasoning.factory import resolve_workspace_reasoning_provider
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
        },
        "process-pending-meeting-email-confirmations": {
            "task": "lead_agent.process_pending_meeting_email_confirmations",
            "schedule": settings.crm_inbound_scheduler_interval_seconds,
        },
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
            reasoning_provider=None,
            notification_provider=get_notification_provider(),
        )
        if connection.provider_type == "gmail" and state.object_type == "message":
            try:
                from lead_agent.application.meeting_email import (
                    process_recent_gmail_imports,
                )

                process_recent_gmail_imports(
                    session,
                    connection=connection,
                    actor_id="system:crm-inbound-scheduler",
                    since=run.started_at,
                )
            except Exception:
                _LOGGER.exception(
                    "meeting_email.scheduled_inbound_sync_failed",
                    extra={
                        "fields": {
                            "workspace_id": str(state.workspace_id),
                            "connection_id": str(connection.id),
                        }
                    },
                )
        return run.status


@celery_app.task(
    name="lead_agent.process_pending_meeting_email_confirmations"
)
def process_pending_meeting_email_confirmations() -> int:
    from lead_agent.application.meeting_email import (
        process_pending_meeting_confirmations,
    )

    with get_session_factory()() as session:
        return process_pending_meeting_confirmations(session)


@celery_app.task(
    bind=True,
    name="lead_agent.reanalyze_workspace_leads",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
)  # type: ignore[untyped-decorator]
def reanalyze_workspace_leads(self: object, workspace_id: str) -> str:
    """Apply a newly verified workspace OpenAI provider to older non-OpenAI leads."""

    del self
    workspace_uuid = UUID(workspace_id)
    with get_session_factory()() as session:
        icp, routing = settings.load_policies()
        provider = resolve_workspace_reasoning_provider(session, workspace_uuid)
        if getattr(provider, "provider_name", "") != "openai":
            return "skipped: verified workspace OpenAI provider is unavailable"
        openai_completed = select(AnalysisRunORM.id).where(
            AnalysisRunORM.lead_id == LeadORM.id,
            AnalysisRunORM.provider == "openai",
            AnalysisRunORM.status == "completed",
        ).exists()
        lead_ids = list(
            session.scalars(
                select(LeadORM.id)
                .where(
                    LeadORM.workspace_id == workspace_uuid,
                    ~openai_completed,
                )
                .order_by(LeadORM.created_at.asc())
            )
        )
        processed = 0
        failed = 0
        for lead_id in lead_ids:
            lead = session.get(LeadORM, lead_id)
            if lead is None:
                continue
            try:
                payload = {
                    "source": lead.source,
                    "source_detail": lead.source_detail,
                    "external_id": lead.external_id,
                    "full_name": lead.full_name,
                    "email": lead.email,
                    "phone": lead.phone,
                    "job_title": lead.job_title,
                    "company_name": lead.company_name,
                    "industry": lead.industry,
                    "employee_count": lead.employee_count,
                    "annual_revenue": float(lead.annual_revenue)
                    if isinstance(lead.annual_revenue, Decimal)
                    else lead.annual_revenue,
                    "geography": lead.geography,
                    "budget": (
                        float(lead.budget)
                        if isinstance(lead.budget, Decimal)
                        else lead.budget
                    ),
                    "product_or_service": lead.product_or_service,
                    "message": lead.message,
                    "received_at": lead.received_at,
                }
                reprocess_lead(
                    session,
                    lead,
                    payload,
                    icp,
                    routing,
                    provider=provider,
                    notification_provider=get_notification_provider(),
                    send_notification=False,
                    reroute_reason="Workspace OpenAI setup backfill.",
                )
                processed += 1
            except Exception as error:
                session.rollback()
                failed += 1
                _LOGGER.warning(
                    "openai.backfill_lead_failed",
                    extra={
                        "fields": {
                            "workspace_id": workspace_id,
                            "lead_id": str(lead_id),
                            "error_type": type(error).__name__,
                        }
                    },
                )
        return f"completed: {processed} processed, {failed} failed"
