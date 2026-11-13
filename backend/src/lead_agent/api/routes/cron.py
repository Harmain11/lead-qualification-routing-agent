from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, status

from lead_agent.api.dependencies import get_runtime_settings
from lead_agent.settings.config import Settings
from lead_agent.worker.celery_app import (
    dispatch_due_communication_reminders,
    run_due_crm_inbound_syncs_inline,
)

router = APIRouter(prefix="/v1/internal", tags=["internal"])


@router.get("/cron", include_in_schema=False)
def run_scheduled_work(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_runtime_settings),
) -> dict[str, int]:
    expected = f"Bearer {settings.cron_secret}"
    if (
        not settings.cron_secret
        or not authorization
        or not hmac.compare_digest(authorization, expected)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    return {
        "communication_reminders": dispatch_due_communication_reminders.run(),
        "crm_inbound_syncs": run_due_crm_inbound_syncs_inline(),
    }
