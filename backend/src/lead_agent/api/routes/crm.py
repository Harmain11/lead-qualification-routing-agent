from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_active_crm_provider, get_workspace_context
from lead_agent.api.schemas import CrmSyncResponse, crm_sync_response
from lead_agent.application.crm import retry_crm_sync, sync_lead_to_crm
from lead_agent.domain.crm import CrmProvider
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import CrmSyncORM, LeadORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.repositories import (
    get_current_crm_sync,
    get_lead,
    list_crm_sync_history,
)
from lead_agent.settings.config import Settings, get_settings

router = APIRouter(prefix="/v1/leads", tags=["crm"])


def _lead_or_404(lead_id: UUID, session: Session, workspace_id: UUID) -> LeadORM:
    lead = get_lead(session, lead_id, workspace_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def _required_response(row: CrmSyncORM | None) -> CrmSyncResponse:
    response = crm_sync_response(row)
    if response is None:
        raise HTTPException(status_code=404, detail="CRM synchronization not found")
    return response


@router.get("/{lead_id}/crm-sync", response_model=CrmSyncResponse)
def retrieve_crm_sync(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> CrmSyncResponse:
    _lead_or_404(lead_id, session, workspace.workspace_id)
    return _required_response(
        get_current_crm_sync(session, lead_id, workspace_id=workspace.workspace_id)
    )


@router.get("/{lead_id}/crm-sync/history", response_model=list[CrmSyncResponse])
def retrieve_crm_sync_history(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> list[CrmSyncResponse]:
    _lead_or_404(lead_id, session, workspace.workspace_id)
    return [
        _required_response(row)
        for row in list_crm_sync_history(session, lead_id, workspace.workspace_id)
    ]


@router.post("/{lead_id}/crm-sync", response_model=CrmSyncResponse)
def synchronize_crm(
    lead_id: UUID,
    session: Session = Depends(get_db),
    provider: CrmProvider = Depends(get_active_crm_provider),
    settings: Settings = Depends(get_settings),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> CrmSyncResponse:
    lead = _lead_or_404(lead_id, session, workspace.workspace_id)
    row = sync_lead_to_crm(
        session,
        lead,
        provider,
        payload_version=settings.load_crm_config().payload_version,
        max_attempts=settings.crm_max_attempts,
    )
    return _required_response(row)


@router.post("/{lead_id}/crm-sync/retry", response_model=CrmSyncResponse)
def retry_crm(
    lead_id: UUID,
    session: Session = Depends(get_db),
    provider: CrmProvider = Depends(get_active_crm_provider),
    settings: Settings = Depends(get_settings),
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> CrmSyncResponse:
    lead = _lead_or_404(lead_id, session, workspace.workspace_id)
    try:
        row = retry_crm_sync(
            session,
            lead,
            provider,
            max_attempts=settings.crm_max_attempts,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _required_response(row)
