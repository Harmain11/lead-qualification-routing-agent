from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_policies, require_permission
from lead_agent.api.schemas import (
    CrmInboundSyncRunResponse,
    CrmInboundSyncStateResponse,
    crm_inbound_run_response,
    crm_inbound_state_response,
)
from lead_agent.application.crm_inbound import (
    configure_inbound_sync,
    get_inbound_sync_state,
    list_inbound_sync_runs,
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

router = APIRouter(prefix="/v1/integrations/connections", tags=["crm-inbound"])
_LOGGER = logging.getLogger("lead_agent.api.crm_inbound")


class InboundSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["initial", "incremental", "manual"] = "manual"
    object_type: Literal["Lead", "contacts", "message"] = "Lead"
    limit: int = Field(default=500, ge=1, le=5000)
    page_size: int = Field(default=100, ge=1, le=200)
    send_confirmation_email: bool = True


class InboundSyncConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    object_type: Literal["Lead", "contacts", "message"] = "Lead"
    interval_seconds: int = Field(default=300, ge=60, le=86_400)


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
    response_model=CrmInboundSyncRunResponse,
    dependencies=[Depends(rate_limit_dependency("provider_actions"))],
)
def synchronize_inbound_crm(
    connection_id: UUID,
    request: InboundSyncRequest,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    resolver: WorkspaceInboundCrmProviderResolver = Depends(get_workspace_inbound_crm_resolver),
    workspace: WorkspaceContext = Depends(require_permission("crm.sync")),
) -> CrmInboundSyncRunResponse:
    connection = _connection(session, workspace.workspace_id, connection_id)
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
            send_confirmation_email=request.send_confirmation_email,
        )
    except ProviderOperationError as error:
        status_code = 503 if error.retryable else 409
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if connection.provider_type == "gmail" and request.object_type == "message":
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
