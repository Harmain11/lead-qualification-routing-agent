import hashlib
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from celery import Celery  # type: ignore[import-untyped]
from celery.signals import task_failure, task_retry  # type: ignore[import-untyped]
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select

from lead_agent.application.governance import operations_policy
from lead_agent.application.icp import resolve_workspace_icp
from lead_agent.application.crm_inbound import (
    claim_due_inbound_sync_states,
    finish_queued_inbound_sync_cancellation,
    inbound_sync_lease_is_active,
    run_inbound_sync,
)
from lead_agent.application.manual_review import workspace_manual_review_config
from lead_agent.application.reasoning_cache import reasoning_request_fingerprint
from lead_agent.application.services import reprocess_lead
from lead_agent.domain.normalization import normalize_lead
from lead_agent.infrastructure.crm.inbound_factory import get_workspace_inbound_crm_resolver
from lead_agent.infrastructure.db.models import (
    CrmInboundSyncStateORM,
    LeadORM,
    ProviderConnectionORM,
    ProviderLifecycleAuditORM,
)
from lead_agent.infrastructure.db.session import get_session_factory
from lead_agent.infrastructure.notifications.factory import get_notification_provider
from lead_agent.infrastructure.observability import configure_logging
from lead_agent.infrastructure.reasoning.configuration import REASONING_PROVIDERS
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
        "workspace-operations": {"task": "lead_agent.workspace_operations", "schedule": 30},
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
def run_scheduled_crm_inbound_sync(
    self: object,
    state_id: str,
    requested_mode: str | None = None,
    requested_limit: int | None = None,
    requested_page_size: int | None = None,
    requested_actor_id: str | None = None,
    requested_send_confirmation_email: bool = True,
) -> str:
    del self
    with get_session_factory()() as session:
        state = session.get(CrmInboundSyncStateORM, UUID(state_id))
        if state is None:
            return "skipped"
        connection = session.get(ProviderConnectionORM, state.connection_id)
        if state.status == "cancel_requested" and connection is not None:
            if inbound_sync_lease_is_active(state):
                return "cancellation_requested"
            finish_queued_inbound_sync_cancellation(
                session,
                connection,
                state,
                mode=requested_mode or (
                    "incremental" if state.initial_import_completed else "initial"
                ),
                actor_id=requested_actor_id or "system:crm-inbound-scheduler",
            )
            return "cancelled"
        if not state.enabled:
            if state.status == "queued":
                state.status = "failed"
                state.error_category = "configuration"
                state.error_code = "gmail_sync_disabled_before_start"
                state.last_error = "Sync was disabled before the queued run started."
                state.updated_at = datetime.now(UTC)
                session.commit()
            return "skipped"
        if (
            connection is None
            or connection.workspace_id != state.workspace_id
            or not connection.is_active
            or connection.status not in {"connected", "verified"}
        ):
            if state.status == "queued":
                state.status = "failed"
                state.error_category = "configuration"
                state.error_code = "provider_connection_unavailable"
                state.last_error = "The provider connection became unavailable before sync started."
                state.updated_at = datetime.now(UTC)
                session.commit()
            return "skipped"
        provider = get_workspace_inbound_crm_resolver().resolve(
            session,
            state.workspace_id,
            state.connection_id,
            actor_id="system:crm-inbound-scheduler",
        )
        if (
            connection.provider_type == "gmail"
            and state.object_type == "message"
            and not callable(getattr(provider, "lead_extractor", None))
        ):
            state.status = "failed"
            state.error_category = "configuration"
            state.error_code = "gmail_ai_credentials_unavailable"
            state.last_error = (
                "Gmail AI lead detection is unavailable. Connect or re-verify a "
                "workspace OpenAI credential, then retry."
            )
            state.updated_at = datetime.now(UTC)
            session.commit()
            return "failed"
        icp, routing = settings.load_policies()
        run = run_inbound_sync(
            session,
            connection,
            provider,
            mode=requested_mode or (
                "incremental" if state.initial_import_completed else "initial"
            ),
            object_type=state.object_type,
            limit=requested_limit or settings.crm_inbound_batch_limit,
            page_size=requested_page_size or settings.crm_inbound_page_size,
            actor_id=requested_actor_id or "system:crm-inbound-scheduler",
            icp=icp,
            routing=routing,
            reasoning_provider=None,
            notification_provider=get_notification_provider(),
            send_confirmation_email=requested_send_confirmation_email,
        )
        if (
            run.status != "cancelled"
            and connection.provider_type == "gmail"
            and state.object_type == "message"
        ):
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


@celery_app.task(name="lead_agent.process_pending_meeting_email_confirmations")
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
def reanalyze_workspace_leads(
    self: object,
    workspace_id: str,
    connection_id: str,
    job_id: str,
    config_key: str,
) -> str:
    """Apply the verified workspace provider to eligible, not-yet-analyzed leads."""
    del self
    workspace_uuid = UUID(workspace_id)
    with get_session_factory()() as session:
        icp, routing = settings.load_policies()
        provider = resolve_workspace_reasoning_provider(session, workspace_uuid)
        provider_name = getattr(provider, "provider_name", "")
        model_name = getattr(provider, "model_name", "")
        if provider_name not in REASONING_PROVIDERS:
            return "skipped: verified workspace reasoning provider is unavailable"
        connection = session.get(ProviderConnectionORM, UUID(connection_id))
        if (
            connection is None
            or connection.workspace_id != workspace_uuid
            or connection.provider_type != provider_name
            or not connection.is_active
            or connection.status != "verified"
            or (
                f"{provider_name}|{model_name}|{getattr(provider, 'prompt_version', '')}"
                != config_key
            )
        ):
            return "skipped: verified workspace reasoning connection is unavailable"
        correlation_id = job_id
        lock_hash = hashlib.sha256(config_key.encode()).hexdigest()[:24]
        lock_key = f"reasoning-backfill:{workspace_id}:{connection_id}:{lock_hash}"

        def record(outcome: str, details: dict[str, object]) -> None:
            session.add(
                ProviderLifecycleAuditORM(
                    id=uuid4(),
                    workspace_id=workspace_uuid,
                    connection_id=connection.id,
                    provider=provider_name,
                    actor_id="reasoning-backfill-worker",
                    action="reasoning_backfill",
                    outcome=outcome,
                    correlation_id=correlation_id,
                    details={
                        "provider": provider_name,
                        "model": model_name,
                        "config_key": config_key,
                        **details,
                    },
                    created_at=datetime.now(UTC),
                )
            )
            session.commit()

        def release_lock() -> None:
            client = Redis.from_url(
                settings.redis_url, socket_connect_timeout=1, socket_timeout=1
            )
            try:
                client.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('del', KEYS[1]) else return 0 end",
                    1,
                    lock_key,
                    job_id,
                )
            except (RedisError, OSError, ValueError):
                _LOGGER.warning("reasoning.backfill_lock_release_failed")
            finally:
                client.close()

        lead_ids = list(
            session.scalars(
                select(LeadORM.id)
                .where(LeadORM.workspace_id == workspace_uuid)
                .order_by(LeadORM.created_at.asc())
            )
        )
        record("running", {
            "total": len(lead_ids),
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "status": "no_eligible_leads" if not lead_ids else "running",
        })
        if not lead_ids:
            record("no_eligible_leads", {
                "total": 0, "completed": 0, "failed": 0, "skipped": 0,
                "status": "no_eligible_leads",
            })
            release_lock()
            return "no eligible leads"
        completed = 0
        failed = 0
        skipped = 0
        from lead_agent.domain.analysis import (
            ReasoningProviderTimeout,
            ReasoningProviderUnavailable,
        )
        retryable_provider_errors = (ReasoningProviderTimeout, ReasoningProviderUnavailable)

        for lead_id in lead_ids:
            lead = session.get(LeadORM, lead_id)
            if lead is None:
                skipped += 1
                continue
            existing = lead.analysis
            if (
                existing is not None
                and existing.provider == provider_name
                and existing.model == model_name
                and existing.prompt_version == getattr(provider, "prompt_version", "")
                and existing.status in {"completed", "manual_review"}
            ):
                skipped += 1
                record("running", {
                    "total": len(lead_ids), "completed": completed,
                    "failed": failed, "skipped": skipped,
                    "current_lead_id": str(lead_id),
                })
                continue
            prior_completion = session.scalar(
                select(ProviderLifecycleAuditORM.id)
                .where(
                    ProviderLifecycleAuditORM.workspace_id == workspace_uuid,
                    ProviderLifecycleAuditORM.connection_id == connection.id,
                    ProviderLifecycleAuditORM.action == "reasoning_backfill",
                    ProviderLifecycleAuditORM.outcome == "lead_completed",
                    ProviderLifecycleAuditORM.details["lead_id"].as_string() == str(lead_id),
                    ProviderLifecycleAuditORM.details["config_key"].as_string() == config_key,
                )
                .limit(1)
            )
            if prior_completion:
                skipped += 1
                record("running", {
                    "total": len(lead_ids), "completed": completed,
                    "failed": failed, "skipped": skipped,
                    "current_lead_id": str(lead_id),
                })
                continue
            if existing is not None and existing.status == "skipped_hard_disqualifier":
                skipped += 1
                record("running", {
                    "total": len(lead_ids), "completed": completed,
                    "failed": failed, "skipped": skipped,
                    "current_lead_id": str(lead_id),
                })
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
                        float(lead.budget) if isinstance(lead.budget, Decimal) else lead.budget
                    ),
                    "product_or_service": lead.product_or_service,
                    "message": lead.message,
                    "received_at": lead.received_at,
                }
                for attempt in range(3):
                    try:
                        reprocess_lead(
                            session,
                            lead,
                            payload,
                            icp,
                            routing,
                            provider=provider,
                            notification_provider=get_notification_provider(),
                            send_notification=False,
                            send_confirmation_email=False,
                            require_provider_success=True,
                            reroute_reason=f"Workspace {provider_name.title()} setup backfill.",
                        )
                        break
                    except retryable_provider_errors:
                        session.rollback()
                        if attempt == 2:
                            raise
                        time.sleep(0.2 * (attempt + 1))
                completed += 1
                record(
                    "lead_completed",
                    {
                        "lead_id": str(lead_id),
                        "status": "running",
                        "total": len(lead_ids),
                        "completed": completed,
                        "failed": failed,
                        "skipped": skipped,
                    },
                )
            except Exception as error:
                session.rollback()
                failed += 1
                _LOGGER.warning(
                    "reasoning.backfill_lead_failed",
                    extra={
                        "fields": {
                            "workspace_id": workspace_id,
                            "lead_id": str(lead_id),
                            "error_type": type(error).__name__,
                        }
                    },
                )
            record("running", {
                "total": len(lead_ids), "completed": completed,
                "failed": failed, "skipped": skipped,
                "current_lead_id": str(lead_id),
            })
        terminal = (
            "partial_failure" if failed and completed else "failed" if failed else "completed"
        )
        record(terminal, {
            "total": len(lead_ids),
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
            "status": terminal,
            "current_lead_id": None,
        })
        release_lock()
        return f"{terminal}: {completed} completed, {failed} failed, {skipped} skipped"


@celery_app.task(
    bind=True,
    name="lead_agent.analyze_workspace_lead",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
)  # type: ignore[untyped-decorator]
def analyze_workspace_lead(self: object, workspace_id: str, lead_id: str) -> str:
    """Reconcile one new or materially updated lead with its active AI configuration."""
    del self
    workspace_uuid, lead_uuid = UUID(workspace_id), UUID(lead_id)
    with get_session_factory()() as session:
        lead = session.get(LeadORM, lead_uuid)
        if lead is None or lead.workspace_id != workspace_uuid:
            return "skipped: lead not found"
        provider = resolve_workspace_reasoning_provider(session, workspace_uuid)
        provider_name = getattr(provider, "provider_name", "")
        model_name = getattr(provider, "model_name", "")
        prompt_version = getattr(provider, "prompt_version", "")
        if provider_name not in REASONING_PROVIDERS:
            return "skipped: verified workspace reasoning provider is unavailable"
        connection = session.scalar(
            select(ProviderConnectionORM).where(
                ProviderConnectionORM.workspace_id == workspace_uuid,
                ProviderConnectionORM.provider_type == provider_name,
                ProviderConnectionORM.status == "verified",
                ProviderConnectionORM.is_active.is_(True),
            )
        )
        if connection is None:
            return "skipped: verified workspace connection is unavailable"
        config_key = f"{provider_name}|{model_name}|{prompt_version}"
        icp, routing = settings.load_policies()
        resolved_icp = resolve_workspace_icp(session, workspace_uuid, icp)
        business_profile = operations_policy(session, workspace_uuid).business_profile
        manual_review_config = workspace_manual_review_config(
            session, workspace_uuid, routing.manual_review
        )
        payload: dict[str, object] = {
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
            "budget": float(lead.budget) if isinstance(lead.budget, Decimal) else lead.budget,
            "product_or_service": lead.product_or_service,
            "message": lead.message,
            "received_at": lead.received_at,
            "raw_payload": lead.raw_payload,
            "normalization_version": lead.normalization_version,
        }
        normalized = normalize_lead(payload, lead.id)
        request_fingerprint = reasoning_request_fingerprint(
            workspace_id=workspace_uuid,
            lead=normalized,
            icp=resolved_icp.configuration,
            routing=routing,
            provider=provider,
            manual_review_config=manual_review_config,
            business_profile=business_profile,
            analyze_hard_disqualified=False,
            icp_enabled=resolved_icp.source == "workspace",
        )
        existing = lead.analysis
        if (
            existing is not None
            and existing.status == "skipped_hard_disqualifier"
            and existing.analysis_request_fingerprint == request_fingerprint
        ):
            return "skipped: unchanged hard disqualifier"
        if (
            existing is not None
            and existing.provider == provider_name
            and existing.model == model_name
            and existing.prompt_version == prompt_version
            and existing.status in {"completed", "manual_review"}
            and existing.analysis_request_fingerprint == request_fingerprint
        ):
            return "skipped: exact analysis input already completed"
        completed_for_config = session.scalar(
            select(ProviderLifecycleAuditORM.id)
            .where(
                ProviderLifecycleAuditORM.workspace_id == workspace_uuid,
                ProviderLifecycleAuditORM.connection_id == connection.id,
                ProviderLifecycleAuditORM.action == "reasoning_backfill",
                ProviderLifecycleAuditORM.outcome == "lead_completed",
                ProviderLifecycleAuditORM.details["lead_id"].as_string() == str(lead_uuid),
                ProviderLifecycleAuditORM.details["config_key"].as_string() == config_key,
                ProviderLifecycleAuditORM.details[
                    "analysis_request_fingerprint"
                ].as_string() == request_fingerprint,
            )
            .limit(1)
        )
        if completed_for_config:
            return "skipped: exact analysis request already completed"
        from lead_agent.domain.analysis import (
            ReasoningProviderTimeout,
            ReasoningProviderUnavailable,
        )

        for attempt in range(3):
            try:
                reprocess_lead(
                    session, lead, payload, icp, routing,
                    provider=provider,
                    notification_provider=get_notification_provider(),
                    send_notification=False,
                    send_confirmation_email=False,
                    require_provider_success=True,
                    reroute_reason="Background analysis for the active workspace model.",
                )
                session.add(
                    ProviderLifecycleAuditORM(
                        id=uuid4(),
                        workspace_id=workspace_uuid,
                        connection_id=connection.id,
                        provider=provider_name,
                        actor_id="reasoning-analysis-worker",
                        action="reasoning_backfill",
                        outcome="lead_completed",
                        correlation_id=str(uuid4()),
                        details={
                            "lead_id": str(lead_uuid),
                            "provider": provider_name,
                            "model": model_name,
                            "config_key": config_key,
                            "analysis_request_fingerprint": request_fingerprint,
                            "status": "completed",
                        },
                        created_at=datetime.now(UTC),
                    )
                )
                session.commit()
                return "completed"
            except (ReasoningProviderTimeout, ReasoningProviderUnavailable):
                session.rollback()
                if attempt == 2:
                    raise
                time.sleep(0.2 * (attempt + 1))
        return "failed"


@celery_app.task(name="lead_agent.workspace_operations")
def workspace_operations():
    from lead_agent.application.automations import poll_operations_once

    with get_session_factory()() as session:
        return poll_operations_once(session)
