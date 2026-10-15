from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_policies, require_permission
from lead_agent.api.operations_schemas import InsightPage, InsightRecord
from lead_agent.api.routes.contact_records import owned_company
from lead_agent.application.advanced_analytics import advanced_analytics
from lead_agent.application.governance import audit_operation
from lead_agent.application.intelligence import (
    approve_learning,
    coach_lead,
    prepare_digest,
    propose_learning,
    research_company,
)
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.operations import StrictModel
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import (
    LeadORM,
    WorkspaceMembershipORM,
)
from lead_agent.infrastructure.db.operations import AgentInsightORM
from lead_agent.infrastructure.db.session import get_db

router = APIRouter(prefix="/v1/intelligence", tags=["research coaching and learning"])


@router.get("/insights", response_model=InsightPage)
def insights(
    kind: Literal["all", "research", "coach", "learning", "digest", "watcher"] = "all",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.view")),
) -> Any:
    conditions = [AgentInsightORM.workspace_id == workspace.workspace_id]
    if kind != "all":
        conditions.append(AgentInsightORM.kind == kind)
    rows = list(
        session.scalars(
            select(AgentInsightORM)
            .where(*conditions)
            .order_by(AgentInsightORM.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    if "manual_review.manage" not in workspace.permissions:
        rows = [
            row
            for row in rows
            if row.kind != "digest"
            or (
                row.status == "approved"
                and row.content.get("recipient_id") == str(workspace.user_id)
            )
        ]
    return {"items": rows}


@router.post("/companies/{company_id}/research", response_model=InsightRecord)
def research(
    company_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.execute")),
) -> Any:
    try:
        return research_company(
            session, owned_company(session, workspace.workspace_id, company_id), workspace.actor_id
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except Exception:
        session.rollback()
        raise HTTPException(
            502, "Company research is unavailable; check the reviewed page and OpenAI connection"
        ) from None


@router.post("/leads/{lead_id}/coach", response_model=InsightRecord)
def coach(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.execute")),
) -> Any:
    lead = session.scalar(
        select(LeadORM).where(LeadORM.id == lead_id, LeadORM.workspace_id == workspace.workspace_id)
    )
    if lead is None:
        raise HTTPException(404, "Lead not found")
    return coach_lead(session, lead, workspace.actor_id)


@router.post("/learning", response_model=InsightRecord)
def learning(
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    workspace: WorkspaceContext = Depends(require_permission("icp.manage")),
) -> Any:
    return propose_learning(session, workspace.workspace_id, workspace.actor_id, policies[0])


class InsightReview(StrictModel):
    approve: bool


@router.post("/insights/{insight_id}/review", response_model=InsightRecord)
def review(
    insight_id: UUID,
    request: InsightReview,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    workspace: WorkspaceContext = Depends(require_permission("manual_review.manage")),
) -> Any:
    row = session.scalar(
        select(AgentInsightORM)
        .where(
            AgentInsightORM.id == insight_id, AgentInsightORM.workspace_id == workspace.workspace_id
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "Insight not found")
    if row.status != "pending_review":
        raise HTTPException(409, "This insight has already been reviewed")
    if request.approve and row.kind == "learning":
        if "icp.manage" not in workspace.permissions:
            raise HTTPException(403, "ICP management permission is required")
        try:
            return approve_learning(session, row, workspace, policies[0])
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
    if request.approve and row.kind == "research":
        if row.company_id is None:
            raise HTTPException(409, "This research has no company record")
        company = owned_company(session, workspace.workspace_id, row.company_id)
        source = row.content.get("sources", [{}])[0].get("url")
        if not company.website_reviewed or company.website != source:
            raise HTTPException(
                409, "Company website changed; research the reviewed website again before approval"
            )
        company.profile = {
            **company.profile,
            "research": row.content,
            "approved_insight_id": str(row.id),
        }
        for fact in row.content.get("facts", []):
            if fact["field"] == "industry":
                company.industry = fact["value"][:255]
        company.updated_at = datetime.now(UTC)
    changed = cast(
        CursorResult[Any],
        session.execute(
            update(AgentInsightORM)
            .where(AgentInsightORM.id == row.id, AgentInsightORM.status == "pending_review")
            .execution_options(synchronize_session=False)
            .values(status="approved" if request.approve else "dismissed")
        ),
    )
    if not changed.rowcount:
        session.rollback()
        raise HTTPException(409, "This insight changed while you were reviewing it")
    row.status = "approved" if request.approve else "dismissed"
    row.reviewed_by, row.reviewed_at = workspace.actor_id, datetime.now(UTC)
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "insight.reviewed",
        {"insight_id": str(row.id), "kind": row.kind, "status": row.status},
    )
    session.commit()
    session.refresh(row)
    return row


@router.get("/objections")
def objection_library(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.view")),
) -> Any:
    rows = list(
        session.scalars(
            select(AgentInsightORM)
            .where(
                AgentInsightORM.workspace_id == workspace.workspace_id,
                AgentInsightORM.kind == "coach",
            )
            .order_by(AgentInsightORM.created_at.desc())
            .limit(500)
        )
    )
    items, seen = [], set()
    for row in rows:
        for objection in row.content.get("objections", []):
            key = (objection["source"], objection["tag"])
            if key not in seen:
                lead = session.get(LeadORM, row.lead_id)
                if lead is None:
                    continue
                evidence = row.content.get("evidence", [])
                index = next(
                    (i for i, item in enumerate(evidence) if item["source"] == objection["source"]),
                    -1,
                )
                response = (
                    next(
                        (
                            item
                            for item in evidence[index + 1 :]
                            if item.get("direction") in {"outbound", "sent"}
                        ),
                        None,
                    )
                    if index >= 0
                    else None
                )
                items.append(
                    {
                        **objection,
                        "lead_id": row.lead_id,
                        "outcome": lead.outcome.pipeline_stage if lead.outcome else "open",
                        "recorded_response": response,
                    }
                )
                seen.add(key)
    return {"items": items, "truncated": len(rows) == 500}


class DigestInput(StrictModel):
    user_id: UUID


@router.post("/digests", response_model=InsightRecord)
def digest(
    request: DigestInput,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("manual_review.manage")),
) -> Any:
    if not session.scalar(
        select(WorkspaceMembershipORM.id).where(
            WorkspaceMembershipORM.workspace_id == workspace.workspace_id,
            WorkspaceMembershipORM.user_id == request.user_id,
            WorkspaceMembershipORM.status == "active",
        )
    ):
        raise HTTPException(422, "Choose an active member of this workspace")
    return prepare_digest(session, workspace.workspace_id, request.user_id, workspace.actor_id)


@router.get("/analytics")
def analytics(
    months: int = Query(6, ge=1, le=24),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("analytics.view")),
) -> Any:
    return advanced_analytics(session, workspace.workspace_id, months)


@router.post("/analytics/narrate")
def narrate(
    months: int = Query(6, ge=1, le=24),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("analytics.view")),
) -> Any:
    try:
        return advanced_analytics(session, workspace.workspace_id, months, narrate=True)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except Exception:
        session.rollback()
        raise HTTPException(
            502, "OpenAI narration is unavailable; recorded metrics remain available"
        ) from None
