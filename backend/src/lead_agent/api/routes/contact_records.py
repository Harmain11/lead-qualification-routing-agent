from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission
from lead_agent.api.operations_schemas import (
    CompanyDetail,
    CompanyPage,
    CompanyRecord,
    ContactDetail,
    ContactPage,
    ContactRecord,
)
from lead_agent.application.contacts import attach_contact, company_identity, duplicate_suggestions
from lead_agent.application.governance import audit_operation
from lead_agent.domain.operations import CompanyInput, ContactInput, merge_record_profile
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import LeadORM
from lead_agent.infrastructure.db.operations import CompanyORM, ContactORM, LeadContactORM
from lead_agent.infrastructure.db.session import get_db

router = APIRouter(prefix="/v1/records", tags=["contacts and accounts"])


def _require_field_permission(workspace: WorkspaceContext, permission: str) -> None:
    if permission not in workspace.permissions:
        raise HTTPException(403, f"permission required: {permission}")


def _accessible_contact_ids(workspace: WorkspaceContext):
    return (
        select(LeadContactORM.contact_id)
        .join(LeadORM, LeadORM.id == LeadContactORM.lead_id)
        .where(LeadORM.workspace_id == workspace.workspace_id)
    )


def _restrict_record_scope(conditions: list[Any], workspace: WorkspaceContext, column: Any) -> None:
    if workspace.role not in {"owner", "admin"}:
        conditions.append(column.in_(_accessible_contact_ids(workspace)))


def owned_company(
    session: Session, workspace: WorkspaceContext, company_id: UUID
) -> CompanyORM:
    row = session.get(CompanyORM, company_id)
    if row is None or row.workspace_id != workspace.workspace_id:
        raise HTTPException(404, "Company not found")
    if workspace.role not in {"owner", "admin"}:
        visible = session.scalar(
            select(func.count())
            .select_from(ContactORM)
            .where(
                ContactORM.company_id == row.id,
                ContactORM.id.in_(_accessible_contact_ids(workspace)),
            )
        )
        if not visible:
            raise HTTPException(404, "Company not found")
    return row


def owned_contact(
    session: Session, workspace: WorkspaceContext, contact_id: Any
) -> ContactORM:
    row = session.get(ContactORM, contact_id)
    if row is None or row.workspace_id != workspace.workspace_id or row.merged_into_id:
        raise HTTPException(404, "Contact not found")
    if workspace.role not in {"owner", "admin"}:
        visible = session.scalar(
            select(func.count())
            .select_from(LeadContactORM)
            .join(LeadORM, LeadORM.id == LeadContactORM.lead_id)
            .where(
                LeadContactORM.contact_id == row.id,
                LeadORM.workspace_id == workspace.workspace_id,
            )
        )
        if not visible:
            raise HTTPException(404, "Contact not found")
    return row


@router.post("/backfill")
def backfill(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> Any:
    missing = list(
        session.scalars(
            select(LeadORM)
            .outerjoin(LeadContactORM, LeadContactORM.lead_id == LeadORM.id)
            .where(LeadORM.workspace_id == workspace.workspace_id, LeadContactORM.lead_id.is_(None))
            .limit(500)
        )
    )
    for lead in missing:
        attach_contact(session, lead)
    session.commit()
    return {"linked": len(missing), "more": len(missing) == 500}


@router.get("/contacts", response_model=ContactPage)
def list_contacts(
    search: str = Query("", max_length=200),
    company_id: UUID | None = None,
    sort: Literal["name", "recent", "company"] = "name",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> Any:
    _require_field_permission(workspace, "fields.contact.view")
    conditions = [
        ContactORM.workspace_id == workspace.workspace_id,
        ContactORM.merged_into_id.is_(None),
    ]
    _restrict_record_scope(conditions, workspace, ContactORM.id)
    if search:
        conditions.append(
            or_(ContactORM.name.ilike(f"%{search}%"), ContactORM.email.ilike(f"%{search}%"))
        )
    if company_id:
        conditions.append(ContactORM.company_id == company_id)
    total = session.scalar(select(func.count()).select_from(ContactORM).where(*conditions))
    order = {
        "name": ContactORM.name,
        "recent": ContactORM.updated_at.desc(),
        "company": ContactORM.company_id,
    }[sort]
    rows = list(
        session.scalars(
            select(ContactORM)
            .where(*conditions)
            .order_by(order, ContactORM.id)
            .offset(offset)
            .limit(limit)
        )
    )
    return {"items": rows, "total": total}


@router.put("/contacts/{contact_id}", response_model=ContactRecord)
def save_contact(
    contact_id: UUID,
    request: ContactInput,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> Any:
    _require_field_permission(workspace, "fields.contact.edit")
    if request.company_id:
        owned_company(session, workspace, request.company_id)
    row = session.get(ContactORM, contact_id)
    if row and row.workspace_id != workspace.workspace_id:
        raise HTTPException(404, "Contact not found")
    if row and row.merged_into_id:
        raise HTTPException(409, "This contact was merged; edit the surviving record.")
    if row:
        owned_contact(session, workspace, contact_id)
    if row is None:
        row = ContactORM(id=contact_id, workspace_id=workspace.workspace_id)
        session.add(row)
    for key, value in request.model_dump(exclude={"profile"}).items():
        setattr(row, key, value)
    row.profile = merge_record_profile(row.profile, request.profile)
    row.updated_at = datetime.now(UTC)
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "contact.saved",
        {"contact_id": str(contact_id)},
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "This email already has a contact in the workspace") from None
    return row


@router.get("/contacts/{contact_id}", response_model=ContactDetail)
def contact_detail(
    contact_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> Any:
    _require_field_permission(workspace, "fields.contact.view")
    contact = owned_contact(session, workspace, contact_id)
    leads = list(
        session.scalars(
            select(LeadORM)
            .join(LeadContactORM)
            .where(
                LeadORM.workspace_id == workspace.workspace_id,
                LeadContactORM.contact_id == contact.id,
            )
            .order_by(LeadORM.received_at.desc())
            .limit(100)
        )
    )
    return {
        "contact": contact,
        "company": owned_company(session, workspace, contact.company_id)
        if contact.company_id
        else None,
        "touches": [
            {
                "id": lead.id,
                "source": lead.source,
                "received_at": lead.received_at,
                "score": lead.qualification.total_score if lead.qualification else None,
                "stage": lead.outcome.pipeline_stage if lead.outcome else "open",
            }
            for lead in leads
        ],
    }


@router.get("/companies", response_model=CompanyPage)
def companies(
    search: str = Query("", max_length=200),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> Any:
    conditions = [CompanyORM.workspace_id == workspace.workspace_id]
    if workspace.role not in {"owner", "admin"}:
        conditions.append(
            CompanyORM.id.in_(
                select(ContactORM.company_id).where(
                    ContactORM.id.in_(_accessible_contact_ids(workspace)),
                    ContactORM.company_id.is_not(None),
                )
            )
        )
    if search:
        conditions.append(
            or_(CompanyORM.name.ilike(f"%{search}%"), CompanyORM.domain.ilike(f"%{search}%"))
        )
    total = session.scalar(select(func.count()).select_from(CompanyORM).where(*conditions))
    rows = list(
        session.scalars(
            select(CompanyORM)
            .where(*conditions)
            .order_by(CompanyORM.name, CompanyORM.id)
            .offset(offset)
            .limit(limit)
        )
    )
    return {"items": rows, "total": total}


@router.put("/companies/{company_id}", response_model=CompanyRecord)
def save_company(
    company_id: UUID,
    request: CompanyInput,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> Any:
    row = session.get(CompanyORM, company_id)
    if row and row.workspace_id != workspace.workspace_id:
        raise HTTPException(404, "Company not found")
    if row:
        owned_company(session, workspace, company_id)
    if row is None:
        row = CompanyORM(id=company_id, workspace_id=workspace.workspace_id)
        session.add(row)
    for key, value in request.model_dump(exclude={"profile"}).items():
        setattr(row, key, value)
    row.profile = merge_record_profile(row.profile, request.profile)
    row.identity_key = company_identity(request.name, request.domain)
    row.updated_at = datetime.now(UTC)
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "company.saved",
        {"company_id": str(company_id), "website_reviewed": request.website_reviewed},
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "A company with this domain or name already exists") from None
    return row


@router.get("/companies/{company_id}", response_model=CompanyDetail)
def company_detail(
    company_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> Any:
    company = owned_company(session, workspace, company_id)
    contact_conditions = [
        ContactORM.workspace_id == workspace.workspace_id,
        ContactORM.company_id == company_id,
        ContactORM.merged_into_id.is_(None),
    ]
    _restrict_record_scope(contact_conditions, workspace, ContactORM.id)
    contacts = list(
        session.scalars(
            select(ContactORM)
            .where(*contact_conditions)
            .order_by(ContactORM.name)
            .limit(100)
        )
    )
    rows = list(
        session.scalars(
            select(LeadORM)
            .join(LeadContactORM)
            .join(ContactORM, ContactORM.id == LeadContactORM.contact_id)
            .where(
                LeadORM.workspace_id == workspace.workspace_id, ContactORM.company_id == company_id
            )
            .order_by(LeadORM.received_at.desc())
            .limit(500)
        )
    )
    revenue: dict[str, float] = {}
    if "fields.financial.view" in workspace.permissions:
        for lead in rows:
            if lead.outcome and lead.outcome.pipeline_stage == "closed_won":
                currency = lead.outcome.currency or "USD"
                revenue[currency] = revenue.get(currency, 0) + float(
                    lead.outcome.closed_revenue or 0
                )
    return {
        "company": company,
        "contacts": contacts,
        "lead_count": len(rows),
        "truncated": len(rows) == 500,
        "won": sum(
            bool(lead.outcome and lead.outcome.pipeline_stage == "closed_won") for lead in rows
        ),
        "revenue_by_currency": revenue,
        "touches": [
            {
                "id": lead.id,
                "name": lead.full_name,
                "source": lead.source,
                "received_at": lead.received_at,
            }
            for lead in rows
        ],
    }


@router.get("/duplicates")
def duplicates(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> Any:
    _require_field_permission(workspace, "fields.contact.view")
    conditions = [
        ContactORM.workspace_id == workspace.workspace_id,
        ContactORM.merged_into_id.is_(None),
    ]
    _restrict_record_scope(conditions, workspace, ContactORM.id)
    rows = list(
        session.scalars(
            select(ContactORM)
            .where(*conditions)
            .order_by(ContactORM.updated_at.desc())
            .limit(500)
        )
    )
    return {
        "items": duplicate_suggestions(rows),
        "contacts_scanned": len(rows),
        "truncated": len(rows) == 500,
    }


class MergeRequest(BaseModel):
    target_id: UUID
    confirm: bool


@router.post("/contacts/{contact_id}/merge")
def merge_contact(
    contact_id: UUID,
    request: MergeRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> Any:
    _require_field_permission(workspace, "fields.contact.edit")
    source = owned_contact(session, workspace, contact_id)
    target = owned_contact(session, workspace, request.target_id)
    if not request.confirm or source.id == target.id:
        raise HTTPException(422, "Review both people and confirm an explicit merge")
    session.execute(
        update(LeadContactORM)
        .where(LeadContactORM.contact_id == source.id)
        .values(contact_id=target.id)
    )
    session.execute(
        update(ContactORM)
        .where(
            ContactORM.workspace_id == workspace.workspace_id,
            ContactORM.merged_into_id == source.id,
        )
        .values(merged_into_id=target.id)
    )
    source.merged_into_id = target.id
    # Preserve both emails and consent ledgers; merging never grants outreach permission.
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "contact.merged",
        {"source_id": str(source.id), "target_id": str(target.id)},
    )
    session.commit()
    return {"merged_into_id": target.id}
