from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import WorkspaceEmailAutomationSettingORM
from lead_agent.infrastructure.db.session import get_db

router = APIRouter(prefix="/v1/settings/email-automation", tags=["email automation"])


class LeadConfirmationAutomationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


def _response(row: WorkspaceEmailAutomationSettingORM | None) -> dict[str, bool]:
    return {"enabled": bool(row and row.lead_confirmation_enabled)}


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
