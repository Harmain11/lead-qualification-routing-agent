from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_policies, require_permission
from lead_agent.api.schemas import (
    CrmInboundSyncAcceptedResponse,
    CrmInboundSyncCancellationResponse,
    CrmInboundSyncRunResponse,
    CrmInboundSyncStateResponse,
    crm_inbound_run_response,
    crm_inbound_state_response,
)
from lead_agent.application.crm_inbound import (
    configure_inbound_sync,
    get_inbound_sync_state,
    inbound_sync_lease_is_active,
    list_inbound_sync_runs,
    request_inbound_sync_cancellation,
    run_inbound_sync,
)
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.domain.routing import NotificationProvider
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.crm.inbound_factory import (
    WorkspaceInboundCrmProviderResolver,
    get_workspace_inbound_crm_resolver,
)
from lead_agent.infrastructure.db.models import ProviderConnectionORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.notifications.factory import get_notification_provider
from lead_agent.infrastructure.rate_limit import rate_limit_dependency
from lead_agent.infrastructure.repositories import get_provider_connection
from lead_agent.settings.config import get_settings

router = APIRouter(prefix="/v1/integrations/connections", tags=["crm-inbound"])
_LOGGER = logging.getLogger("lead_agent.api.crm_inbound")


class InboundSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["initial", "incremental", "manual", "review_recent"] = "manual"
    object_type: Literal["Lead", "contacts", "message"] = "Lead"
    limit: int = Field(default=500, ge=1, le=5000)
    page_size: int = Field(default=100, ge=1, le=200)
    send_confirmation_email: bool = True


class InboundSyncConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    object_type: Literal["Lead", "contacts", "message"] = "Lead"
    interval_seconds: int | None = Field(default=None, ge=60, le=86_400)


def _connection(session: Session, workspace_id: UUID, connection_id: UUID) -> ProviderConnectionORM:
    row = get_provider_connection(session, workspace_id, connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Provider connection not found")
    return row


@router.put(
    "/{connection_id}/inbound-sync/configuration",
    response_model=CrmInboundSyncStateResponse,
    dependencies=[Depends(rate_limit_dependency("provider_actions"))],
)
def update_inbound_sync_configuration(
    connection_id: UUID,
    request: InboundSyncConfigurationRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
) -> CrmInboundSyncStateResponse:
    connection = _connection(session, workspace.workspace_id, connection_id)
    if (
        connection.provider_type not in {"gmail", "outlook"}
        and "crm.sync" not in workspace.permissions
    ):
        raise HTTPException(status_code=403, detail="permission required: crm.sync")
    try:
        state = configure_inbound_sync(
            session,
            connection,
            enabled=request.enabled,
            object_type=request.object_type,
            interval_seconds=request.interval_seconds,
            actor_id=workspace.actor_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return crm_inbound_state_response(state)


@router.post(
    "/{connection_id}/inbound-sync",
    response_model=None,
    dependencies=[Depends(rate_limit_dependency("provider_actions"))],
)
def synchronize_inbound_crm(
    connection_id: UUID,
    request: InboundSyncRequest,
    response: Response,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    resolver: WorkspaceInboundCrmProviderResolver = Depends(get_workspace_inbound_crm_resolver),
    workspace: WorkspaceContext = Depends(require_permission("crm.sync")),
) -> CrmInboundSyncRunResponse | CrmInboundSyncAcceptedResponse:
    connection = _connection(session, workspace.workspace_id, connection_id)
    if connection.provider_type == "gmail" and request.object_type == "message":
        state = get_inbound_sync_state(
            session, workspace.workspace_id, connection_id, request.object_type
        )
        if state is None or not state.enabled:
            raise HTTPException(status_code=409, detail="inbound synchronization is disabled")
        queued_at = datetime.now(UTC)
        if (
            state.status in {"queued", "cancel_requested"}
            or (
                state.status == "running"
                and inbound_sync_lease_is_active(state, queued_at)
            )
        ):
            raise HTTPException(
                status_code=409,
                detail="an inbound synchronization run is already active",
            )
        state.status = "queued"
        state.last_error = None
        state.error_category = None
        state.error_code = None
        state.next_sync_at = queued_at + timedelta(seconds=state.sync_interval_seconds)
        state.updated_at = queued_at
        session.commit()
        try:
            task_kwargs = {
                "requested_mode": request.mode,
                "requested_limit": request.limit,
                "requested_page_size": request.page_size,
                "requested_actor_id": workspace.actor_id,
                "requested_send_confirmation_email": False
                if request.mode == "review_recent"
                else request.send_confirmation_email,
            }
            if get_settings().app_env == "development":
                from lead_agent.worker.preview_sync import enqueue_manual_sync

                enqueue_manual_sync(str(state.id), **task_kwargs)
            else:
                from lead_agent.worker.celery_app import run_scheduled_crm_inbound_sync

                run_scheduled_crm_inbound_sync.apply_async(
                    args=[str(state.id)],
                    kwargs=task_kwargs,
                )
        except Exception as error:
            state.status = "failed"
            state.error_category = "temporarily_unavailable"
            state.error_code = "gmail_sync_queue_unavailable"
            state.last_error = "Gmail sync could not be queued because the worker is unavailable."
            state.updated_at = datetime.now(UTC)
            session.commit()
            _LOGGER.warning(
                "gmail.inbound_sync_queue_failed",
                extra={
                    "fields": {
                        "connection_id": str(connection.id),
                        "error_class": error.__class__.__name__,
                    }
                },
            )
            raise HTTPException(status_code=503, detail=state.last_error) from None
        response.status_code = 202
        return CrmInboundSyncAcceptedResponse(
            queued_at=queued_at,
            message="Gmail sync queued. Progress and results will update here.",
        )
    try:
        provider = resolver.resolve(
            session,
            workspace.workspace_id,
            connection_id,
            actor_id=workspace.actor_id,
        )
        run = run_inbound_sync(
            session,
            connection,
            provider,
            mode=request.mode,
            object_type=request.object_type,
            limit=request.limit,
            page_size=request.page_size,
            actor_id=workspace.actor_id,
            icp=policies[0],
            routing=policies[1],
            reasoning_provider=None,
            notification_provider=notification_provider,
            send_confirmation_email=(
                False
                if connection.provider_type == "gmail" and request.mode == "review_recent"
                else request.send_confirmation_email
            ),
        )
    except ProviderOperationError as error:
        status_code = 503 if error.retryable else 409
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if (
        connection.provider_type == "gmail"
        and request.object_type == "message"
        and request.mode != "review_recent"
    ):
        try:
            from lead_agent.application.meeting_email import process_recent_gmail_imports

            process_recent_gmail_imports(
                session,
                connection=connection,
                actor_id=workspace.actor_id,
                since=run.started_at,
            )
        except Exception:
            _LOGGER.exception(
                "meeting_email.manual_inbound_sync_failed",
                extra={
                    "fields": {
                        "workspace_id": str(workspace.workspace_id),
                        "connection_id": str(connection.id),
                    }
                },
            )
    return crm_inbound_run_response(run)


@router.get(
    "/{connection_id}/inbound-sync/state",
    response_model=CrmInboundSyncStateResponse,
)
def retrieve_inbound_sync_state(
    connection_id: UUID,
    object_type: Literal["Lead", "contacts", "message"] = "Lead",
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.view")),
) -> CrmInboundSyncStateResponse:
    _connection(session, workspace.workspace_id, connection_id)
    row = get_inbound_sync_state(session, workspace.workspace_id, connection_id, object_type)
    if row is None:
        raise HTTPException(status_code=404, detail="Inbound synchronization state not found")
    return crm_inbound_state_response(row)


@router.get(
    "/{connection_id}/inbound-sync/runs",
    response_model=list[CrmInboundSyncRunResponse],
)
def retrieve_inbound_sync_runs(
    connection_id: UUID,
    limit: int = 25,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.view")),
) -> list[CrmInboundSyncRunResponse]:
    _connection(session, workspace.workspace_id, connection_id)
    return [
        crm_inbound_run_response(row)
        for row in list_inbound_sync_runs(
            session, workspace.workspace_id, connection_id, limit=limit
        )
    ]


@router.post(
    "/{connection_id}/inbound-sync/cancel",
    response_model=CrmInboundSyncCancellationResponse,
    dependencies=[Depends(rate_limit_dependency("provider_actions"))],
)
def cancel_inbound_sync(
    connection_id: UUID,
    object_type: Literal["message"] = "message",
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("crm.sync")),
) -> CrmInboundSyncCancellationResponse:
    connection = _connection(session, workspace.workspace_id, connection_id)
    if connection.provider_type != "gmail":
        raise HTTPException(
            status_code=409,
            detail="Cancellation is currently supported for Gmail syncs only.",
        )
    result = request_inbound_sync_cancellation(
        session,
        workspace.workspace_id,
        connection_id,
        object_type,
        workspace.actor_id,
    )
    return CrmInboundSyncCancellationResponse(**result)
