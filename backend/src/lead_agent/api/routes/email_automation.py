from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission
from lead_agent.application.lead_confirmation_sources import (
    LEAD_CONFIRMATION_SOURCE_KEYS,
    LEAD_CONFIRMATION_SOURCES,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import (
    WorkspaceEmailAutomationSettingORM,
    WorkspaceEmailAutomationSourceSettingORM,
)
from lead_agent.infrastructure.db.session import get_db

router = APIRouter(prefix="/v1/settings/email-automation", tags=["email automation"])


class LeadConfirmationAutomationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class LeadConfirmationSourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None


def _response(row: WorkspaceEmailAutomationSettingORM | None) -> dict[str, bool]:
    return {"enabled": bool(row and row.lead_confirmation_enabled)}


def _source_response(
    session: Session, workspace_id: UUID
) -> dict[str, object]:
    default_enabled = bool(
        (setting := session.get(WorkspaceEmailAutomationSettingORM, workspace_id))
        and setting.lead_confirmation_enabled
    )
    overrides = {
        row.source: row.lead_confirmation_enabled
        for row in session.scalars(
            select(WorkspaceEmailAutomationSourceSettingORM).where(
                WorkspaceEmailAutomationSourceSettingORM.workspace_id == workspace_id
            )
        )
    }
    return {
        "default_enabled": default_enabled,
        "sources": [
            {
                "source": source,
                "label": label,
                "enabled": overrides.get(source, default_enabled),
                "uses_default": source not in overrides,
            }
            for source, label in LEAD_CONFIRMATION_SOURCES
        ],
    }


@router.get("/lead-confirmations")
def get_lead_confirmation_automation(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
) -> dict[str, bool]:
    row = session.get(WorkspaceEmailAutomationSettingORM, workspace.workspace_id)
    return _response(row)


@router.put("/lead-confirmations")
def update_lead_confirmation_automation(
    request: LeadConfirmationAutomationUpdate,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
) -> dict[str, bool]:
    row = session.get(WorkspaceEmailAutomationSettingORM, workspace.workspace_id)
    now = datetime.now(UTC)
    if row is None:
        row = WorkspaceEmailAutomationSettingORM(
            workspace_id=workspace.workspace_id,
            lead_confirmation_enabled=request.enabled,
            updated_by=workspace.actor_id[:255],
            updated_at=now,
        )
        session.add(row)
    else:
        row.lead_confirmation_enabled = request.enabled
        row.updated_by = workspace.actor_id[:255]
        row.updated_at = now
    session.commit()
    session.refresh(row)
    return _response(row)


@router.get("/lead-confirmations/sources")
def get_lead_confirmation_sources(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
) -> dict[str, object]:
    return _source_response(session, workspace.workspace_id)


@router.put("/lead-confirmations/sources/{source}")
def update_lead_confirmation_source(
    source: str,
    request: LeadConfirmationSourceUpdate,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
) -> dict[str, object]:
    if source not in LEAD_CONFIRMATION_SOURCE_KEYS:
        raise HTTPException(status_code=404, detail="Lead source not found")
    row = session.get(
        WorkspaceEmailAutomationSourceSettingORM, (workspace.workspace_id, source)
    )
    if request.enabled is None:
        if row is not None:
            session.delete(row)
    else:
        now = datetime.now(UTC)
        if row is None:
            row = WorkspaceEmailAutomationSourceSettingORM(
                workspace_id=workspace.workspace_id,
                source=source,
                lead_confirmation_enabled=request.enabled,
                updated_by=workspace.actor_id[:255],
                updated_at=now,
            )
            session.add(row)
        else:
            row.lead_confirmation_enabled = request.enabled
            row.updated_by = workspace.actor_id[:255]
            row.updated_at = now
    session.commit()
    return _source_response(session, workspace.workspace_id)
