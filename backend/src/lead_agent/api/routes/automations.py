from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission
from lead_agent.api.operations_schemas import (
    AutomationEnrollmentBatch,
    AutomationEnrollmentItem,
    AutomationPage,
    AutomationRecord,
    RunPage,
    RunRecord,
)
from lead_agent.application.automations import advance_run, latest_reply
from lead_agent.application.governance import audit_operation, operations_policy
from lead_agent.domain.operations import AutomationInput, StrictModel
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import LeadORM
from lead_agent.infrastructure.db.operations import AutomationDefinitionORM, AutomationRunORM
from lead_agent.infrastructure.db.session import get_db

router = APIRouter(prefix="/v1/automations", tags=["sequences and playbooks"])


@router.get("", response_model=AutomationPage)
def definitions(
    kind: Literal["all", "sequence", "playbook"] = "all",
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.view")),
) -> Any:
    query = select(AutomationDefinitionORM).where(
        AutomationDefinitionORM.workspace_id == workspace.workspace_id
    )
    if kind != "all":
        query = query.where(AutomationDefinitionORM.kind == kind)
    return {
        "items": list(
            session.scalars(query.order_by(AutomationDefinitionORM.updated_at.desc()).limit(100))
        )
    }


@router.put("/{definition_id}", response_model=AutomationRecord)
def save_definition(
    definition_id: UUID,
    request: AutomationInput,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("workspace.manage")),
) -> Any:
    row = session.get(AutomationDefinitionORM, definition_id)
    if row and row.workspace_id != workspace.workspace_id:
        raise HTTPException(404, "Playbook not found")
    if (row.version if row else 0) != request.version:
        raise HTTPException(409, "Definition changed elsewhere. Reload before saving.")
    payload = request.model_dump(mode="json")
    if row:
        changed = cast(
            CursorResult[Any],
            session.execute(
                update(AutomationDefinitionORM)
                .where(
                    AutomationDefinitionORM.id == definition_id,
                    AutomationDefinitionORM.version == request.version,
                )
                .values(
                    name=request.name,
                    kind=request.kind,
                    active=request.active,
                    specification=payload,
                    version=request.version + 1,
                    updated_at=datetime.now(UTC),
                )
            ),
        )
        if not changed.rowcount:
            raise HTTPException(409, "Definition changed elsewhere")
    else:
        row = AutomationDefinitionORM(
            id=definition_id,
            workspace_id=workspace.workspace_id,
            name=request.name,
            kind=request.kind,
            active=request.active,
            specification=payload,
            created_by=workspace.actor_id,
        )
        session.add(row)
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "automation.saved",
        {"definition_id": str(definition_id), "version": request.version + 1},
    )
    session.commit()
    session.refresh(row)
    return row


class Enrollment(StrictModel):
    lead_id: UUID


class BulkEnrollment(StrictModel):
    lead_ids: list[UUID] = Field(min_length=1, max_length=100)

    @field_validator("lead_ids")
    @classmethod
    def unique_lead_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


def _enroll_one(
    session: Session,
    definition: AutomationDefinitionORM,
    lead: LeadORM,
    workspace: WorkspaceContext,
) -> tuple[AutomationRunORM, bool]:
    existing = session.scalar(
        select(AutomationRunORM).where(
            AutomationRunORM.definition_id == definition.id,
            AutomationRunORM.lead_id == lead.id,
        )
    )
    if existing:
        return existing, False
    start = next(
        node["id"]
        for node in definition.specification["nodes"]
        if node["type"] == "start"
    )
    row = AutomationRunORM(
        workspace_id=workspace.workspace_id,
        definition_id=definition.id,
        lead_id=lead.id,
        specification=definition.specification,
        cursor=start,
        created_by=workspace.actor_id,
        state={
            "enrolled_at": datetime.now(UTC).isoformat(),
            "definition_version": definition.version,
        },
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        existing = session.scalar(
            select(AutomationRunORM).where(
                AutomationRunORM.definition_id == definition.id,
                AutomationRunORM.lead_id == lead.id,
            )
        )
        if existing is None:
            raise
        return existing, False
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "automation.enrolled",
        {"run_id": str(row.id), "lead_id": str(lead.id)},
    )
    return row, True


@router.post("/{definition_id}/enroll", status_code=201, response_model=RunRecord)
def enroll(
    definition_id: UUID,
    request: Enrollment,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> Any:
    definition = session.get(AutomationDefinitionORM, definition_id)
    lead = session.get(LeadORM, request.lead_id)
    if definition is None or definition.workspace_id != workspace.workspace_id:
        raise HTTPException(404, "Playbook not found")
    if lead is None or lead.workspace_id != workspace.workspace_id:
        raise HTTPException(404, "Lead not found")
    if not definition.active:
        raise HTTPException(409, "Activate the definition before enrollment")
    row, _ = _enroll_one(session, definition, lead, workspace)
    session.commit()
    session.refresh(row)
    return row


@router.post(
    "/{definition_id}/enroll-bulk",
    response_model=AutomationEnrollmentBatch,
)
def enroll_bulk(
    definition_id: UUID,
    request: BulkEnrollment,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> AutomationEnrollmentBatch:
    definition = session.get(AutomationDefinitionORM, definition_id)
    if definition is None or definition.workspace_id != workspace.workspace_id:
        raise HTTPException(404, "Playbook not found")
    if not definition.active:
        raise HTTPException(409, "Activate the definition before enrollment")

    leads = {
        lead.id: lead
        for lead in session.scalars(
            select(LeadORM).where(
                LeadORM.workspace_id == workspace.workspace_id,
                LeadORM.id.in_(request.lead_ids),
            )
        )
    }
    if len(leads) != len(request.lead_ids):
        raise HTTPException(404, "One or more leads were not found")

    items: list[AutomationEnrollmentItem] = []
    enrolled = 0
    already_enrolled = 0
    for lead_id in request.lead_ids:
        run, created = _enroll_one(session, definition, leads[lead_id], workspace)
        enrolled += int(created)
        already_enrolled += int(not created)
        items.append(
            AutomationEnrollmentItem(
                lead_id=lead_id,
                run_id=run.id,
                status="enrolled" if created else "already_enrolled",
            )
        )
    session.commit()
    return AutomationEnrollmentBatch(
        definition_id=definition_id,
        enrolled=enrolled,
        already_enrolled=already_enrolled,
        items=items,
    )


@router.get("/runs/list", response_model=RunPage)
def runs(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.view")),
) -> Any:
    return {
        "items": list(
            session.scalars(
                select(AutomationRunORM)
                .where(AutomationRunORM.workspace_id == workspace.workspace_id)
                .order_by(AutomationRunORM.updated_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
    }


class RunAction(StrictModel):
    action: Literal["approve", "advance", "pause", "resume", "cancel"]


@router.post("/runs/{run_id}", response_model=RunRecord)
def run_action(
    run_id: UUID,
    request: RunAction,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> Any:
    row = session.scalar(
        select(AutomationRunORM)
        .where(
            AutomationRunORM.id == run_id, AutomationRunORM.workspace_id == workspace.workspace_id
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "Run not found")
    previous_status = row.status
    if row.status == "processing":
        raise HTTPException(
            409, "This step is running; wait for it to finish before changing its state"
        )
    if row.status in {"completed", "cancelled"}:
        raise HTTPException(409, "This run is finished")
    if request.action == "approve":
        if row.status != "awaiting_approval":
            raise HTTPException(409, "No step awaits approval")
        node = next(node for node in row.specification["nodes"] if node["id"] == row.cursor)
        tier = operations_policy(session, workspace.workspace_id).approval_tiers.get(
            node["type"], "approve"
        )
        if tier == "full_review" and (
            "manual_review.manage" not in workspace.permissions
            or row.created_by == workspace.actor_id
        ):
            raise HTTPException(
                403, "Full review requires another member with review-management permission"
            )
        row.state = {**row.state, "approved_node": row.cursor}
        row.approved_by, row.status = workspace.actor_id, "approved"
    elif request.action in {"pause", "cancel"}:
        row.status = "paused" if request.action == "pause" else "cancelled"
    elif request.action == "resume":
        if row.status not in {"paused", "blocked"}:
            raise HTTPException(409, "Only paused or blocked runs can resume")
        lead = session.get(LeadORM, row.lead_id)
        if lead is None:
            raise HTTPException(404, "Lead not found")
        reply = latest_reply(session, lead, datetime.fromisoformat(row.state["enrolled_at"]))
        row.state = {**row.state, "acknowledged_reply": str(reply.id) if reply else None}
        row.status, row.due_at = "queued", datetime.now(UTC)
    values = {
        "state": row.state,
        "status": row.status,
        "due_at": row.due_at,
        "approved_by": row.approved_by,
        "updated_at": datetime.now(UTC),
    }
    # Avoid autoflush overriding a worker's state change before the compare-and-swap.
    session.expire(row)
    changed = cast(
        CursorResult[Any],
        session.execute(
            update(AutomationRunORM)
            .where(AutomationRunORM.id == run_id, AutomationRunORM.status == previous_status)
            .values(**values)
        ),
    )
    if not changed.rowcount:
        session.rollback()
        raise HTTPException(409, "This run changed while you were reviewing it")
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "automation." + request.action,
        {"run_id": str(row.id)},
    )
    session.commit()
    return (
        advance_run(session, row, workspace.actor_id)
        if request.action in {"advance", "approve", "resume"}
        else row
    )


@router.get("/trust/summary")
def trust(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.view")),
) -> Any:
    reviewed = list(
        session.scalars(
            select(AutomationRunORM).where(
                AutomationRunORM.workspace_id == workspace.workspace_id,
                AutomationRunORM.status.in_(("completed", "blocked", "cancelled")),
            )
        )
    )
    completed = sum(row.status == "completed" for row in reviewed)
    observed = [row for row in reviewed if row.status != "cancelled"]
    return {
        "completed": completed,
        "blocked": sum(row.status == "blocked" for row in observed),
        "sample_size": len(observed),
        "score": round(100 * completed / len(observed), 1) if len(observed) >= 5 else None,
        "method": (
            "Completed / (completed + blocked) runs. Withheld below 5 "
            "runs. This operational score never grants permissions or auto-send authority."
        ),
    }
