"""Commercial facts shared by pipeline, lead detail and revenue screens."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission
from lead_agent.application.governance import audit_operation
from lead_agent.application.commercial import quote_total
from lead_agent.application.routing import record_pipeline_event
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.deals import CommercialSettingsORM, DealORM
from lead_agent.infrastructure.db.models import LeadORM, UserORM, WorkspaceMembershipORM
from lead_agent.infrastructure.db.session import get_db

router = APIRouter(tags=["deal desk"])
def exact_cents(value: float) -> float:
    amount = Decimal(str(value))
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("Amounts must have at most two decimal places")
    return value


Amount = Annotated[float, Field(ge=0, le=1e12, allow_inf_nan=False), AfterValidator(exact_cents)]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Stage = Literal["new", "contacted", "engaged", "quoted", "negotiation", "contract", "won", "lost"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CatalogItem(Strict):
    id: str = Field(max_length=100)
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)
    unitPrice: Amount
    billing: Literal["one_time", "monthly"]
    unit: str = Field(max_length=100)


class QuoteLine(CatalogItem):
    quantity: Annotated[float, Field(ge=0, le=1e6, allow_inf_nan=False)]
    discountPct: Percent = 0


class Quote(Strict):
    id: str = Field(max_length=100)
    number: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)
    status: Literal["draft", "sent", "accepted", "declined", "superseded"]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    lines: list[QuoteLine] = Field(max_length=100)
    discountPct: Percent = 0
    taxPct: Percent = 0
    termMonths: int = Field(ge=1, le=1200)
    validUntil: date | None = None
    notes: str = Field(default="", max_length=8000)
    createdAt: datetime
    sentAt: datetime | None = None
    decidedAt: datetime | None = None

    @model_validator(mode="after")
    def safe_total(self) -> "Quote":
        if quote_total(self.model_dump(mode="json")) > Decimal("1000000000000"):
            raise ValueError("Quote total exceeds the supported amount")
        if len({line.id for line in self.lines}) != len(self.lines):
            raise ValueError("Quote line identifiers must be unique")
        return self


class Negotiation(Strict):
    id: str = Field(max_length=100)
    at: datetime
    party: Literal["us", "client"]
    kind: Literal["offer", "counter", "concession", "objection", "agreement", "note"]
    amount: Amount | None = None
    summary: str = Field(min_length=1, max_length=4000)


class Sla(Strict):
    status: Literal["draft", "proposed", "agreed", "signed"]
    responseHours: Annotated[float, Field(gt=0, le=8760, allow_inf_nan=False)]
    resolutionHours: Annotated[float, Field(gt=0, le=8760, allow_inf_nan=False)]
    uptimePct: Percent
    supportWindow: str = Field(max_length=255)
    termMonths: int = Field(ge=1, le=1200)
    paymentTerms: str = Field(max_length=2000)
    startDate: date | None = None
    renewal: Literal["auto", "manual"]
    signedBy: str = Field(default="", max_length=255)
    signedAt: datetime | None = None
    notes: str = Field(default="", max_length=8000)

    @model_validator(mode="after")
    def signed_evidence(self) -> "Sla":
        if self.resolutionHours < self.responseHours:
            raise ValueError("Resolution time must be at least the response time")
        if self.status == "signed" and (not self.signedBy or not self.signedAt):
            raise ValueError("Signed terms require the signer's name and signing date")
        return self


class Generator(Strict):
    id: UUID
    name: str = Field(max_length=255)


class Activity(Strict):
    id: str = Field(max_length=100)
    at: datetime
    text: str = Field(max_length=4000)
    kind: Literal["stage", "quote", "negotiation", "sla", "value", "note"]


class Deal(Strict):
    leadId: UUID
    stage: Stage | None = None
    stageChangedAt: datetime | None = None
    valueOverride: Amount | None = None
    expectedClose: date | None = None
    generatedBy: Generator | None = None
    quotes: list[Quote] = Field(default_factory=list, max_length=100)
    negotiation: list[Negotiation] = Field(default_factory=list, max_length=500)
    sla: Sla | None = None
    activity: list[Activity] = Field(default_factory=list, max_length=80)
    updatedAt: datetime


class DealUpdate(Strict):
    version: int = Field(ge=0)
    deal: Deal


class CommercialSettings(Strict):
    version: int = Field(ge=0)
    catalog: list[CatalogItem] = Field(default_factory=list, max_length=500)
    monthlyGoal: Amount | None = None
    quoteCounter: int = Field(default=0, ge=0)


def owned_lead(session: Session, workspace: WorkspaceContext, lead_id: UUID) -> LeadORM:
    lead = session.get(LeadORM, lead_id)
    if lead is None or lead.workspace_id != workspace.workspace_id:
        raise HTTPException(404, "Lead not found")
    return lead


@router.get("/v1/deals")
def list_deals(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> dict[str, Any]:
    rows = session.scalars(select(DealORM).where(DealORM.workspace_id == workspace.workspace_id))
    settings = session.get(CommercialSettingsORM, workspace.workspace_id)
    return {
        "deals": {str(row.lead_id): {"version": row.version, "deal": row.data} for row in rows},
        "settings": {"version": settings.version, **settings.data} if settings else None,
    }


@router.put("/v1/leads/{lead_id}/deal")
def save_deal(
    lead_id: UUID,
    request: DealUpdate,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> dict[str, Any]:
    lead = owned_lead(session, workspace, lead_id)
    if request.deal.leadId != lead_id:
        raise HTTPException(422, "The deal must belong to the requested lead")
    if request.deal.stage in {"won", "lost"}:
        # Closing remains the existing, audited progress/outcome operation.
        if not lead.outcome or lead.outcome.pipeline_stage != (
            "closed_won" if request.deal.stage == "won" else "closed_lost"
        ):
            raise HTTPException(
                409, "Record the closing outcome before assigning a closed deal stage"
            )
    elif (
        lead.outcome
        and lead.outcome.pipeline_stage in {"closed_won", "closed_lost"}
        and request.deal.stage
    ):
        raise HTTPException(409, "A closed lead cannot be reopened from the deal desk")
    row = session.get(DealORM, lead_id)
    if (row.version if row else 0) != request.version:
        raise HTTPException(409, "This deal changed elsewhere. Reload before saving your changes.")
    previous_stage = row.data.get("stage") if row else None
    data = request.deal.model_dump(mode="json")
    if request.deal.generatedBy:
        member = session.scalar(
            select(WorkspaceMembershipORM).where(
                WorkspaceMembershipORM.workspace_id == workspace.workspace_id,
                WorkspaceMembershipORM.user_id == request.deal.generatedBy.id,
                WorkspaceMembershipORM.status == "active",
            )
        )
        user = session.get(UserORM, request.deal.generatedBy.id)
        if member is None or user is None or user.status != "active":
            raise HTTPException(422, "Choose an active member of this workspace")
        data["generatedBy"]["name"] = user.display_name
    data["updatedAt"] = datetime.now(UTC).isoformat()
    if row:
        result = session.execute(
            update(DealORM)
            .where(DealORM.lead_id == lead_id, DealORM.version == request.version)
            .values(
                data=data,
                version=request.version + 1,
                updated_by=workspace.actor_id,
                updated_at=datetime.now(UTC),
            )
        )
        if result.rowcount != 1:
            raise HTTPException(409, "This deal changed elsewhere. Reload before saving.")
    else:
        session.add(
            DealORM(
                lead_id=lead_id,
                workspace_id=workspace.workspace_id,
                version=1,
                data=data,
                updated_by=workspace.actor_id,
                updated_at=datetime.now(UTC),
            )
        )
    if previous_stage != data["stage"] and data["stage"]:
        record_pipeline_event(
            session, lead_id, "deal_stage", str(data["stage"]), {"actor_id": workspace.actor_id}
        )
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "deal.saved",
        {"lead_id": str(lead_id), "version": request.version + 1},
    )
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(409, "This deal was saved elsewhere. Reload before saving.") from error
    return {"version": request.version + 1, "deal": data}


@router.put("/v1/deals/settings")
def save_settings(
    request: CommercialSettings,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("workspace.manage")),
) -> dict[str, Any]:
    row = session.get(CommercialSettingsORM, workspace.workspace_id)
    if (row.version if row else 0) != request.version:
        raise HTTPException(409, "Revenue settings changed elsewhere. Reload before saving.")
    data = request.model_dump(mode="json", exclude={"version"})
    if row:
        result = session.execute(
            update(CommercialSettingsORM)
            .where(
                CommercialSettingsORM.workspace_id == workspace.workspace_id,
                CommercialSettingsORM.version == request.version,
            )
            .values(
                data=data,
                version=request.version + 1,
                updated_by=workspace.actor_id,
                updated_at=datetime.now(UTC),
            )
        )
        if result.rowcount != 1:
            raise HTTPException(409, "Revenue settings changed elsewhere. Reload before saving.")
    else:
        session.add(
            CommercialSettingsORM(
                workspace_id=workspace.workspace_id,
                version=1,
                data=data,
                updated_by=workspace.actor_id,
                updated_at=datetime.now(UTC),
            )
        )
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "deal.settings_saved",
        {"version": request.version + 1},
    )
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            409, "Revenue settings changed elsewhere. Reload before saving."
        ) from error
    return {"version": request.version + 1, **data}
