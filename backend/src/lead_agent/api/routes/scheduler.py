"""Private endpoints invoked only by Vercel Cron."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from lead_agent.api.dependencies import get_runtime_settings
from lead_agent.application.scheduled_sync import (
    run_due_inbound_syncs,
    run_queued_reasoning_backfills,
)
from lead_agent.settings.config import Settings

router = APIRouter(prefix="/v1/internal", tags=["scheduler"])


class ScheduledSyncResponse(BaseModel):
    claimed: int
    completed: int
    skipped: int
    failed: int
    reasoning_claimed: int
    reasoning_completed: int
    reasoning_skipped: int
    reasoning_failed: int


def _require_cron_secret(request: Request, settings: Settings) -> None:
    """Reject public traffic even if the endpoint URL becomes known."""

    if not settings.cron_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="scheduled sync is not configured",
        )
    authorization = request.headers.get("Authorization", "")
    expected = f"Bearer {settings.cron_secret}"
    if not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


@router.get("/scheduled-sync", response_model=ScheduledSyncResponse)
def scheduled_sync(
    request: Request, settings: Settings = Depends(get_runtime_settings)
) -> ScheduledSyncResponse:
    _require_cron_secret(request, settings)
    summary = run_due_inbound_syncs(limit=settings.vercel_scheduled_sync_limit)
    reasoning = run_queued_reasoning_backfills(limit=1)
    return ScheduledSyncResponse(
        **summary.__dict__,
        reasoning_claimed=reasoning.claimed,
        reasoning_completed=reasoning.completed,
        reasoning_skipped=reasoning.skipped,
        reasoning_failed=reasoning.failed,
    )
