from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from lead_agent.api.dependencies import (
    get_policies,
    get_workspace_context,
    require_permission,
)
from lead_agent.api.schemas import (
    LeadCreateRequest,
    LeadListResponse,
    LeadResponse,
    QualificationResponse,
    lead_response,
    qualification_response,
)
from lead_agent.application.exports import (
    ExportEmptyError,
    ExportLimitError,
    LeadExportRequest,
    canonical_records,
    list_export_leads,
    render_export,
    write_export_audit,
)
from lead_agent.application.services import process_lead
from lead_agent.domain.analysis import ReasoningProvider
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.routing import NotificationProvider
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import (
    CrmSyncORM,
    LeadORM,
    QualificationResultORM,
    RoutingDecisionORM,
    UserORM,
    WorkspaceORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.notifications.factory import get_notification_provider
from lead_agent.infrastructure.reasoning.factory import get_reasoning_provider
from lead_agent.infrastructure.repositories import (
    get_current_crm_sync,
    get_current_routing,
    get_lead,
    get_qualification,
)
from lead_agent.settings.config import Settings, get_settings

router = APIRouter(prefix="/v1/leads", tags=["leads"])


class LeadTitleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    job_title: str = Field(min_length=1, max_length=255)

    @field_validator("job_title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("Job title cannot be blank")
        return value


def policy_dependency() -> tuple[IcpConfig, RoutingConfig]:
    return get_policies()


def _process_create_request(
    request: LeadCreateRequest,
    session: Session,
    policies: tuple[IcpConfig, RoutingConfig],
    provider: ReasoningProvider,
    notification_provider: NotificationProvider,
    workspace: WorkspaceContext,
) -> LeadResponse:
    try:
        _, _, row = process_lead(
            session,
            request.model_dump(mode="json"),
            *policies,
            provider=provider,
            notification_provider=notification_provider,
            workspace_id=workspace.workspace_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return lead_response(
        row,
        get_current_routing(session, row.id, workspace.workspace_id),
        get_current_crm_sync(session, row.id, workspace_id=workspace.workspace_id),
    )


@router.post("", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
def create_lead(
    request: LeadCreateRequest,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(policy_dependency),
    provider: ReasoningProvider = Depends(get_reasoning_provider),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> LeadResponse:
    return _process_create_request(
        request,
        session,
        policies,
        provider,
        notification_provider,
        workspace,
    )


@router.get("", response_model=LeadListResponse)
def list_all_leads(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=200),
    source: str | None = Query(default=None, max_length=64),
    qualification_band: str | None = Query(default=None, max_length=64),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> LeadListResponse:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")

    conditions = [LeadORM.workspace_id == workspace.workspace_id]
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(
            or_(
                LeadORM.full_name.ilike(pattern),
                LeadORM.email.ilike(pattern),
                LeadORM.company_name.ilike(pattern),
                LeadORM.product_or_service.ilike(pattern),
            )
        )
    if source:
        conditions.append(LeadORM.source == source)
    if qualification_band:
        conditions.append(QualificationResultORM.classification == qualification_band)
    if date_from:
        conditions.append(
            LeadORM.received_at >= datetime.combine(date_from, time.min, tzinfo=UTC)
        )
    if date_to:
        end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=UTC)
        conditions.append(LeadORM.received_at < end)

    count_statement = select(func.count(LeadORM.id)).where(*conditions)
    if qualification_band:
        count_statement = count_statement.join(QualificationResultORM)
    total = int(session.scalar(count_statement) or 0)
    lead_statement = select(LeadORM)
    if qualification_band:
        lead_statement = lead_statement.join(QualificationResultORM)
    rows = list(
        session.scalars(
            lead_statement
            .options(
                joinedload(LeadORM.qualification),
                joinedload(LeadORM.analysis),
                selectinload(LeadORM.routing_decisions).selectinload(
                    RoutingDecisionORM.notifications
                ),
                selectinload(LeadORM.crm_syncs),
            )
            .where(*conditions)
            .order_by(LeadORM.received_at.desc(), LeadORM.id.desc())
            .offset(offset)
            .limit(limit)
        ).unique()
    )

    def current_routing(row: LeadORM) -> RoutingDecisionORM | None:
        current = [item for item in row.routing_decisions if item.is_current]
        return max(current, key=lambda item: item.created_at, default=None)

    def current_crm_sync(row: LeadORM) -> CrmSyncORM | None:
        current = [item for item in row.crm_syncs if item.is_current]
        return max(current, key=lambda item: item.requested_at, default=None)

    return LeadListResponse(
        items=[
            lead_response(
                row,
                current_routing(row),
                current_crm_sync(row),
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
    )


@router.post("/export")
def export_leads(
    request: LeadExportRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(get_workspace_context),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Generate a bounded, workspace-scoped business export."""
    if "leads.export" not in workspace.permissions:
        write_export_audit(
            session, workspace, request, outcome="denied", error_code="permission_required"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="permission required: leads.export"
        )
    try:
        rows = list_export_leads(
            session,
            workspace.workspace_id,
            request,
            limit=settings.export_sync_limit + 1,
        )
        if len(rows) > settings.export_sync_limit:
            write_export_audit(
                session, workspace, request, outcome="rejected", error_code="export_limit_exceeded"
            )
            raise ExportLimitError(
                "This export exceeds the synchronous export limit; "
                "narrow the filters and try again."
            )
        if not rows:
            write_export_audit(
                session,
                workspace,
                request,
                outcome="empty",
                record_count=0,
                error_code="no_matching_leads",
            )
            raise ExportEmptyError("No leads match the selected export criteria.")
        records = canonical_records(rows, workspace.workspace_id)
        workspace_row = session.get(WorkspaceORM, workspace.workspace_id)
        actor = session.get(UserORM, workspace.user_id) if workspace.user_id else None
        payload, media_type, extension = render_export(
            request,
            records,
            workspace_row.name if workspace_row else "Current workspace",
            actor.display_name if actor else workspace.actor_id,
        )
        write_export_audit(
            session, workspace, request, outcome="success", record_count=len(records)
        )
    except ExportEmptyError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except ExportLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(error)
        ) from error
    except HTTPException:
        raise
    except Exception as error:
        session.rollback()
        write_export_audit(
            session, workspace, request, outcome="failed", error_code="generation_failed"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to generate export"
        ) from error
    filename = f"ingenuity-leads-{datetime.now(UTC).date().isoformat()}.{extension}"
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{lead_id}", response_model=LeadResponse)
def retrieve_lead(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> LeadResponse:
    row = get_lead(session, lead_id, workspace.workspace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead_response(
        row,
        get_current_routing(session, row.id, workspace.workspace_id),
        get_current_crm_sync(session, row.id, workspace_id=workspace.workspace_id),
    )


@router.patch("/{lead_id}", response_model=LeadResponse)
def update_lead_title(
    lead_id: UUID,
    request: LeadTitleUpdateRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> LeadResponse:
    """Edit a source field locally; CRM delivery remains an explicit separate action."""
    row = get_lead(session, lead_id, workspace.workspace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    row.job_title = request.job_title
    row.updated_at = datetime.now(UTC)
    session.commit()
    return lead_response(
        row,
        get_current_routing(session, row.id, workspace.workspace_id),
        get_current_crm_sync(session, row.id, workspace_id=workspace.workspace_id),
    )


@router.get("/{lead_id}/qualification", response_model=QualificationResponse)
def retrieve_qualification(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> QualificationResponse:
    if get_lead(session, lead_id, workspace.workspace_id) is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    result = get_qualification(session, lead_id, workspace.workspace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Qualification not found")
    return qualification_response(result)  # type: ignore[return-value]
