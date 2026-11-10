from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from io import BytesIO, StringIO
from typing import Any, Literal
from uuid import UUID, uuid4

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from pydantic import BaseModel, ConfigDict, Field, model_validator
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import (
    CrmSyncORM,
    LeadExportAuditEventORM,
    LeadORM,
    QualificationResultORM,
    RoutingDecisionORM,
)

ExportFormat = Literal["csv", "xlsx", "pdf"]
ExportScope = Literal["current_filters", "selected", "all"]
ExportColumn = Literal[
    "lead_name",
    "company",
    "email",
    "source",
    "received_date",
    "service_interest",
    "icp_score",
    "intent_score",
    "engagement_score",
    "total_score",
    "qualification_band",
    "icp_version",
    "routing_queue",
    "assigned_representative",
    "priority",
    "crm_provider",
    "crm_sync_state",
    "provider_record_id",
    "opportunity_value",
    "revenue_value",
    "summary",
    "recommended_action",
    "phone",
    "job_title",
    "industry",
    "employee_count",
    "annual_revenue",
    "geography",
    "budget",
    "source_detail",
    "inquiry_message",
]
DEFAULT_EXPORT_COLUMNS: tuple[ExportColumn, ...] = (
    "lead_name",
    "company",
    "email",
    "source",
    "received_date",
    "service_interest",
    "icp_score",
    "intent_score",
    "engagement_score",
    "total_score",
    "qualification_band",
    "icp_version",
    "routing_queue",
    "assigned_representative",
    "priority",
    "crm_provider",
    "crm_sync_state",
    "provider_record_id",
    "opportunity_value",
    "revenue_value",
    "summary",
    "recommended_action",
)
EXPORT_COLUMNS: tuple[ExportColumn, ...] = (
    *DEFAULT_EXPORT_COLUMNS,
    "phone",
    "job_title",
    "industry",
    "employee_count",
    "annual_revenue",
    "geography",
    "budget",
    "source_detail",
    "inquiry_message",
)

EXPORT_COLUMN_PERMISSIONS: dict[ExportColumn, str] = {
    "email": "fields.contact.view",
    "phone": "fields.contact.view",
    "opportunity_value": "fields.financial.view",
    "revenue_value": "fields.financial.view",
    "annual_revenue": "fields.financial.view",
    "budget": "fields.financial.view",
    "summary": "fields.communications.view",
    "recommended_action": "fields.internal.view",
    "inquiry_message": "fields.communications.view",
}


def allowed_export_columns(
    columns: list[ExportColumn] | None,
    permissions: frozenset[str] | set[str],
) -> list[ExportColumn]:
    """Return only export columns whose data class the actor may view."""
    selected = _resolve_export_columns(columns)
    return [
        column
        for column in selected
        if (required := EXPORT_COLUMN_PERMISSIONS.get(column)) is None
        or required in permissions
    ]


class LeadExportFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search: str = Field(default="", max_length=200)
    source: str | None = Field(default=None, max_length=64)
    qualification_bands: list[str] = Field(default_factory=list, max_length=20)
    assigned_representative: str | None = Field(default=None, max_length=255)
    routing_queue: str | None = Field(default=None, max_length=128)
    crm_state: str | None = Field(default=None, max_length=32)
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def validate_date_range(self) -> LeadExportFilters:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class LeadExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: ExportFormat
    scope: ExportScope = "current_filters"
    filters: LeadExportFilters = Field(default_factory=LeadExportFilters)
    selected_lead_ids: list[UUID] = Field(default_factory=list, max_length=5000)
    columns: list[ExportColumn] = Field(
        default_factory=lambda: list(DEFAULT_EXPORT_COLUMNS),
        max_length=len(EXPORT_COLUMNS),
    )

    @model_validator(mode="after")
    def validate_scope(self) -> LeadExportRequest:
        if not self.columns:
            raise ValueError("At least one export column must be selected")
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("Export columns must be unique")
        if self.scope == "selected" and not self.selected_lead_ids:
            raise ValueError("selected scope requires at least one lead")
        if self.scope != "selected" and self.selected_lead_ids:
            raise ValueError("selected_lead_ids are only valid for selected scope")
        return self


@dataclass(frozen=True)
class ExportLeadRecord:
    lead_name: str
    company: str
    email: str
    source: str
    received_date: str
    service_interest: str
    icp_score: int | None
    intent_score: int | None
    engagement_score: int | None
    total_score: int | None
    qualification_band: str
    icp_version: str
    routing_queue: str
    assigned_representative: str
    priority: str
    crm_provider: str
    crm_sync_state: str
    provider_record_id: str
    opportunity_value: float | None
    revenue_value: float | None
    summary: str
    recommended_action: str
    phone: str = ""
    job_title: str = ""
    industry: str = ""
    employee_count: int | None = None
    annual_revenue: float | None = None
    geography: str = ""
    budget: float | None = None
    source_detail: str = ""
    inquiry_message: str = ""

    @classmethod
    def headings(cls) -> list[str]:
        return [
            "Lead / Contact Name",
            "Company",
            "Email",
            "Source",
            "Received Date",
            "Service / Interest",
            "ICP Score",
            "Intent Score",
            "Engagement Score",
            "Total Score",
            "Qualification Band",
            "ICP Version",
            "Routing Queue",
            "Assigned Representative",
            "Priority / Urgency",
            "CRM Provider",
            "CRM Sync State",
            "Provider Record ID",
            "Opportunity Value",
            "Revenue Value",
            "AI / Grounded Summary",
            "Recommended Action / Action Plan",
            "Phone",
            "Job Title",
            "Industry",
            "Employee Count",
            "Company Annual Revenue",
            "Geography",
            "Budget",
            "Source Detail",
            "Original Inquiry",
        ]

    def values(self) -> list[Any]:
        return [
            self.lead_name,
            self.company,
            self.email,
            self.source,
            self.received_date,
            self.service_interest,
            self.icp_score,
            self.intent_score,
            self.engagement_score,
            self.total_score,
            self.qualification_band,
            self.icp_version,
            self.routing_queue,
            self.assigned_representative,
            self.priority,
            self.crm_provider,
            self.crm_sync_state,
            self.provider_record_id,
            self.opportunity_value,
            self.revenue_value,
            self.summary,
            self.recommended_action,
            self.phone,
            self.job_title,
            self.industry,
            self.employee_count,
            self.annual_revenue,
            self.geography,
            self.budget,
            self.source_detail,
            self.inquiry_message,
        ]


class ExportEmptyError(ValueError):
    pass


class ExportLimitError(ValueError):
    pass


def _effective_filters(request: LeadExportRequest) -> LeadExportFilters:
    return request.filters if request.scope == "current_filters" else LeadExportFilters()


def _label(value: str | None, empty: str = "Not available") -> str:
    if not value:
        return empty
    words = re.sub(r"[_-]+", " ", value).strip().split()
    return " ".join(word.capitalize() for word in words) if words else empty


def _current_routing(row: LeadORM, workspace_id: UUID) -> RoutingDecisionORM | None:
    return next(
        (
            item
            for item in row.routing_decisions
            if item.workspace_id == workspace_id and item.is_current
        ),
        None,
    )


def _current_crm(row: LeadORM, workspace_id: UUID) -> CrmSyncORM | None:
    return next(
        (item for item in row.crm_syncs if item.workspace_id == workspace_id and item.is_current),
        None,
    )


def list_export_leads(
    session: Session,
    workspace_id: UUID,
    request: LeadExportRequest,
    *,
    limit: int | None = None,
    permissions: frozenset[str] | set[str] | None = None,
) -> list[LeadORM]:
    """Query the export dataset with the same workspace and current-state semantics as Leads."""
    filters = request.filters
    statement = (
        select(LeadORM)
        .options(
            joinedload(LeadORM.qualification),
            joinedload(LeadORM.analysis),
            selectinload(LeadORM.routing_decisions),
            selectinload(LeadORM.crm_syncs),
            joinedload(LeadORM.outcome),
        )
        .where(LeadORM.workspace_id == workspace_id)
        .order_by(LeadORM.created_at.desc())
    )
    if request.scope == "selected":
        statement = statement.where(LeadORM.id.in_(request.selected_lead_ids))
    apply_filters = request.scope == "current_filters"
    if apply_filters and filters.search.strip():
        term = f"%{filters.search.strip()}%"
        search_fields = [
            LeadORM.full_name.ilike(term),
            LeadORM.company_name.ilike(term),
            LeadORM.product_or_service.ilike(term),
        ]
        if permissions is None or "fields.contact.view" in permissions:
            search_fields.append(LeadORM.email.ilike(term))
        statement = statement.where(or_(*search_fields))
    if apply_filters and filters.source:
        statement = statement.where(LeadORM.source == filters.source)
    if apply_filters and filters.qualification_bands:
        statement = statement.where(
            LeadORM.qualification.has(
                QualificationResultORM.classification.in_(filters.qualification_bands)
            )
        )
    if apply_filters and filters.assigned_representative:
        statement = statement.where(
            LeadORM.routing_decisions.any(
                (RoutingDecisionORM.workspace_id == workspace_id)
                & RoutingDecisionORM.is_current.is_(True)
                & RoutingDecisionORM.assigned_rep_name.ilike(filters.assigned_representative)
            )
        )
    if apply_filters and filters.routing_queue:
        statement = statement.where(
            LeadORM.routing_decisions.any(
                (RoutingDecisionORM.workspace_id == workspace_id)
                & RoutingDecisionORM.is_current.is_(True)
                & (RoutingDecisionORM.assigned_queue == filters.routing_queue)
            )
        )
    if apply_filters and filters.crm_state:
        statement = statement.where(
            LeadORM.crm_syncs.any(
                (CrmSyncORM.workspace_id == workspace_id)
                & CrmSyncORM.is_current.is_(True)
                & (CrmSyncORM.status == filters.crm_state)
            )
        )
    if apply_filters and filters.date_from:
        statement = statement.where(
            LeadORM.received_at >= datetime.combine(filters.date_from, time.min, tzinfo=UTC)
        )
    if apply_filters and filters.date_to:
        end = datetime.combine(filters.date_to, time.max, tzinfo=UTC)
        statement = statement.where(LeadORM.received_at <= end)
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.scalars(statement).unique())


def canonical_records(rows: list[LeadORM], workspace_id: UUID) -> list[ExportLeadRecord]:
    records: list[ExportLeadRecord] = []
    for row in rows:
        qualification = row.qualification
        routing = _current_routing(row, workspace_id)
        crm = _current_crm(row, workspace_id)
        components = qualification.component_scores if qualification else {}
        action_plan = routing.action_plan if routing else {}
        analysis_summary = row.analysis.structured_output.get("summary", {}) if row.analysis else {}
        summary_parts = [
            str(analysis_summary.get(key, "")).strip()
            for key in ("who", "company_context", "need", "buying_signal")
            if analysis_summary.get(key)
        ]
        provider_ids = crm.provider_entity_ids if crm else {}
        outcome = row.outcome if row.outcome and row.outcome.workspace_id == workspace_id else None
        records.append(
            ExportLeadRecord(
                lead_name=row.full_name,
                company=row.company_name,
                email=row.email,
                source=_label(row.source),
                received_date=row.received_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC"),
                service_interest=row.product_or_service,
                icp_score=components.get("icp_fit"),
                intent_score=components.get("intent_urgency"),
                engagement_score=components.get("engagement_quality"),
                total_score=qualification.total_score if qualification else None,
                qualification_band=_label(qualification.classification if qualification else None),
                icp_version=qualification.icp_profile_version if qualification else "Not available",
                routing_queue=_label(qualification.routing_queue if qualification else None),
                assigned_representative=routing.assigned_rep_name
                if routing and routing.assigned_rep_name
                else "Unassigned",
                priority=_label(routing.priority if routing else None),
                crm_provider=_label(crm.provider if crm else None),
                crm_sync_state=_label(crm.status if crm else None, "Not synced"),
                provider_record_id=str(
                    next((value for value in provider_ids.values() if value), "Not available")
                ),
                opportunity_value=float(outcome.opportunity_value)
                if outcome and outcome.opportunity_value is not None
                else None,
                revenue_value=float(outcome.closed_revenue)
                if outcome and outcome.closed_revenue is not None
                else None,
                summary=" ".join(summary_parts) or "Not available",
                recommended_action=str(action_plan.get("immediate_action", "Review lead")),
                phone=row.phone or "",
                job_title=row.job_title or "",
                industry=row.industry or "",
                employee_count=row.employee_count,
                annual_revenue=float(row.annual_revenue)
                if row.annual_revenue is not None
                else None,
                geography=row.geography or "",
                budget=float(row.budget) if row.budget is not None else None,
                source_detail=row.source_detail or "",
                inquiry_message=row.message or "",
            )
        )
    return records


def _csv_safe(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip(" \t\r\n\ufeff")[:1] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


def _resolve_export_columns(columns: list[ExportColumn] | None) -> list[ExportColumn]:
    selected = list(DEFAULT_EXPORT_COLUMNS if columns is None else columns)
    if not selected:
        raise ValueError("At least one export column must be selected")
    if len(selected) != len(set(selected)):
        raise ValueError("Export columns must be unique")
    if any(column not in EXPORT_COLUMNS for column in selected):
        raise ValueError("The export contains an unknown column")
    return selected


def render_csv(
    records: list[ExportLeadRecord],
    columns: list[ExportColumn] | None = None,
) -> bytes:
    selected = _resolve_export_columns(columns)
    indexes = [EXPORT_COLUMNS.index(column) for column in selected]
    headings = ExportLeadRecord.headings()
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow([headings[index] for index in indexes])
    for record in records:
        values = record.values()
        writer.writerow([_csv_safe(values[index]) for index in indexes])
    return output.getvalue().encode("utf-8-sig")


def render_xlsx(
    records: list[ExportLeadRecord],
    columns: list[ExportColumn] | None = None,
) -> bytes:
    selected = _resolve_export_columns(columns)
    indexes = [EXPORT_COLUMNS.index(column) for column in selected]
    headings = ExportLeadRecord.headings()
    workbook = Workbook()
    worksheet: Worksheet = workbook.worksheets[0]
    worksheet.title = "Leads"
    worksheet.append([headings[index] for index in indexes])
    for cell in worksheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F2933")
    for record in records:
        values = record.values()
        worksheet.append([_csv_safe(values[index]) for index in indexes])
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    legacy_widths = [
        22,
        22,
        28,
        15,
        22,
        24,
        12,
        12,
        15,
        12,
        20,
        16,
        20,
        22,
        17,
        16,
        16,
        20,
        18,
        15,
        48,
        42,
    ]
    width_by_column = dict(zip(DEFAULT_EXPORT_COLUMNS, legacy_widths, strict=True))
    width_by_column.update({
        "phone": 18,
        "job_title": 22,
        "industry": 20,
        "employee_count": 16,
        "annual_revenue": 22,
        "geography": 22,
        "budget": 18,
        "source_detail": 24,
        "inquiry_message": 48,
    })
    for index, column in enumerate(selected, start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width_by_column[column]
    for row in worksheet.iter_rows(min_row=2):
        for index, column in enumerate(selected):
            if column in {
                "icp_score",
                "intent_score",
                "engagement_score",
                "total_score",
                "employee_count",
            }:
                row[index].number_format = "0"
            elif column in {"opportunity_value", "revenue_value", "annual_revenue", "budget"}:
                row[index].number_format = "#,##0.00"
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _paragraph(value: Any, style: ParagraphStyle) -> Paragraph:
    escaped = (
        str(value if value is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )
    return Paragraph(escaped, style)


def render_pdf(
    records: list[ExportLeadRecord],
    workspace_name: str,
    actor_name: str,
    filters: LeadExportFilters,
    columns: list[ExportColumn] | None = None,
) -> bytes:
    selected = _resolve_export_columns(columns)
    indexes = [EXPORT_COLUMNS.index(column) for column in selected]
    output = BytesIO()
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "export-body", parent=styles["BodyText"], fontName="Helvetica", fontSize=6.5, leading=8
    )
    header = ParagraphStyle(
        "export-header", parent=body, fontName="Helvetica-Bold", textColor=colors.white, fontSize=6
    )
    title = ParagraphStyle(
        "export-title",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=20,
        textColor=colors.HexColor("#1f2933"),
    )
    document = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
    )
    story: list[Any] = [
        Paragraph(
            "INGENUITY SYSTEMS",
            ParagraphStyle(
                "brand",
                parent=body,
                fontName="Helvetica-Bold",
                textColor=colors.HexColor("#e13511"),
                fontSize=8,
            ),
        ),
        Paragraph("Lead export report", title),
        Paragraph(
            f"Workspace: {_html_text(workspace_name)} &nbsp; | &nbsp; "
            f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} "
            f"&nbsp; | &nbsp; Actor: {_html_text(actor_name)}",
            body,
        ),
        Spacer(1, 5 * mm),
    ]
    counts = Counter(record.qualification_band for record in records)
    summary: list[list[Any]] = [
        ["Total leads", "Hot", "Warm", "Needs Qualifying", "Nurture", "Disqualified"],
        [
            len(records),
            counts["Hot"],
            counts["Warm"],
            counts["Needs Qualifying"],
            counts["Nurture"],
            counts["Disqualified"],
        ],
    ]
    story.append(
        Table(
            summary,
            colWidths=[35 * mm] * 6,
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2933")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#ccd2d8")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#ccd2d8")),
                ]
            ),
        )
    )
    story.append(Spacer(1, 4 * mm))
    story.append(
        Paragraph(
            f"Applied filters: {_html_text(filter_description(filters))} "
            f"&nbsp; | &nbsp; {len(records)} leads",
            body,
        )
    )
    table_body = ParagraphStyle(
        "export-table-body",
        parent=body,
        fontSize=max(4.5, 6.5 - max(0, len(selected) - 10) * 0.1),
        leading=max(5.5, 8 - max(0, len(selected) - 10) * 0.12),
    )
    table_header = ParagraphStyle(
        "export-table-header",
        parent=header,
        fontSize=max(4, 6 - max(0, len(selected) - 10) * 0.08),
        leading=max(5, 7 - max(0, len(selected) - 10) * 0.1),
    )
    headings = ExportLeadRecord.headings()
    table_data: list[list[Any]] = [
        [_paragraph(headings[index], table_header) for index in indexes]
    ]
    for record in records:
        values = record.values()
        table_data.append([_paragraph(values[index], table_body) for index in indexes])
    weight_by_column = {
        "lead_name": 1.4,
        "company": 1.3,
        "email": 1.4,
        "summary": 1.7,
        "recommended_action": 1.7,
        "inquiry_message": 1.7,
        "source_detail": 1.3,
        "job_title": 1.2,
        "geography": 1.2,
    }
    weights = [weight_by_column.get(column, 1.0) for column in selected]
    available_width = landscape(A4)[0] - 20 * mm
    col_widths = [available_width * weight / sum(weights) for weight in weights]
    story.append(
        Table(
            table_data,
            repeatRows=1,
            colWidths=col_widths,
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2933")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#ccd2d8")),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f5f7f8")],
                    ),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            ),
        )
    )
    document.build(story)
    return output.getvalue()


def _html_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def filter_description(filters: LeadExportFilters) -> str:
    parts: list[str] = []
    if filters.search.strip():
        parts.append(f"Search: {filters.search.strip()}")
    if filters.source:
        parts.append(f"Source: {_label(filters.source)}")
    if filters.qualification_bands:
        parts.append(
            "Qualification: " + ", ".join(_label(value) for value in filters.qualification_bands)
        )
    if filters.assigned_representative:
        parts.append(f"Representative: {filters.assigned_representative}")
    if filters.routing_queue:
        parts.append(f"Queue: {_label(filters.routing_queue)}")
    if filters.crm_state:
        parts.append(f"CRM: {_label(filters.crm_state)}")
    if filters.date_from or filters.date_to:
        parts.append(f"Date: {filters.date_from or 'Any'} – {filters.date_to or 'Any'}")
    return " · ".join(parts) or "All permitted workspace leads"


def render_export(
    request: LeadExportRequest,
    records: list[ExportLeadRecord],
    workspace_name: str,
    actor_name: str,
) -> tuple[bytes, str, str]:
    if request.format == "csv":
        return render_csv(records, request.columns), "text/csv", "csv"
    if request.format == "xlsx":
        return (
            render_xlsx(records, request.columns),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xlsx",
        )
    return (
        render_pdf(
            records,
            workspace_name,
            actor_name,
            _effective_filters(request),
            request.columns,
        ),
        "application/pdf",
        "pdf",
    )


def write_export_audit(
    session: Session,
    workspace: WorkspaceContext,
    request: LeadExportRequest,
    *,
    outcome: str,
    record_count: int | None = None,
    error_code: str | None = None,
) -> None:
    session.add(
        LeadExportAuditEventORM(
            id=uuid4(),
            workspace_id=workspace.workspace_id,
            actor_id=workspace.user_id,
            format=request.format,
            record_count=record_count,
            selection_mode=request.scope,
            outcome=outcome,
            filter_summary={
                "description": filter_description(_effective_filters(request)),
                "selected_count": len(request.selected_lead_ids),
            },
            error_code=error_code,
            created_at=datetime.now(UTC),
        )
    )
    session.commit()
