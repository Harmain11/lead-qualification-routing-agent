from __future__ import annotations

from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission
from lead_agent.application.analytics import AnalyticsService
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.session import get_db

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])
RangeParam = Literal["weekly", "monthly", "annual"]


def _run(service: AnalyticsService, method: str, range_key: RangeParam) -> dict[str, Any]:
    try:
        handler = getattr(service, method)
        return cast(dict[str, Any], handler(range_key))
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/dashboard")
def dashboard(
    range: RangeParam = "monthly",
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("analytics.view")),
) -> dict[str, Any]:
    return _run(
        AnalyticsService(session, workspace.workspace_id, workspace=workspace), "dashboard", range
    )


@router.get("/channels")
def channels(
    range: RangeParam = "monthly",
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("analytics.view")),
) -> dict[str, Any]:
    return _run(
        AnalyticsService(session, workspace.workspace_id, workspace=workspace), "channels", range
    )


@router.get("/pipeline")
def pipeline(
    range: RangeParam = "monthly",
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("analytics.view")),
) -> dict[str, Any]:
    return _run(
        AnalyticsService(session, workspace.workspace_id, workspace=workspace), "pipeline", range
    )


@router.get("/revenue")
def revenue(
    range: RangeParam = "monthly",
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("analytics.view")),
) -> dict[str, Any]:
    return _run(
        AnalyticsService(session, workspace.workspace_id, workspace=workspace), "revenue", range
    )


@router.get("/reps")
def reps(
    range: RangeParam = "monthly",
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("analytics.view")),
) -> dict[str, Any]:
    return _run(
        AnalyticsService(session, workspace.workspace_id, workspace=workspace), "reps", range
    )


@router.get("/time-series")
def time_series(
    range: RangeParam = "monthly",
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("analytics.view")),
) -> dict[str, Any]:
    return _run(
        AnalyticsService(session, workspace.workspace_id, workspace=workspace), "time_series", range
    )


@router.get("/agent-activity")
def agent_activity(
    range: RangeParam = "monthly",
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.view")),
) -> dict[str, Any]:
    return _run(
        AnalyticsService(session, workspace.workspace_id, workspace=workspace),
        "agent_activity",
        range,
    )


@router.get("/manual-review")
def manual_review(
    range: RangeParam = "monthly",
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("manual_review.view")),
) -> dict[str, Any]:
    return _run(
        AnalyticsService(session, workspace.workspace_id, workspace=workspace),
        "manual_review",
        range,
    )


@router.get("/extended")
def extended(
    range: RangeParam = "monthly",
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("analytics.view")),
) -> dict[str, Any]:
    return _run(
        AnalyticsService(session, workspace.workspace_id, workspace=workspace), "extended", range
    )
