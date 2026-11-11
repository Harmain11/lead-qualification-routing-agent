"""Bounded, provider-agnostic inbound sync work for background schedulers.

This module intentionally contains no Celery dependency.  It can be invoked by
the existing Celery worker on long-lived deployments or by a Vercel Cron
function in serverless deployments.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from lead_agent.application.crm_inbound import (
    claim_due_inbound_sync_states,
    finish_queued_inbound_sync_cancellation,
    inbound_sync_lease_is_active,
    run_inbound_sync,
)
from lead_agent.infrastructure.crm.inbound_factory import get_workspace_inbound_crm_resolver
from lead_agent.infrastructure.db.models import CrmInboundSyncStateORM, ProviderConnectionORM
from lead_agent.infrastructure.db.session import get_session_factory
from lead_agent.infrastructure.notifications.factory import get_notification_provider
from lead_agent.settings.config import get_settings

_LOGGER = logging.getLogger("lead_agent.scheduled_sync")
_SYSTEM_ACTOR = "system:crm-inbound-scheduler"


@dataclass(frozen=True)
class ScheduledSyncSummary:
    claimed: int
    completed: int
    skipped: int
    failed: int


def run_claimed_inbound_sync(
    state_id: str,
    *,
    requested_mode: str | None = None,
    requested_limit: int | None = None,
    requested_page_size: int | None = None,
    requested_actor_id: str | None = None,
    requested_send_confirmation_email: bool = True,
) -> str:
    """Run one claimed state with the same checks used by the Celery worker."""

    settings = get_settings()
    actor_id = requested_actor_id or _SYSTEM_ACTOR
    with get_session_factory()() as session:
        state = session.get(CrmInboundSyncStateORM, UUID(state_id))
        if state is None:
            return "skipped"
        connection = session.get(ProviderConnectionORM, state.connection_id)
        mode = requested_mode or (
            "incremental" if state.initial_import_completed else "initial"
        )
        if state.status == "cancel_requested" and connection is not None:
            if inbound_sync_lease_is_active(state):
                return "cancellation_requested"
            finish_queued_inbound_sync_cancellation(
                session, connection, state, mode=mode, actor_id=actor_id
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
            session, state.workspace_id, state.connection_id, actor_id=actor_id
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
            mode=mode,
            object_type=state.object_type,
            limit=requested_limit or settings.crm_inbound_batch_limit,
            page_size=requested_page_size or settings.crm_inbound_page_size,
            actor_id=actor_id,
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
                from lead_agent.application.meeting_email import process_recent_gmail_imports

                process_recent_gmail_imports(
                    session, connection=connection, actor_id=actor_id, since=run.started_at
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


def run_due_inbound_syncs(*, limit: int = 10) -> ScheduledSyncSummary:
    """Claim and run a small amount of due work within a serverless invocation."""

    with get_session_factory()() as session:
        state_ids = [str(state.id) for state in claim_due_inbound_sync_states(session, limit=limit)]
    completed = skipped = failed = 0
    for state_id in state_ids:
        try:
            status = run_claimed_inbound_sync(state_id)
        except Exception:
            failed += 1
            _LOGGER.exception(
                "scheduled_inbound_sync_failed", extra={"fields": {"state_id": state_id}}
            )
            continue
        if status == "completed":
            completed += 1
        elif status == "failed":
            failed += 1
        else:
            skipped += 1
    return ScheduledSyncSummary(
        claimed=len(state_ids), completed=completed, skipped=skipped, failed=failed
    )
