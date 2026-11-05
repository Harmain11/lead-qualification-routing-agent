from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from pydantic import Field, field_validator
from sqlalchemy import Select, func, inspect, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_authenticated_identity, require_permission
from lead_agent.api.operations_schemas import (
    CompanyRecord,
    ConsentPage,
    ConsentRecord,
    ContactRecord,
)
from lead_agent.api.schemas import lead_response
from lead_agent.application.coaching_privacy import PRIVATE_INSIGHT_KINDS, insight_visibility
from lead_agent.application.governance import audit_operation, hosting_region, operations_policy
from lead_agent.application.intelligence import suggest_workspace_business_profile
from lead_agent.domain.auth import AuthenticatedIdentity
from lead_agent.domain.operations import (
    ConsentInput,
    OperationsPolicy,
    StrictModel,
    safe_website,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.calling import CallRecordORM
from lead_agent.infrastructure.db.models import (
    AgentToolAuditORM,
    LeadFollowUpEmailORM,
    LeadORM,
    LeadOutcomeORM,
    PipelineEventORM,
    RoutingDecisionORM,
    UserORM,
    WorkspaceMembershipORM,
    WorkspaceORM,
)
from lead_agent.infrastructure.db.operations import (
    AgentInsightORM,
    AutomationRunORM,
    CompanyORM,
    ConsentEventORM,
    ContactORM,
    LeadContactORM,
    LeadNoteORM,
    OperationsPolicyORM,
)
from lead_agent.infrastructure.db.session import get_db

router = APIRouter(prefix="/v1/ops", tags=["workspace governance"])


class PolicyUpdate(StrictModel):
    configuration: OperationsPolicy
    version: int = Field(ge=0)


class WorkspaceBusinessProfileWebsiteRequest(StrictModel):
    website: str = Field(min_length=1, max_length=500)

    @field_validator("website")
    @classmethod
    def website_is_public_https(cls, value: str) -> str:
        return safe_website(value)


@router.get("/policy")
def get_policy(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("routing.view")),
) -> Any:
    row = session.get(OperationsPolicyORM, workspace.workspace_id)
    members = session.execute(
        select(WorkspaceMembershipORM.user_id, UserORM.display_name)
        .join(UserORM, UserORM.id == WorkspaceMembershipORM.user_id)
        .where(
            WorkspaceMembershipORM.workspace_id == workspace.workspace_id,
            WorkspaceMembershipORM.status == "active",
            UserORM.status == "active",
        )
    ).all()
    counts: dict[str | None, int] = {
        rep: count
        for rep, count in session.execute(
            select(RoutingDecisionORM.assigned_rep_id, func.count())
            .outerjoin(LeadOutcomeORM, LeadOutcomeORM.lead_id == RoutingDecisionORM.lead_id)
            .where(
                RoutingDecisionORM.workspace_id == workspace.workspace_id,
                RoutingDecisionORM.is_current.is_(True),
                (LeadOutcomeORM.id.is_(None))
                | LeadOutcomeORM.pipeline_stage.not_in(("closed_won", "closed_lost")),
            )
            .group_by(RoutingDecisionORM.assigned_rep_id)
        ).all()
    }
    return {
        "configuration": operations_policy(session, workspace.workspace_id),
        "version": row.version if row else 0,
        "hosting_region": hosting_region(),
        "members": [
            {"user_id": user_id, "name": name, "open_leads": counts.get(str(user_id), 0)}
            for user_id, name in members
        ],
    }


@router.post("/business-profile/analyze")
def analyze_business_profile_website(
    request: WorkspaceBusinessProfileWebsiteRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("workspace.manage")),
) -> Any:
    try:
        return suggest_workspace_business_profile(
            session, workspace.workspace_id, request.website
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except Exception:
        raise HTTPException(
            502,
            "Website analysis is unavailable. Check the public page and workspace AI connection.",
        ) from None


@router.put("/policy")
def save_policy(
    request: PolicyUpdate,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("workspace.manage")),
) -> Any:
    config = request.configuration
    members = set(
        session.scalars(
            select(WorkspaceMembershipORM.user_id)
            .join(UserORM, UserORM.id == WorkspaceMembershipORM.user_id)
            .where(
                WorkspaceMembershipORM.workspace_id == workspace.workspace_id,
                WorkspaceMembershipORM.status == "active",
                UserORM.status == "active",
            )
        )
    )
    selected = {value.user_id for value in config.capacity} | {
        user_id for group in config.territories for user_id in group.member_ids
    }
    if not selected.issubset(members):
        raise HTTPException(422, "Policies can use only active members of this workspace")
    if config.required_region not in {"any", hosting_region()}:
        raise HTTPException(
            422,
            (
                "The current hosting region cannot satisfy that residency "
                "requirement. Configure hosting before saving."
            ),
        )
    row = session.get(OperationsPolicyORM, workspace.workspace_id)
    if (row.version if row else 0) != request.version:
        raise HTTPException(409, "Policy changed elsewhere. Reload before saving.")
    payload = config.model_dump(mode="json")
    if row:
        changed = cast(
            CursorResult[Any],
            session.execute(
                update(OperationsPolicyORM)
                .where(
                    OperationsPolicyORM.workspace_id == workspace.workspace_id,
                    OperationsPolicyORM.version == request.version,
                )
                .values(
                    configuration=payload,
                    version=request.version + 1,
                    updated_by=workspace.actor_id,
                    updated_at=datetime.now(UTC),
                )
            ),
        )
        if not changed.rowcount:
            raise HTTPException(409, "Policy changed elsewhere. Reload before saving.")
    else:
        row = OperationsPolicyORM(
            workspace_id=workspace.workspace_id,
            configuration=payload,
            updated_by=workspace.actor_id,
            version=1,
        )
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
        except IntegrityError:
            raise HTTPException(409, "Policy changed elsewhere. Reload before saving.") from None
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "operations.policy.saved",
        {"version": request.version + 1},
    )
    session.commit()
    return {"configuration": config, "version": request.version + 1}


class Availability(StrictModel):
    available: bool
    minutes: int = Field(default=15, ge=1, le=480)


@router.post("/availability")
def availability(
    request: Availability,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> Any:
    if workspace.user_id is None:
        raise HTTPException(401, "Sign in to update your availability")
    row = session.scalar(
        select(OperationsPolicyORM)
        .where(OperationsPolicyORM.workspace_id == workspace.workspace_id)
        .with_for_update()
    )
    config = operations_policy(session, workspace.workspace_id)
    from lead_agent.domain.operations import RepCapacity

    existing = next(
        (value for value in config.capacity if value.user_id == workspace.user_id), None
    )
    replacement = RepCapacity(
        user_id=workspace.user_id,
        max_open_leads=existing.max_open_leads if existing else 30,
        available=request.available,
        available_until=datetime.now(UTC) + timedelta(minutes=request.minutes),
    )
    config.capacity = [value for value in config.capacity if value.user_id != workspace.user_id] + [
        replacement
    ]
    payload = config.model_dump(mode="json")
    if row is None:
        try:
            with session.begin_nested():
                session.add(
                    OperationsPolicyORM(
                        workspace_id=workspace.workspace_id,
                        updated_by=workspace.actor_id,
                        configuration=payload,
                        version=1,
                    )
                )
                session.flush()
        except IntegrityError:
            raise HTTPException(
                409, "Availability changed elsewhere; retry your check-in"
            ) from None
    else:
        changed = cast(
            CursorResult[Any],
            session.execute(
                update(OperationsPolicyORM)
                .where(
                    OperationsPolicyORM.workspace_id == workspace.workspace_id,
                    OperationsPolicyORM.version == row.version,
                )
                .values(
                    configuration=payload,
                    version=row.version + 1,
                    updated_by=workspace.actor_id,
                    updated_at=datetime.now(UTC),
                )
            ),
        )
        if not changed.rowcount:
            raise HTTPException(409, "Availability changed elsewhere; retry your check-in")
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "rep.availability.changed",
        replacement.model_dump(mode="json"),
    )
    session.commit()
    return replacement


@router.get("/branding")
def branding(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> Any:
    config = operations_policy(session, workspace.workspace_id)
    return {
        "name": config.brand_name,
        "logo_url": config.brand_logo_url,
        "accent": config.brand_accent,
        "custom_domain": config.custom_domain,
    }


@router.get("/consent", response_model=ConsentPage)
def consent(
    search: str = Query("", max_length=200),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("workspace.manage")),
) -> Any:
    conditions = [ConsentEventORM.workspace_id == workspace.workspace_id]
    if search:
        conditions.append(ConsentEventORM.email.ilike(f"%{search}%"))
    rows = list(
        session.scalars(
            select(ConsentEventORM)
            .where(*conditions)
            .order_by(ConsentEventORM.created_at.desc(), ConsentEventORM.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return {
        "items": rows,
        "total": session.scalar(
            select(func.count()).select_from(ConsentEventORM).where(*conditions)
        ),
    }


@router.post("/consent", status_code=201, response_model=ConsentRecord)
def record_consent(
    request: ConsentInput,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("workspace.manage")),
) -> Any:
    row = ConsentEventORM(
        workspace_id=workspace.workspace_id, actor_id=workspace.actor_id, **request.model_dump()
    )
    session.add(row)
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "consent.recorded",
        {"email": request.email, "channel": request.channel, "status": request.status},
    )
    session.commit()
    session.refresh(row)
    return row


def orm_columns(row: Any) -> dict[str, Any]:
    return {column.key: getattr(row, column.key) for column in inspect(type(row)).columns}


@router.get("/leads/{lead_id}/audit-export")
def audit_export(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.export")),
) -> Any:
    lead = session.scalar(
        select(LeadORM).where(LeadORM.id == lead_id, LeadORM.workspace_id == workspace.workspace_id)
    )
    if lead is None:
        raise HTTPException(404, "Lead not found")
    linked = session.get(LeadContactORM, lead_id)
    contact = session.get(ContactORM, linked.contact_id) if linked else None
    if contact and contact.workspace_id != workspace.workspace_id:
        contact = None
    company = (
        session.get(CompanyORM, contact.company_id) if contact and contact.company_id else None
    )
    if company and company.workspace_id != workspace.workspace_id:
        company = None
    queries: dict[str, Select[Any]] = {
        "events": select(PipelineEventORM).where(
            PipelineEventORM.lead_id == lead_id,
            PipelineEventORM.stage.not_in(PRIVATE_INSIGHT_KINDS),
        ),
        "follow_ups": select(LeadFollowUpEmailORM).where(LeadFollowUpEmailORM.lead_id == lead_id),
        "consent": select(ConsentEventORM).where(ConsentEventORM.email == lead.email.lower()),
        "notes": select(LeadNoteORM).where(LeadNoteORM.lead_id == lead_id),
        "calls": select(CallRecordORM).where(CallRecordORM.lead_id == lead_id),
        "insights": select(AgentInsightORM).where(
            AgentInsightORM.lead_id == lead_id, insight_visibility(session, workspace)
        ),
        "automations": select(AutomationRunORM).where(AutomationRunORM.lead_id == lead_id),
    }
    payload: dict[str, Any] = {
        "exported_at": datetime.now(UTC),
        "lead": lead_response(lead),
        "contact": ContactRecord.model_validate(contact) if contact else None,
        "company": CompanyRecord.model_validate(company) if company else None,
    }
    for key, query in queries.items():
        model = query.column_descriptions[0]["entity"]
        payload[key] = [
            orm_columns(row)
            for row in session.scalars(query.where(model.workspace_id == workspace.workspace_id))
        ]
    payload["routing_history"] = [orm_columns(row) for row in lead.routing_decisions]
    payload["crm_history"] = [orm_columns(row) for row in lead.crm_syncs]
    payload["notifications"] = [
        orm_columns(notice) for route in lead.routing_decisions for notice in route.notifications
    ]
    payload["agent_tool_audit"] = [
        orm_columns(row)
        for row in session.scalars(
            select(AgentToolAuditORM).where(
                AgentToolAuditORM.workspace_id == workspace.workspace_id,
                AgentToolAuditORM.input_summary["lead_id"].as_string() == str(lead_id),
            )
        )
    ]
    content = json.dumps(jsonable_encoder(payload), ensure_ascii=False, indent=2)
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "lead.compliance_exported",
        {"lead_id": str(lead_id)},
    )
    session.commit()
    return Response(
        content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="corral-audit-{lead_id}.json"',
            "Cache-Control": "no-store",
        },
    )


def retention_candidates(session: Session, workspace_id: UUID) -> list[UUID]:
    days = operations_policy(session, workspace_id).retention_days
    if days is None:
        return []
    active = select(AutomationRunORM.lead_id).where(
        AutomationRunORM.workspace_id == workspace_id,
        AutomationRunORM.status.not_in(("completed", "cancelled")),
    )
    return list(
        session.scalars(
            select(LeadORM.id)
            .where(
                LeadORM.workspace_id == workspace_id,
                LeadORM.updated_at < datetime.now(UTC) - timedelta(days=days),
                LeadORM.id.not_in(active),
            )
            .order_by(LeadORM.id)
            .limit(500)
        )
    )


def retention_proof(ids: list[UUID]) -> str:
    return hashlib.sha256("\n".join(map(str, ids)).encode()).hexdigest()


@router.get("/retention")
def retention(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("workspace.manage")),
) -> Any:
    ids = retention_candidates(session, workspace.workspace_id)
    return {
        "lead_ids": ids,
        "count": len(ids),
        "proof": retention_proof(ids),
        "scope": (
            "Inactive leads and dependent events only. Independent "
            "contacts and consent records are retained."
        ),
        "more": len(ids) == 500,
    }


class RetentionApproval(StrictModel):
    proof: str = Field(min_length=64, max_length=64)
    confirm_permanent_deletion: bool


@router.post("/retention/apply")
def apply_retention(
    request: RetentionApproval,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("workspace.manage")),
) -> Any:
    ids = retention_candidates(session, workspace.workspace_id)
    if not request.confirm_permanent_deletion or retention_proof(ids) != request.proof:
        raise HTTPException(409, "Review the current retention plan before confirming deletion")
    locked = list(
        session.scalars(
            select(LeadORM)
            .where(LeadORM.workspace_id == workspace.workspace_id, LeadORM.id.in_(ids))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if retention_proof(retention_candidates(session, workspace.workspace_id)) != request.proof:
        raise HTTPException(409, "The retention plan changed while acquiring its locks")
    from lead_agent.api.routes.leads import _delete_lead

    for lead in locked:
        _delete_lead(session, lead, actor_id=workspace.actor_id)
        if lead.source_event is not None:
            lead.source_event.raw_payload = {}
            lead.source_event.error_message = "Lead payload removed by reviewed retention policy"
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "retention.applied",
        {"deleted_lead_ids": list(map(str, ids))},
    )
    session.commit()
    return {"deleted": len(ids)}


@router.get("/agency")
def agency(
    identity: AuthenticatedIdentity = Depends(get_authenticated_identity),
    session: Session = Depends(get_db),
) -> Any:
    memberships = list(
        session.scalars(
            select(WorkspaceMembershipORM).where(
                WorkspaceMembershipORM.user_id == identity.user_id,
                WorkspaceMembershipORM.status == "active",
            )
        )
    )
    from lead_agent.application.auth import membership_permissions

    items = []
    for membership in memberships:
        if "analytics.view" not in membership_permissions(membership):
            continue
        workspace = session.get(WorkspaceORM, membership.workspace_id)
        if workspace is None or workspace.status != "active":
            continue
        from lead_agent.application.governance import OutreachBlocked, assert_residency

        try:
            assert_residency(session, workspace.id)
        except OutreachBlocked:
            continue
        count = session.scalar(
            select(func.count()).select_from(LeadORM).where(LeadORM.workspace_id == workspace.id)
        )
        won = session.scalar(
            select(func.count())
            .select_from(LeadOutcomeORM)
            .where(
                LeadOutcomeORM.workspace_id == workspace.id,
                LeadOutcomeORM.pipeline_stage == "closed_won",
            )
        )
        items.append(
            {
                "workspace_id": workspace.id,
                "name": workspace.name,
                "role": membership.role,
                "leads": count,
                "wins": won,
                "current": workspace.id == identity.current_workspace_id,
            }
        )
    return {"items": items}
