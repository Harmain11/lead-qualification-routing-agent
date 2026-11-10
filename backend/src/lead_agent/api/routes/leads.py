from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from lead_agent.api.dependencies import (
    get_policies,
    get_workspace_context,
    require_permission,
    require_permissions,
)
from lead_agent.api.schemas import (
    LeadCreateRequest,
    LeadListResponse,
    LeadOutcomeResponse,
    LeadResponse,
    QualificationResponse,
    lead_response,
    outcome_response,
    qualification_response,
)
from lead_agent.application.assignment_notifications import notify_assigned_member
from lead_agent.application.exports import (
    ExportEmptyError,
    ExportLimitError,
    LeadExportRequest,
    allowed_export_columns,
    canonical_records,
    list_export_leads,
    render_export,
    write_export_audit,
)
from lead_agent.application.field_privacy import assert_field_writes
from lead_agent.application.gmail_history import (
    append_gmail_thread_messages,
    lead_history_context,
)
from lead_agent.application.gmail_profile import extract_stated_gmail_profile
from lead_agent.application.integration_access import assert_connection_action
from lead_agent.application.mailbox_access import can_use_mailbox
from lead_agent.application.governance import operations_policy
from lead_agent.application.icp import resolve_workspace_icp
from lead_agent.application.lead_outcomes import record_outcome, send_win_notifications
from lead_agent.application.lead_access import (
    assert_lead_action,
    assign_lead,
    revoke_lead_share,
    share_lead,
)
from lead_agent.application.manual_review import lead_manual_review_reasons
from lead_agent.application.routing import record_pipeline_event, route_and_notify
from lead_agent.application.services import _analyze_and_qualify, process_lead
from lead_agent.domain.analysis import (
    ReasoningProvider,
    ReasoningProviderError,
    load_persisted_analysis,
)
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.lead_assistant import LeadProfileChanges
from lead_agent.domain.lead_sources import MANUAL_SOURCE, MANUAL_SOURCE_DETAIL
from lead_agent.domain.normalization import normalize_lead
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.domain.rbac import effective_permissions
from lead_agent.domain.routing import NotificationProvider
from lead_agent.domain.scoring import qualify_lead
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.crm.inbound_factory import (
    WorkspaceInboundCrmProviderResolver,
    get_workspace_inbound_crm_resolver,
)
from lead_agent.infrastructure.db.models import (
    CrmProviderRecordLinkORM,
    CrmSyncORM,
    LeadFollowUpEmailORM,
    LeadORM,
    LeadSuppressionORM,
    PipelineEventORM,
    ProviderConnectionORM,
    QualificationResultORM,
    RoutingDecisionORM,
    SourceEventORM,
    UserORM,
    WorkspaceMembershipORM,
    WorkspaceORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.db.teams import LeadAccessGrantORM
from lead_agent.infrastructure.notifications.factory import get_notification_provider
from lead_agent.infrastructure.reasoning.factory import resolve_workspace_reasoning_provider
from lead_agent.infrastructure.repositories import (
    get_current_crm_sync,
    get_current_routing,
    get_lead,
    get_qualification,
    update_lead_with_result,
)
from lead_agent.infrastructure.slack import (
    WorkspaceSlackNotificationResolver,
    get_workspace_slack_notification_resolver,
)
from lead_agent.settings.config import Settings, get_settings

router = APIRouter(prefix="/v1/leads", tags=["leads"])
_LOGGER = logging.getLogger("lead_agent.api.leads")


def _current_confirmation_email(
    session: Session, lead_id: UUID, workspace_id: UUID
) -> LeadFollowUpEmailORM | None:
    return session.scalar(
        select(LeadFollowUpEmailORM)
        .where(
            LeadFollowUpEmailORM.lead_id == lead_id,
            LeadFollowUpEmailORM.workspace_id == workspace_id,
            LeadFollowUpEmailORM.kind == "lead_confirmation",
        )
        .order_by(LeadFollowUpEmailORM.updated_at.desc())
    )


class LeadTitleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    job_title: str = Field(min_length=1, max_length=255)

    @field_validator("job_title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("Job title cannot be blank")
        return value


class LeadGeneratedUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_generated: bool


class LeadAssignmentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assigned_rep_id: UUID | None


class LeadShareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    membership_id: UUID
    can_edit: bool = False
    reason: str = Field(default="collaboration", max_length=255)


class LeadShareResponse(BaseModel):
    membership_id: UUID
    can_edit: bool
    reason: str
    expires_at: datetime | None


class LeadAssignmentOptionResponse(BaseModel):
    user_id: UUID
    display_name: str


class LeadProfileUpdateRequest(LeadProfileChanges):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def require_changes(self) -> LeadProfileUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("Provide at least one lead field to update.")
        for field in ("full_name", "email", "company_name", "product_or_service"):
            value = getattr(self, field)
            if field in self.model_fields_set and (value is None or not value):
                raise ValueError(f"{field.replace('_', ' ').title()} cannot be blank.")
        return self


class LeadBulkDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead_ids: list[UUID] = Field(min_length=1, max_length=500)


class LeadProgressUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    stage: Literal[
        "contacted", "responded", "meeting_scheduled", "proposal_sent", "converted", "lost"
    ]
    note: str = Field(min_length=3, max_length=2000)
    conversion_type: Literal["project", "sale"] | None = None
    closed_revenue: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    credited_user_id: UUID | None = None
    send_direct_message: bool = False

    @model_validator(mode="after")
    def validate_closing_fields(self) -> LeadProgressUpdate:
        if self.stage == "converted" and self.conversion_type is None:
            raise ValueError("Choose Project or Sale for a successful conversion.")
        if self.stage != "converted" and (
            self.conversion_type is not None or self.closed_revenue is not None
        ):
            raise ValueError(
                "Conversion type and revenue are only allowed for a successful conversion."
            )
        if self.stage not in {"converted", "lost"} and self.credited_user_id is not None:
            raise ValueError("Member credit is only allowed when closing a lead.")
        if self.send_direct_message and self.stage != "converted":
            raise ValueError(
                "Direct Slack congratulations are only available for successful conversions."
            )
        return self


class LeadProgressResponse(BaseModel):
    stage: str
    note: str
    classification: str
    score: int
    rationale: list[str]
    updated_at: datetime
    outcome: LeadOutcomeResponse | None = None


class GmailConversationSyncResponse(BaseModel):
    messages_added: int
    profile_updated_fields: list[str] = Field(default_factory=list)
    threads_checked: int
    threads_unreadable: int = 0
    truncated: bool
    classification: str
    score: int


class RetryConfirmedMeetingBookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    offer_source_message_id: str = Field(min_length=1, max_length=512)


class RetryConfirmedMeetingBookingResponse(BaseModel):
    status: str
    message: str
    booking_error: str | None = None


class LeadMomentumEvidence(BaseModel):
    source: str
    excerpt: str
    observed_at: datetime | None = None


class LeadDeliveryCheckpoint(BaseModel):
    destination: str
    status: str
    detail: str
    attempted_at: datetime | None = None
    needs_attention: bool = False


class LeadMomentumResponse(BaseModel):
    score: int
    classification: str
    state: Literal["new", "warming", "cooling", "steady", "awaiting_review"]
    score_delta: int | None = None
    previous_score: int | None = None
    confidence: float | None = None
    freshness_hours: int
    observed_signals: int
    last_signal_at: datetime | None = None
    next_human_action: str
    evidence: list[LeadMomentumEvidence]
    delivery: list[LeadDeliveryCheckpoint]


def _delete_lead(
    session: Session,
    row: LeadORM,
    *,
    actor_id: str,
) -> None:
    """Move a lead to trash and suppress its source record on future syncs."""
    now = datetime.now(UTC)
    source_links = list(
        session.scalars(
            select(CrmProviderRecordLinkORM).where(
                CrmProviderRecordLinkORM.workspace_id == row.workspace_id,
                CrmProviderRecordLinkORM.lead_id == row.id,
            )
        )
    )
    suppression_sources = {(link.provider, link.provider_record_id) for link in source_links}
    if not suppression_sources and row.external_id:
        suppression_sources.add((row.source, row.external_id))
    for provider, external_id in suppression_sources:
        existing = session.scalar(
            select(LeadSuppressionORM).where(
                LeadSuppressionORM.workspace_id == row.workspace_id,
                LeadSuppressionORM.provider == provider,
                LeadSuppressionORM.external_id == external_id,
            )
        )
        if existing is None:
            session.add(
                LeadSuppressionORM(
                    workspace_id=row.workspace_id,
                    lead_id=row.id,
                    provider=provider,
                    external_id=external_id,
                    lead_email=row.email,
                    reason="user_deleted",
                    created_by=actor_id,
                    created_at=now,
                )
            )
        elif existing.lead_id is None:
            existing.lead_id = row.id
    if row.source_event_id:
        event = session.get(SourceEventORM, row.source_event_id)
        if event is not None:
            event.status = "deleted"
            event.error_message = (
                "Lead moved to trash by workspace user; matching source is suppressed."
            )
            event.updated_at = now
    row.deleted_at = now
    row.deleted_by = actor_id
    row.updated_at = now


class LeadTrashItemResponse(BaseModel):
    id: UUID
    provider: str
    external_id: str
    lead_email: str | None = None
    reason: str
    created_by: str
    created_at: datetime


@router.get("/trash", response_model=list[LeadTrashItemResponse])
def list_lead_trash(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> list[LeadTrashItemResponse]:
    rows = session.scalars(
        select(LeadSuppressionORM)
        .where(LeadSuppressionORM.workspace_id == workspace.workspace_id)
        .order_by(LeadSuppressionORM.created_at.desc())
        .limit(200)
    ).all()
    return [
        LeadTrashItemResponse(
            id=row.id,
            provider=row.provider,
            external_id=row.external_id,
            lead_email=row.lead_email,
            reason=row.reason,
            created_by=row.created_by,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/trash/{suppression_id}/restore")
def restore_lead_from_trash(
    suppression_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.delete_restore")),
) -> dict[str, str]:
    row = session.scalar(
        select(LeadSuppressionORM).where(
            LeadSuppressionORM.id == suppression_id,
            LeadSuppressionORM.workspace_id == workspace.workspace_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Trash item not found")
    provider = row.provider
    external_id = row.external_id
    lead = None
    if row.lead_id is not None:
        lead = session.scalar(
            select(LeadORM)
            .execution_options(include_deleted_leads=True)
            .where(
                LeadORM.id == row.lead_id,
                LeadORM.workspace_id == workspace.workspace_id,
            )
        )
    if lead is not None:
        assert_lead_action(session, workspace, lead, "delete_restore")
        restored_at = datetime.now(UTC)
        lead.deleted_at = None
        lead.deleted_by = None
        lead.updated_at = restored_at
        if lead.source_event is not None and lead.source_event.status == "deleted":
            lead.source_event.status = "processed"
            lead.source_event.error_message = None
            lead.source_event.updated_at = restored_at
        session.execute(
            delete(LeadSuppressionORM).where(
                LeadSuppressionORM.workspace_id == workspace.workspace_id,
                LeadSuppressionORM.lead_id == lead.id,
            )
        )
        record_pipeline_event(
            session,
            lead.id,
            "trash",
            "restored",
            {"actor_id": workspace.actor_id},
        )
    else:
        # Legacy trash rows predate lossless soft deletion. Their source can
        # be imported again, but already-deleted related history is unavailable.
        session.delete(row)
    session.commit()
    return {
        "status": "restored" if lead is not None else "restore_pending",
        "provider": provider,
        "external_id": external_id,
        "lead_id": str(lead.id) if lead is not None else "",
    }


def policy_dependency() -> tuple[IcpConfig, RoutingConfig]:
    return get_policies()


def _process_create_request(
    request: LeadCreateRequest,
    session: Session,
    policies: tuple[IcpConfig, RoutingConfig],
    provider: ReasoningProvider | None,
    notification_provider: NotificationProvider,
    workspace: WorkspaceContext,
) -> LeadResponse:
    active_provider = provider or resolve_workspace_reasoning_provider(
        session, workspace.workspace_id
    )
    if getattr(active_provider, "provider_name", "") in {
        "",
        "deterministic",
        "deterministic-safeguard",
    }:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Connect and verify a workspace AI provider in Integrations before "
                "creating a lead. The lead was not created."
            ),
        )
    try:
        _, _, row = process_lead(
            session,
            request.model_dump(mode="json"),
            *policies,
            provider=active_provider,
            notification_provider=notification_provider,
            workspace_id=workspace.workspace_id,
            require_provider_success=True,
            analyze_hard_disqualified=True,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ReasoningProviderError as error:
        message = str(error).strip() or "The AI provider did not complete analysis."
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"AI analysis failed; the lead was not created. {message[:500]}",
        ) from error
    routing = get_current_routing(session, row.id, workspace.workspace_id)
    return lead_response(
        row,
        routing,
        get_current_crm_sync(session, row.id, workspace_id=workspace.workspace_id),
        _current_confirmation_email(session, row.id, workspace.workspace_id),
        lead_manual_review_reasons(session, row, routing),
        permissions=workspace.permissions,
    )


@router.post("", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
def create_lead(
    request: LeadCreateRequest,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(policy_dependency),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> LeadResponse:
    return _process_create_request(
        request,
        session,
        policies,
        None,
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
        if source in {"salesforce", "hubspot"}:
            conditions.append(LeadORM.source_detail.ilike(f"crm-import:{source}:%"))
        elif source == MANUAL_SOURCE:
            conditions.append(
                or_(
                    LeadORM.source == MANUAL_SOURCE,
                    LeadORM.source_detail == MANUAL_SOURCE_DETAIL,
                )
            )
        elif source == "website":
            conditions.append(
                and_(
                    LeadORM.source == source,
                    or_(
                        LeadORM.source_detail.is_(None),
                        LeadORM.source_detail != MANUAL_SOURCE_DETAIL,
                    ),
                )
            )
        else:
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

    def serialize_lead(row: LeadORM) -> LeadResponse:
        routing = current_routing(row)
        return lead_response(
            row,
            routing,
            current_crm_sync(row),
            _current_confirmation_email(session, row.id, workspace.workspace_id),
            lead_manual_review_reasons(session, row, routing),
            permissions=workspace.permissions,
        )

    return LeadListResponse(
        items=[serialize_lead(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
    )


@router.get("/closing-options")
def closing_options(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> list[dict[str, object]]:
    rows = session.execute(select(WorkspaceMembershipORM, UserORM).join(
        UserORM, UserORM.id == WorkspaceMembershipORM.user_id,
    ).where(
        WorkspaceMembershipORM.workspace_id == workspace.workspace_id,
        WorkspaceMembershipORM.status == "active", UserORM.status == "active",
    ).order_by(UserORM.display_name)).all()
    return [
        {"user_id": str(user.id), "display_name": user.display_name,
         "slack_linked": bool(member.slack_member_id)}
        for member, user in rows
        if "leads.edit" in effective_permissions(member.role, member.permission_overrides)
    ]


@router.get(
    "/assignment-options",
    response_model=list[LeadAssignmentOptionResponse],
)
def lead_assignment_options(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.reassign")),
) -> list[LeadAssignmentOptionResponse]:
    members = session.execute(
        select(WorkspaceMembershipORM, UserORM)
        .join(UserORM, UserORM.id == WorkspaceMembershipORM.user_id)
        .where(
            WorkspaceMembershipORM.workspace_id == workspace.workspace_id,
            WorkspaceMembershipORM.status == "active",
            UserORM.status == "active",
        )
        .order_by(UserORM.display_name)
    ).all()
    return [
        LeadAssignmentOptionResponse(user_id=user.id, display_name=user.display_name)
        for membership, user in members
        if "leads.edit"
        in effective_permissions(membership.role, membership.permission_overrides)
    ]


@router.post("/bulk-delete")
def delete_leads(
    request: LeadBulkDeleteRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.delete_restore")),
) -> dict[str, int]:
    rows = list(
        session.scalars(
            select(LeadORM).where(
                LeadORM.workspace_id == workspace.workspace_id,
                LeadORM.id.in_(request.lead_ids),
            )
        )
    )
    for row in rows:
        assert_lead_action(session, workspace, row, "delete_restore")
        _delete_lead(session, row, actor_id=workspace.actor_id)
    session.commit()
    return {"deleted": len(rows), "requested": len(set(request.lead_ids))}


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
    columns = allowed_export_columns(request.columns, workspace.permissions)
    if not columns:
        write_export_audit(
            session, workspace, request, outcome="denied", error_code="field_permission_required"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No requested export columns are authorized for this member.",
        )
    authorized_request = request.model_copy(update={"columns": columns})
    try:
        rows = list_export_leads(
            session,
            workspace.workspace_id,
            authorized_request,
            limit=settings.export_sync_limit + 1,
            permissions=workspace.permissions,
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
            authorized_request,
            records,
            workspace_row.name if workspace_row else "Current workspace",
            actor.display_name if actor else workspace.actor_id,
        )
        write_export_audit(
            session, workspace, authorized_request, outcome="success", record_count=len(records)
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


@router.delete("/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lead(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.delete_restore")),
) -> Response:
    row = get_lead(session, lead_id, workspace.workspace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    assert_lead_action(session, workspace, row, "delete_restore")
    _delete_lead(session, row, actor_id=workspace.actor_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{lead_id}", response_model=LeadResponse)
def retrieve_lead(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> LeadResponse:
    row = get_lead(session, lead_id, workspace.workspace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    routing = get_current_routing(session, row.id, workspace.workspace_id)
    return lead_response(
        row,
        routing,
        get_current_crm_sync(session, row.id, workspace_id=workspace.workspace_id),
        _current_confirmation_email(session, row.id, workspace.workspace_id),
        lead_manual_review_reasons(session, row, routing),
        permissions=workspace.permissions,
    )


@router.get("/{lead_id}/shares", response_model=list[LeadShareResponse])
def list_lead_shares(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.access.manage")),
) -> list[LeadShareResponse]:
    row = get_lead(session, lead_id, workspace.workspace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    assert_lead_action(session, workspace, row, "view")
    grants = session.scalars(select(LeadAccessGrantORM).where(
        LeadAccessGrantORM.workspace_id == workspace.workspace_id,
        LeadAccessGrantORM.lead_id == row.id,
    ).order_by(LeadAccessGrantORM.created_at))
    return [LeadShareResponse(
        membership_id=grant.membership_id,
        can_edit=grant.can_edit,
        reason=grant.reason,
        expires_at=grant.expires_at,
    ) for grant in grants]


@router.put("/{lead_id}/shares/{membership_id}", response_model=LeadShareResponse)
def put_lead_share(
    lead_id: UUID,
    membership_id: UUID,
    request: LeadShareRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.share")),
) -> LeadShareResponse:
    if request.membership_id != membership_id:
        raise HTTPException(422, "membership IDs must match")
    if workspace.user_id is None:
        raise HTTPException(403, "authenticated workspace actor required")
    row = get_lead(session, lead_id, workspace.workspace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    assert_lead_action(session, workspace, row, "view")
    member = session.scalar(select(WorkspaceMembershipORM).where(
        WorkspaceMembershipORM.id == membership_id,
        WorkspaceMembershipORM.workspace_id == workspace.workspace_id,
        WorkspaceMembershipORM.status == "active",
    ))
    if member is None:
        raise HTTPException(404, "workspace member not found")
    grant = share_lead(
        session,
        row,
        member,
        actor_id=workspace.user_id,
        can_edit=request.can_edit,
        reason=request.reason,
    )
    session.commit()
    return LeadShareResponse(
        membership_id=grant.membership_id,
        can_edit=grant.can_edit,
        reason=grant.reason,
        expires_at=grant.expires_at,
    )


@router.delete("/{lead_id}/shares/{membership_id}", status_code=204)
def delete_lead_share(
    lead_id: UUID,
    membership_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.share")),
) -> Response:
    if workspace.user_id is None:
        raise HTTPException(403, "authenticated workspace actor required")
    row = get_lead(session, lead_id, workspace.workspace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    assert_lead_action(session, workspace, row, "view")
    revoke_lead_share(
        session, row, membership_id, actor_id=workspace.user_id,
    )
    session.commit()
    return Response(status_code=204)


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
    assert_lead_action(session, workspace, row, "edit")
    row.job_title = request.job_title
    row.updated_at = datetime.now(UTC)
    session.commit()
    routing = get_current_routing(session, row.id, workspace.workspace_id)
    return lead_response(
        row,
        routing,
        get_current_crm_sync(session, row.id, workspace_id=workspace.workspace_id),
        _current_confirmation_email(session, row.id, workspace.workspace_id),
        lead_manual_review_reasons(session, row, routing),
        permissions=workspace.permissions,
    )


@router.patch("/{lead_id}/generated", response_model=LeadResponse)
def update_lead_generated(
    lead_id: UUID,
    request: LeadGeneratedUpdateRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> LeadResponse:
    row = get_lead(session, lead_id, workspace.workspace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    assert_lead_action(session, workspace, row, "edit")
    row.is_generated = request.is_generated
    row.updated_at = datetime.now(UTC)
    session.commit()
    routing = get_current_routing(session, row.id, workspace.workspace_id)
    return lead_response(
        row,
        routing,
        get_current_crm_sync(session, row.id, workspace_id=workspace.workspace_id),
        _current_confirmation_email(session, row.id, workspace.workspace_id),
        lead_manual_review_reasons(session, row, routing),
        permissions=workspace.permissions,
    )


@router.patch("/{lead_id}/assignment", response_model=LeadResponse)
def update_lead_assignment(
    lead_id: UUID,
    request: LeadAssignmentUpdateRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.reassign")),
) -> LeadResponse:
    row = get_lead(session, lead_id, workspace.workspace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    assert_lead_action(session, workspace, row, "reassign")
    routing = get_current_routing(session, row.id, workspace.workspace_id)
    if routing is None:
        raise HTTPException(
            status_code=409,
            detail="This lead does not have a current routing decision yet.",
        )

    previous_assignee_id = routing.assigned_rep_id
    if request.assigned_rep_id is None:
        routing.assigned_rep_id = None
        routing.assigned_rep_name = None
        assign_lead(
            session, row, None, actor=workspace, reason="manual unassignment",
        )
    else:
        assign_lead(
            session,
            row,
            request.assigned_rep_id,
            actor=workspace,
            reason="manual reassignment",
        )
        user = session.get(UserORM, request.assigned_rep_id)
        if user is None:  # assign_lead already validates; retain a fail-closed guard.
            raise HTTPException(status_code=422, detail="Choose an active workspace member.")
        routing.assigned_rep_id = str(user.id)
        routing.assigned_rep_name = user.display_name

    if (
        request.assigned_rep_id is not None
        and str(request.assigned_rep_id) != previous_assignee_id
    ):
        notify_assigned_member(
            session,
            workspace_id=workspace.workspace_id,
            lead_id=row.id,
            recipient_user_id=request.assigned_rep_id,
            assigned_by_user_id=workspace.user_id,
        )

    routing.is_manually_assigned = True
    row.updated_at = datetime.now(UTC)
    session.commit()
    routing = get_current_routing(session, row.id, workspace.workspace_id)
    return lead_response(
        row,
        routing,
        get_current_crm_sync(session, row.id, workspace_id=workspace.workspace_id),
        _current_confirmation_email(session, row.id, workspace.workspace_id),
        lead_manual_review_reasons(session, row, routing),
        permissions=workspace.permissions,
    )


@router.patch("/{lead_id}/profile", response_model=LeadResponse)
def update_lead_profile(
    lead_id: UUID,
    request: LeadProfileUpdateRequest,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(policy_dependency),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> LeadResponse:
    """Apply reviewed profile fields and refresh qualification without external writes."""
    row = get_lead(session, lead_id, workspace.workspace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    assert_lead_action(session, workspace, row, "edit")
    changes = request.model_dump(exclude_unset=True)
    assert_field_writes(workspace, changes)
    for field, value in changes.items():
        if field == "email":
            value = value.lower()
        setattr(row, field, value)
    row.updated_at = datetime.now(UTC)
    _reanalyze_lead_with_history(
        session,
        row,
        workspace.workspace_id,
        policies,
        notification_provider,
        trigger="assistant_profile_update",
    )
    session.commit()
    routing = get_current_routing(session, row.id, workspace.workspace_id)
    return lead_response(
        row,
        routing,
        get_current_crm_sync(session, row.id, workspace_id=workspace.workspace_id),
        _current_confirmation_email(session, row.id, workspace.workspace_id),
        lead_manual_review_reasons(session, row, routing),
        permissions=workspace.permissions,
    )


@router.post("/{lead_id}/manual-review/complete", response_model=LeadResponse)
def complete_lead_manual_review(
    lead_id: UUID,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(policy_dependency),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    workspace: WorkspaceContext = Depends(
        require_permissions("leads.view", "manual_review.manage")
    ),
) -> LeadResponse:
    """Complete one active review, record the decision, then apply current routing."""
    row = session.scalar(
        select(LeadORM)
        .where(
            LeadORM.id == lead_id,
            LeadORM.workspace_id == workspace.workspace_id,
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    assert_lead_action(session, workspace, row, "edit")
    if row.qualification is None:
        raise HTTPException(
            status_code=409,
            detail="This lead has no completed qualification to route from.",
        )

    current_routing = get_current_routing(
        session, row.id, workspace.workspace_id
    )
    reasons = lead_manual_review_reasons(session, row, current_routing)
    if not reasons:
        raise HTTPException(
            status_code=409,
            detail="This lead is no longer waiting for manual review.",
        )

    analysis = row.analysis
    cleared_analysis_hold = bool(
        analysis is not None and analysis.requires_manual_review
    )
    cleared_routing_hold = bool(
        current_routing is not None and current_routing.manual_review
    )
    if analysis is not None:
        analysis.requires_manual_review = False
        if analysis.status == "manual_review":
            analysis.status = "completed"
        structured_output = dict(analysis.structured_output or {})
        structured_output["requires_manual_review"] = False
        if structured_output.get("status") == "manual_review":
            structured_output["status"] = "completed"
        analysis.structured_output = structured_output
        progress_context, recorded_evidence = lead_history_context(
            session,
            workspace_id=workspace.workspace_id,
            lead_id=row.id,
        )
        normalized = replace(
            normalize_lead(
                {
                    "source": row.source,
                    "source_detail": row.source_detail,
                    "external_id": row.external_id,
                    "full_name": row.full_name,
                    "email": row.email,
                    "phone": row.phone,
                    "job_title": row.job_title,
                    "company_name": row.company_name,
                    "industry": row.industry,
                    "employee_count": row.employee_count,
                    "annual_revenue": row.annual_revenue,
                    "geography": row.geography,
                    "budget": row.budget,
                    "product_or_service": row.product_or_service,
                    "message": (
                        f"{row.message}\n\nVerified lead history (oldest first):\n"
                        f"{progress_context}"
                    ),
                    "received_at": row.received_at,
                    "raw_payload": row.raw_payload,
                    "normalization_version": row.normalization_version,
                },
                row.id,
            ),
            original_message=row.message,
            recorded_evidence=recorded_evidence,
        )
        stored_analysis = load_persisted_analysis(structured_output)
        resolved_icp = resolve_workspace_icp(session, workspace.workspace_id, policies[0])
        rescored = qualify_lead(
            normalized,
            resolved_icp.configuration,
            policies[1],
            stored_analysis,
            include_icp=resolved_icp.source == "workspace",
        )
        update_lead_with_result(
            session,
            row,
            normalized,
            resolved_icp.trace_result(rescored),
            stored_analysis,
            preserve_inquiry=True,
        )
    row.manual_review_completed_at = datetime.now(UTC)
    row.updated_at = row.manual_review_completed_at
    if row.source_event is not None and row.source_event.status == "manual_review":
        row.source_event.status = "processed"
        row.source_event.updated_at = row.manual_review_completed_at
    record_pipeline_event(
        session,
        row.id,
        "manual_review",
        "completed",
        {
            "actor_id": workspace.actor_id,
            "cleared_analysis_hold": cleared_analysis_hold,
            "cleared_routing_hold": cleared_routing_hold,
            "trigger_count": len(reasons),
        },
    )
    route_and_notify(
        session,
        row,
        policies[1],
        notification_provider=notification_provider,
        force=True,
        reroute_reason=f"Manual review completed by {workspace.actor_id}.",
    )
    routing = get_current_routing(session, row.id, workspace.workspace_id)
    return lead_response(
        row,
        routing,
        get_current_crm_sync(session, row.id, workspace_id=workspace.workspace_id),
        _current_confirmation_email(session, row.id, workspace.workspace_id),
        [],
        permissions=workspace.permissions,
    )


@router.get("/{lead_id}/qualification", response_model=QualificationResponse)
def retrieve_qualification(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> QualificationResponse:
    lead = get_lead(session, lead_id, workspace.workspace_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    assert_lead_action(session, workspace, lead, "view")
    result = get_qualification(session, lead_id, workspace.workspace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Qualification not found")
    return qualification_response(  # type: ignore[return-value]
        result,
        include_sensitive_evidence="fields.communications.view" in workspace.permissions,
    )


def _delivery_checkpoint(
    destination: str,
    status: str,
    detail: str,
    attempted_at: datetime | None = None,
) -> LeadDeliveryCheckpoint:
    return LeadDeliveryCheckpoint(
        destination=destination,
        status=status,
        detail=detail,
        attempted_at=attempted_at,
        needs_attention=status in {"failed", "retryable", "delivery_uncertain", "not_configured"},
    )


@router.get("/{lead_id}/momentum", response_model=LeadMomentumResponse)
def retrieve_lead_momentum(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> LeadMomentumResponse:
    """Explain how verified evidence has changed the lead's current temperature.

    This is deliberately a read model. It never changes scoring, routing, or
    provider state; it explains persisted decisions and delivery records.
    """
    row = get_lead(session, lead_id, workspace.workspace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    assert_lead_action(session, workspace, row, "view")

    events = list(
        session.scalars(
            select(PipelineEventORM)
            .where(
                PipelineEventORM.lead_id == lead_id,
                PipelineEventORM.workspace_id == workspace.workspace_id,
            )
            .order_by(PipelineEventORM.created_at.asc(), PipelineEventORM.id.asc())
        )
    )
    qualification = row.qualification
    score = qualification.total_score if qualification else 0
    classification = qualification.classification if qualification else "needs_qualifying"
    rechecks = [event for event in events if event.stage == "qualification_rechecked"]
    latest_recheck = rechecks[-1] if rechecks else None
    previous_score: int | None = None
    if latest_recheck:
        previous = latest_recheck.details.get("previous_qualification")
        if isinstance(previous, dict) and isinstance(previous.get("score"), int):
            previous_score = previous["score"]
    score_delta = score - previous_score if previous_score is not None else None
    if qualification is None:
        state: Literal["new", "warming", "cooling", "steady", "awaiting_review"] = "awaiting_review"
    elif row.analysis and row.analysis.requires_manual_review:
        state = "awaiting_review"
    elif score_delta is None:
        state = "new" if not rechecks else "steady"
    elif score_delta >= 5:
        state = "warming"
    elif score_delta <= -5:
        state = "cooling"
    else:
        state = "steady"

    evidence: list[LeadMomentumEvidence] = []
    if qualification:
        for line in qualification.score_breakdown:
            for item in line.get("evidence", [])[:2]:
                if isinstance(item, str) and item.strip():
                    evidence.append(
                        LeadMomentumEvidence(
                            source=f"Qualification · {line.get('component', 'score')}",
                            excerpt=item.strip()[:360],
                            observed_at=qualification.created_at,
                        )
                    )
    for event in reversed(events):
        if event.stage == "manual_progress":
            note = event.details.get("note")
            if isinstance(note, str) and note.strip():
                evidence.append(LeadMomentumEvidence(
                    source=f"Verified progress · {event.details.get('stage', event.status)}",
                    excerpt=note.strip()[:360], observed_at=event.created_at,
                ))
        elif event.stage == "gmail_message":
            subject = event.details.get("subject") or "Email conversation"
            body = event.details.get("body")
            if isinstance(body, str) and body.strip():
                evidence.append(LeadMomentumEvidence(
                    source=f"Gmail · {event.details.get('direction', event.status)} · {subject}",
                    excerpt=body.strip()[:360], observed_at=event.created_at,
                ))
        if len(evidence) >= 8:
            break
    evidence.sort(key=lambda item: item.observed_at or row.received_at, reverse=True)

    current_route = get_current_routing(session, row.id, workspace.workspace_id)
    confirmation = _current_confirmation_email(session, row.id, workspace.workspace_id)
    crm = get_current_crm_sync(session, row.id, workspace_id=workspace.workspace_id)
    notification = (
        current_route.notifications[0]
        if current_route and current_route.notifications
        else None
    )
    notification_status = (
        notification.status
        if notification
        else ("not_configured" if not current_route else "not_attempted")
    )
    notification_detail = (
        notification.error_message
        if notification and notification.error_message
        else (
            notification.provider
            if notification
            else "No notification delivery record is available."
        )
    )
    notification_at = (
        notification.sent_at
        if notification and notification.sent_at
        else (notification.updated_at if notification else None)
    )
    confirmation_detail = (
        confirmation.error_message
        if confirmation and confirmation.error_message
        else (
            confirmation.recipient
            if confirmation
            else "No confirmation delivery has been recorded."
        )
    )
    confirmation_at = (
        confirmation.sent_at
        if confirmation and confirmation.sent_at
        else (confirmation.updated_at if confirmation else None)
    )
    delivery = [
        _delivery_checkpoint(
            "Slack notification",
            notification_status,
            notification_detail,
            notification_at,
        ),
        _delivery_checkpoint(
            "Confirmation email",
            confirmation.status if confirmation else "not_attempted",
            confirmation_detail,
            confirmation_at,
        ),
        _delivery_checkpoint(
            "CRM handoff",
            crm.status if crm else "not_synchronized",
            crm.last_error if crm and crm.last_error else (
                crm.provider if crm else "No CRM write has been requested yet."
            ),
            crm.completed_at if crm and crm.completed_at else (crm.updated_at if crm else None),
        ),
    ]
    signal_events = [
        event for event in events if event.stage in {"manual_progress", "gmail_message"}
    ]
    last_signal_at = signal_events[-1].created_at if signal_events else row.received_at
    # SQLite returns naive datetimes even for timezone-aware mapped columns.
    # Treat persisted naive values as UTC, which is the application's storage
    # convention, rather than allowing the read-only insight to fail.
    freshness_observed_at = (
        last_signal_at.replace(tzinfo=UTC)
        if last_signal_at.tzinfo is None
        else last_signal_at
    )
    freshness_hours = max(
        0, int((datetime.now(UTC) - freshness_observed_at).total_seconds() // 3600)
    )
    next_action = (
        current_route.action_plan.get("immediate_action")
        if current_route else None
    ) or "Review the source evidence and assign an owner."
    return LeadMomentumResponse(
        score=score,
        classification=classification,
        state=state,
        score_delta=score_delta,
        previous_score=previous_score,
        confidence=float(qualification.confidence) if qualification else None,
        freshness_hours=freshness_hours,
        observed_signals=len(signal_events),
        last_signal_at=last_signal_at,
        next_human_action=next_action,
        evidence=evidence[:8],
        delivery=delivery,
    )


@router.post("/{lead_id}/progress", response_model=LeadProgressResponse)
def update_lead_progress(
    lead_id: UUID,
    request: LeadProgressUpdate,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(policy_dependency),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
    slack_resolver: WorkspaceSlackNotificationResolver = Depends(
        get_workspace_slack_notification_resolver
    ),
) -> LeadProgressResponse:
    row = session.scalar(
        select(LeadORM).where(
            LeadORM.id == lead_id, LeadORM.workspace_id == workspace.workspace_id
        ).with_for_update()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    assert_lead_action(session, workspace, row, "edit")

    if (
        row.outcome and row.outcome.pipeline_stage in {"closed_won", "closed_lost"}
        and request.stage not in {"converted", "lost"}
    ):
        raise HTTPException(
            status_code=409,
            detail="This lead is closed. Use a closing stage to correct its recorded outcome.",
        )

    now = datetime.now(UTC)
    progress_event = PipelineEventORM(
        id=uuid4(),
        workspace_id=workspace.workspace_id,
        lead_id=row.id,
        stage="manual_progress",
        status=request.stage,
        details={"stage": request.stage, "note": request.note, "updated_by": workspace.actor_id},
        created_at=now,
    )
    session.add(progress_event)
    session.flush()
    if request.stage in {"converted", "lost"}:
        try:
            outcome = record_outcome(
                session, row, stage=request.stage, conversion_type=request.conversion_type,
                revenue=request.closed_revenue, credited_user_id=request.credited_user_id,
                send_direct_message=request.send_direct_message,
            )
        except ValueError as error:
            session.rollback()
            raise HTTPException(status_code=422, detail=str(error)) from error
        progress_event.details = {
            **progress_event.details, "conversion_type": outcome.conversion_type,
            "closed_revenue": (
                float(outcome.closed_revenue) if outcome.closed_revenue is not None else None
            ),
            "credited_user_id": str(outcome.credited_user_id) if outcome.credited_user_id else None,
        }
    classification, score, rationale = _reanalyze_lead_with_history(
        session,
        row,
        workspace.workspace_id,
        policies,
        notification_provider,
        trigger="manual_progress_update",
        route_after_reanalysis=request.stage not in {"converted", "lost"},
    )
    progress_event.details = {
        **progress_event.details,
        "qualification": {"classification": classification, "score": score},
    }
    session.commit()
    if request.stage == "converted":
        send_win_notifications(session, row, slack_resolver, workspace.actor_id)
    return LeadProgressResponse(
        stage=request.stage,
        note=request.note,
        classification=classification,
        score=score,
        rationale=rationale,
        updated_at=now,
        outcome=outcome_response(
            row.outcome,
            include_financial="fields.financial.view" in workspace.permissions,
        ),
    )


@router.post("/{lead_id}/congratulations/retry", response_model=LeadOutcomeResponse)
def retry_win_notifications(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
    slack_resolver: WorkspaceSlackNotificationResolver = Depends(
        get_workspace_slack_notification_resolver
    ),
) -> LeadOutcomeResponse:
    row = get_lead(session, lead_id, workspace.workspace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    assert_lead_action(session, workspace, row, "edit")
    if row.outcome is None or row.outcome.pipeline_stage != "closed_won":
        raise HTTPException(
            status_code=409, detail="Only a closed-won lead has congratulations to retry."
        )
    send_win_notifications(session, row, slack_resolver, workspace.actor_id, retry=True)
    return outcome_response(
        row.outcome,
        include_financial="fields.financial.view" in workspace.permissions,
    )


def _reanalyze_lead_with_history(
    session: Session,
    row: LeadORM,
    workspace_id: UUID,
    policies: tuple[IcpConfig, RoutingConfig],
    notification_provider: NotificationProvider | None,
    *,
    trigger: str,
    route_after_reanalysis: bool = True,
) -> tuple[str, int, list[str]]:
    progress_context, recorded_evidence = lead_history_context(
        session,
        workspace_id=workspace_id,
        lead_id=row.id,
    )
    payload = {
        "source": row.source,
        "source_detail": row.source_detail,
        "external_id": row.external_id,
        "full_name": row.full_name,
        "email": row.email,
        "phone": row.phone,
        "job_title": row.job_title,
        "company_name": row.company_name,
        "industry": row.industry,
        "employee_count": row.employee_count,
        "annual_revenue": row.annual_revenue,
        "geography": row.geography,
        "budget": row.budget,
        "product_or_service": row.product_or_service,
        "message": f"{row.message}\n\nVerified lead history (oldest first):\n{progress_context}",
        "received_at": row.received_at,
        "raw_payload": row.raw_payload,
        "normalization_version": row.normalization_version,
    }
    normalized = replace(
        normalize_lead(payload, row.id),
        original_message=row.message,
        recorded_evidence=recorded_evidence,
    )
    resolved_icp = resolve_workspace_icp(session, workspace_id, policies[0])
    reasoning_provider = resolve_workspace_reasoning_provider(session, workspace_id)
    analysis, result = _analyze_and_qualify(
        normalized,
        resolved_icp.configuration,
        policies[1],
        reasoning_provider,
        business_profile=operations_policy(session, workspace_id).business_profile,
        icp_enabled=resolved_icp.source == "workspace",
        session=session,
        workspace_id=workspace_id,
    )
    result = resolved_icp.trace_result(result)
    previous = {
        "classification": row.qualification.classification,
        "score": row.qualification.total_score,
    } if row.qualification else None
    update_lead_with_result(session, row, normalized, result, analysis, preserve_inquiry=True)
    record_pipeline_event(
        session,
        row.id,
        "qualification_rechecked",
        analysis.status,
        {
            "trigger": trigger,
            "previous_qualification": previous,
            "classification": result.classification,
            "score": result.total_score,
            "rationale": list(result.rationale),
            "analysis_provider": analysis.provider,
        },
    )
    if route_after_reanalysis:
        route_and_notify(
            session,
            row,
            policies[1],
            notification_provider=notification_provider,
            force=True,
            send_notification=False,
            reroute_reason="Lead history changed; qualification was recalculated.",
        )
    return result.classification, result.total_score, list(result.rationale)


@router.post("/{lead_id}/reevaluate", response_model=LeadResponse)
def reevaluate_lead(
    lead_id: UUID,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(policy_dependency),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> LeadResponse:
    row = get_lead(session, lead_id, workspace.workspace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    assert_lead_action(session, workspace, row, "edit")
    try:
        _reanalyze_lead_with_history(
            session,
            row,
            workspace.workspace_id,
            policies,
            notification_provider,
            trigger="manual_assistant_reevaluation",
        )
    except ReasoningProviderError as error:
        session.rollback()
        raise HTTPException(
            status_code=503,
            detail=(
                "Qualification could not be re-evaluated. Check the workspace reasoning "
                "provider and retry."
            ),
        ) from error
    session.commit()
    routing = get_current_routing(
        session, row.id, workspace_id=workspace.workspace_id
    )
    return lead_response(
        row,
        routing,
        get_current_crm_sync(session, row.id, workspace_id=workspace.workspace_id),
        _current_confirmation_email(session, row.id, workspace.workspace_id),
        lead_manual_review_reasons(session, row, routing),
        permissions=workspace.permissions,
    )


@router.post(
    "/{lead_id}/gmail-conversation/sync",
    response_model=GmailConversationSyncResponse,
)
def sync_lead_gmail_conversation(
    lead_id: UUID,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(policy_dependency),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    resolver: WorkspaceInboundCrmProviderResolver = Depends(get_workspace_inbound_crm_resolver),
    workspace: WorkspaceContext = Depends(
        require_permissions("leads.edit", "mailbox.sync_own")
    ),
) -> GmailConversationSyncResponse:
    row = session.scalar(
        select(LeadORM).where(LeadORM.id == lead_id, LeadORM.workspace_id == workspace.workspace_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    assert_lead_action(session, workspace, row, "edit")
    connections = list(
        session.scalars(
            select(ProviderConnectionORM).where(
                ProviderConnectionORM.workspace_id == workspace.workspace_id,
                ProviderConnectionORM.provider_type == "gmail",
                ProviderConnectionORM.is_active.is_(True),
                ProviderConnectionORM.status == "verified",
            )
        )
    )
    if not connections:
        raise HTTPException(
            status_code=409,
            detail="Connect and verify Gmail before syncing conversations.",
        )
    connections = [connection for connection in connections if can_use_mailbox(workspace, connection)]
    if not connections:
        raise HTTPException(403, "No connected Gmail mailbox is available to this workspace member.")

    existing_events = list(
        session.scalars(
            select(PipelineEventORM).where(
                PipelineEventORM.lead_id == lead_id,
                PipelineEventORM.workspace_id == workspace.workspace_id,
                PipelineEventORM.stage == "gmail_message",
            )
        )
    )
    known_message_ids_by_connection: set[tuple[str, str]] = set()
    legacy_known_message_ids: set[str] = set()
    for event in existing_events:
        details = event.details if isinstance(event.details, dict) else {}
        message_id = str(details.get("message_id") or "")
        if not message_id:
            continue
        connection_id = str(details.get("gmail_connection_id") or "")
        if connection_id:
            known_message_ids_by_connection.add((connection_id, message_id))
        else:
            legacy_known_message_ids.add(message_id)
    checked_threads: set[tuple[str, str]] = set()
    automation_threads: list[
        tuple[ProviderConnectionORM, list[dict[str, str]]]
    ] = []
    messages_added = 0
    stated_company: str | None = None
    stated_industry: str | None = None
    threads_unreadable = 0
    truncated = False
    try:
        for connection in connections:
            provider = resolver.resolve(
                session, workspace.workspace_id, connection.id, actor_id=workspace.actor_id
            )
            if not hasattr(provider, "list_contact_threads"):
                continue
            link_rows = list(
                session.scalars(
                    select(CrmProviderRecordLinkORM).where(
                        CrmProviderRecordLinkORM.workspace_id == workspace.workspace_id,
                        CrmProviderRecordLinkORM.connection_id == connection.id,
                        CrmProviderRecordLinkORM.lead_id == lead_id,
                        CrmProviderRecordLinkORM.provider == "gmail",
                        CrmProviderRecordLinkORM.object_type == "message",
                        CrmProviderRecordLinkORM.status == "active",
                    )
                )
            )
            linked_threads = {
                str(link.provider_metadata.get("gmail_thread_id"))
                for link in link_rows
                if link.provider_metadata.get("gmail_thread_id")
            }
            found_threads = set(provider.list_contact_threads(row.email, max_threads=200))
            thread_ids = linked_threads | found_threads
            truncated = truncated or len(thread_ids) >= 200
            for thread_id in thread_ids:
                thread_key = (str(connection.id), thread_id)
                if thread_key in checked_threads:
                    continue
                checked_threads.add(thread_key)
                try:
                    thread_messages = provider.fetch_thread(
                        thread_id, lead_email=row.email
                    )
                except ProviderOperationError as error:
                    if error.code != "gmail_thread_access_denied":
                        raise
                    threads_unreadable += 1
                    continue
                messages_added += append_gmail_thread_messages(
                    session,
                    workspace_id=workspace.workspace_id,
                    lead_id=lead_id,
                    connection_id=connection.id,
                    messages=thread_messages,
                    known_message_ids_by_connection=known_message_ids_by_connection,
                    legacy_known_message_ids=legacy_known_message_ids,
                    existing_events=existing_events,
                )
                if thread_messages:
                    automation_threads.append((connection, thread_messages))
                    company, industry = extract_stated_gmail_profile(thread_messages, row.email)
                    stated_company = stated_company or company
                    stated_industry = stated_industry or industry
    except ProviderOperationError as error:
        session.rollback()
        raise HTTPException(
            status_code=503 if error.retryable else 502,
            detail=str(error),
        ) from error

    profile_updated_fields: list[str] = []
    current_company = (row.company_name or "").strip().lower()
    if stated_company and current_company in {"", "unknown", "unknown company"}:
        row.company_name = stated_company
        profile_updated_fields.append("company name")
    if stated_industry and not (row.industry or "").strip():
        row.industry = stated_industry
        profile_updated_fields.append("industry")

    if messages_added or profile_updated_fields:
        session.flush()
        classification, score, _rationale = _reanalyze_lead_with_history(
            session, row, workspace.workspace_id, policies, notification_provider,
            trigger="gmail_conversation_sync",
        )
    else:
        classification = (
            row.qualification.classification
            if row.qualification
            else "needs_qualifying"
        )
        score = row.qualification.total_score if row.qualification else 0
    session.commit()
    from lead_agent.application.meeting_email import process_gmail_meeting_thread

    for connection, thread_messages in automation_threads:
        try:
            process_gmail_meeting_thread(
                session,
                workspace_id=workspace.workspace_id,
                lead=row,
                gmail_connection=connection,
                messages=thread_messages,
                actor_id=workspace.actor_id,
            )
        except Exception:
            _LOGGER.exception(
                "meeting_email.manual_gmail_sync_failed",
                extra={
                    "fields": {
                        "workspace_id": str(workspace.workspace_id),
                        "lead_id": str(row.id),
                        "connection_id": str(connection.id),
                    }
                },
            )
    return GmailConversationSyncResponse(
        messages_added=messages_added,
        profile_updated_fields=profile_updated_fields,
        threads_checked=len(checked_threads),
        threads_unreadable=threads_unreadable,
        truncated=truncated,
        classification=classification,
        score=score,
    )


@router.post(
    "/{lead_id}/meeting-booking/retry",
    response_model=RetryConfirmedMeetingBookingResponse,
)
def retry_saved_meeting_booking(
    lead_id: UUID,
    request: RetryConfirmedMeetingBookingRequest,
    session: Session = Depends(get_db),
    resolver: WorkspaceInboundCrmProviderResolver = Depends(get_workspace_inbound_crm_resolver),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> RetryConfirmedMeetingBookingResponse:
    lead = session.scalar(
        select(LeadORM)
        .where(
            LeadORM.id == lead_id,
            LeadORM.workspace_id == workspace.workspace_id,
        )
        .with_for_update()
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found.")
    assert_lead_action(session, workspace, lead, "edit")

    offers = list(
        session.scalars(
            select(PipelineEventORM)
            .where(
                PipelineEventORM.workspace_id == workspace.workspace_id,
                PipelineEventORM.lead_id == lead_id,
                PipelineEventORM.stage == "meeting_email_offer",
                PipelineEventORM.status == "needs_attention",
            )
            .order_by(PipelineEventORM.created_at.desc())
            .limit(50)
        )
    )
    offer = next(
        (
            event
            for event in offers
            if isinstance(event.details, dict)
            and event.details.get("source_message_id")
            == request.offer_source_message_id
        ),
        None,
    )
    if offer is None or not isinstance(offer.details, dict):
        raise HTTPException(
            status_code=409,
            detail="No unresolved meeting booking was found for this Gmail request.",
        )

    details = offer.details
    options = details.get("options")
    selected_option = details.get("selected_option")
    thread_id = str(details.get("thread_id") or "")
    reply_message_id = str(details.get("reply_message_id") or "")
    if (
        details.get("lead_requested_other_times")
        or not isinstance(options, list)
        or not isinstance(selected_option, int)
        or isinstance(selected_option, bool)
        or not 1 <= selected_option <= len(options)
        or not thread_id
        or not reply_message_id
    ):
        raise HTTPException(
            status_code=409,
            detail="There is no saved, confirmed numbered choice to retry.",
        )

    try:
        gmail_connection_id = UUID(str(details.get("gmail_connection_id") or ""))
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail="The Gmail connection for this meeting request is unavailable.",
        ) from error
    gmail_connection = session.scalar(
        select(ProviderConnectionORM).where(
            ProviderConnectionORM.id == gmail_connection_id,
            ProviderConnectionORM.workspace_id == workspace.workspace_id,
            ProviderConnectionORM.provider_type == "gmail",
            ProviderConnectionORM.is_active.is_(True),
            ProviderConnectionORM.status == "verified",
        )
    )
    if gmail_connection is None:
        raise HTTPException(
            status_code=409,
            detail="Reconnect and verify the Gmail account used for this conversation.",
        )
    assert_connection_action(session, workspace, gmail_connection, "use")

    try:
        provider = resolver.resolve(
            session,
            workspace.workspace_id,
            gmail_connection.id,
            actor_id=workspace.actor_id,
        )
        if not hasattr(provider, "fetch_thread"):
            raise HTTPException(
                status_code=502,
                detail="The connected Gmail account cannot read this conversation.",
            )
        messages = provider.fetch_thread(thread_id, lead_email=lead.email)
    except ProviderOperationError as error:
        raise HTTPException(
            status_code=503 if error.retryable else 502,
            detail=str(error),
        ) from error
    for message in messages:
        message["gmail_connection_id"] = str(gmail_connection.id)

    from lead_agent.application.meeting_email import process_gmail_meeting_thread

    result = process_gmail_meeting_thread(
        session,
        workspace_id=workspace.workspace_id,
        lead=lead,
        gmail_connection=gmail_connection,
        messages=messages,
        actor_id=workspace.actor_id,
    )
    session.commit()
    updated_details = offer.details if isinstance(offer.details, dict) else {}
    booking_error = (
        str(updated_details["booking_error"])
        if updated_details.get("booking_error")
        else None
    )
    if offer.status == "booked" or result == "booked":
        message = "The saved choice was booked or an existing calendar event was recorded."
    elif booking_error:
        message = f"The booking retry did not complete: {booking_error}"
    elif result == "needs_attention":
        message = (
            "No booking was made. Corral could not verify that the saved numbered "
            "reply is still the lead’s latest choice."
        )
    else:
        message = f"Booking retry result: {result}."
    return RetryConfirmedMeetingBookingResponse(
        status=offer.status,
        message=message,
        booking_error=booking_error,
    )
