from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_active_crm_provider, require_permission
from lead_agent.api.schemas import CrmSyncResponse, crm_sync_response
from lead_agent.application.crm import manually_retry_crm_sync, manually_sync_lead_to_crm
from lead_agent.domain.crm import CrmProvider, CrmProviderError
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import CrmSyncORM, LeadORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.rate_limit import rate_limit_dependency
from lead_agent.infrastructure.repositories import (
    get_current_crm_sync,
    get_lead,
    list_crm_sync_history,
)
from lead_agent.settings.config import Settings, get_settings

router = APIRouter(prefix="/v1/leads", tags=["crm"])


class BulkCrmSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead_ids: list[UUID] = Field(min_length=1, max_length=100)
    confirmed: bool


class BulkCrmSyncItemResponse(BaseModel):
    lead_id: UUID
    status: str
    crm_sync: CrmSyncResponse | None = None
    error: str | None = None


class BulkCrmSyncResponse(BaseModel):
    requested: int
    succeeded: int
    failed: int
    results: list[BulkCrmSyncItemResponse]


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
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> CrmSyncResponse:
    _lead_or_404(lead_id, session, workspace.workspace_id)
    return _required_response(
        get_current_crm_sync(session, lead_id, workspace_id=workspace.workspace_id)
    )


@router.get("/{lead_id}/crm-sync/history", response_model=list[CrmSyncResponse])
def retrieve_crm_sync_history(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> list[CrmSyncResponse]:
    _lead_or_404(lead_id, session, workspace.workspace_id)
    return [
        _required_response(row)
        for row in list_crm_sync_history(session, lead_id, workspace.workspace_id)
    ]


@router.post(
    "/{lead_id}/crm-sync",
    response_model=CrmSyncResponse,
    dependencies=[Depends(rate_limit_dependency("provider_actions"))],
)
def synchronize_crm(
    lead_id: UUID,
    session: Session = Depends(get_db),
    provider: CrmProvider = Depends(get_active_crm_provider),
    settings: Settings = Depends(get_settings),
    workspace: WorkspaceContext = Depends(require_permission("crm.sync")),
) -> CrmSyncResponse:
    lead = _lead_or_404(lead_id, session, workspace.workspace_id)
    try:
        row = manually_sync_lead_to_crm(
            session,
            lead,
            provider,
            actor_id=workspace.actor_id,
            payload_version=settings.load_crm_config().payload_version,
            max_attempts=settings.crm_max_attempts,
            force=True,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except CrmProviderError as error:
        raise HTTPException(status_code=502, detail=f"{error.code}: {error}") from error
    return _required_response(row)


@router.post(
    "/{lead_id}/crm-sync/retry",
    response_model=CrmSyncResponse,
    dependencies=[Depends(rate_limit_dependency("provider_actions"))],
)
def retry_crm(
    lead_id: UUID,
    session: Session = Depends(get_db),
    provider: CrmProvider = Depends(get_active_crm_provider),
    settings: Settings = Depends(get_settings),
    workspace: WorkspaceContext = Depends(require_permission("crm.sync")),
) -> CrmSyncResponse:
    lead = _lead_or_404(lead_id, session, workspace.workspace_id)
    try:
        row = manually_retry_crm_sync(
            session,
            lead,
            provider,
            actor_id=workspace.actor_id,
            max_attempts=settings.crm_max_attempts,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except CrmProviderError as error:
        raise HTTPException(status_code=502, detail=f"{error.code}: {error}") from error
    return _required_response(row)


@router.post(
    "/bulk-crm-sync",
    response_model=BulkCrmSyncResponse,
    dependencies=[Depends(rate_limit_dependency("provider_actions"))],
)
def bulk_synchronize_crm(
    request: BulkCrmSyncRequest,
    session: Session = Depends(get_db),
    provider: CrmProvider = Depends(get_active_crm_provider),
    settings: Settings = Depends(get_settings),
    workspace: WorkspaceContext = Depends(require_permission("crm.sync")),
) -> BulkCrmSyncResponse:
    if not request.confirmed:
        raise HTTPException(status_code=422, detail="Bulk CRM send requires explicit confirmation")
    results: list[BulkCrmSyncItemResponse] = []
    seen: set[UUID] = set()
    for lead_id in request.lead_ids:
        if lead_id in seen:
            continue
        seen.add(lead_id)
        lead = get_lead(session, lead_id, workspace.workspace_id)
        if lead is None:
            results.append(
                BulkCrmSyncItemResponse(lead_id=lead_id, status="failed", error="Lead not found")
            )
            continue
        try:
            row = manually_sync_lead_to_crm(
                session,
                lead,
                provider,
                actor_id=workspace.actor_id,
                payload_version=settings.load_crm_config().payload_version,
                max_attempts=settings.crm_max_attempts,
                requested_action="manual_crm_bulk_send_or_update",
            )
        except Exception as error:
            session.rollback()
            detail = (
                f"{error.code}: {error}"
                if isinstance(error, CrmProviderError)
                else f"{type(error).__name__}: CRM write failed"
            )
            results.append(
                BulkCrmSyncItemResponse(
                    lead_id=lead_id,
                    status="failed",
                    error=detail,
                )
            )
            continue
        results.append(
            BulkCrmSyncItemResponse(
                lead_id=lead_id,
                status=row.status,
                crm_sync=_required_response(row),
            )
        )
    succeeded = sum(item.status == "synced" for item in results)
    return BulkCrmSyncResponse(
        requested=len(seen),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results=results,
    )
