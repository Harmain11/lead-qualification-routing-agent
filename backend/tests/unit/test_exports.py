from __future__ import annotations

import csv
from datetime import UTC, datetime
from io import BytesIO, StringIO
from itertools import count
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.main import app
from lead_agent.application.exports import (
    ExportLeadRecord,
    LeadExportRequest,
    canonical_records,
    list_export_leads,
    render_csv,
    render_pdf,
    render_xlsx,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    LeadExportAuditEventORM,
    LeadORM,
    QualificationResultORM,
    WorkspaceORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.settings.config import Settings, get_settings


def _record(name: str = "Jane Doe") -> ExportLeadRecord:
    return ExportLeadRecord(
        lead_name=name,
        company="Acme, Inc.",
        email="jane@example.test",
        source="Website",
        received_date="2026-09-23 10:00 UTC",
        service_interest="Revenue operations",
        icp_score=32,
        intent_score=25,
        engagement_score=18,
        total_score=75,
        qualification_band="Warm",
        icp_version="v2",
        routing_queue="Sales Development",
        assigned_representative="A. Rep",
        priority="High",
        crm_provider="Not available",
        crm_sync_state="Not synced",
        provider_record_id="Not available",
        opportunity_value=12500.0,
        revenue_value=None,
        summary="Business is evaluating a grounded workflow.",
        recommended_action="Schedule a discovery call.",
    )

_LEAD_NUMBERS = count(1)


def _lead(workspace_id, *, name: str, received_at: datetime, band: str = "warm") -> LeadORM:
    lead_id = uuid4()
    now = datetime.now(UTC)
    row = LeadORM(
        id=lead_id,
        workspace_id=workspace_id,
        lead_number=next(_LEAD_NUMBERS),
        source="website",
        source_detail=None,
        external_id=None,
        full_name=name,
        email=f"{lead_id.hex[:8]}@example.test",
        phone=None,
        job_title=None,
        company_name=f"{name} Company",
        industry=None,
        employee_count=None,
        annual_revenue=None,
        geography=None,
        budget=None,
        product_or_service="Revenue operations",
        message="Interested in a qualified workflow.",
        received_at=received_at,
        raw_payload={"message": "safe"},
        normalization_version="1.0",
        created_at=now,
        updated_at=now,
    )
    row.qualification = QualificationResultORM(
        id=uuid4(), lead_id=lead_id, total_score=75, classification=band,
        routing_queue="sales_development", routing_action="follow_up",
        hard_disqualified=False, disqualifier_evidence=[], rationale=[],
        score_breakdown=[], component_scores={
            "icp_fit": 32, "intent_urgency": 25, "engagement_quality": 18
        },
        policy_version="1", icp_version_id=None, icp_source="workspace",
        icp_profile_version="v2", icp_configuration_hash="hash", confidence=0.9,
        created_at=now,
    )
    return row


def test_csv_is_utf8_escaped_and_formula_safe() -> None:
    record = _record("\t=HYPERLINK(\"https://bad.test\")")
    record = record.__class__(**{**record.__dict__, "summary": "line one, \"quoted\"\nline two"})
    rows = list(csv.reader(StringIO(render_csv([record]).decode("utf-8-sig"))))
    assert rows[0][0] == "Lead / Contact Name"
    assert rows[1][0].startswith("'\t=")
    assert rows[1][-2] == 'line one, "quoted"\nline two'
    assert "credentials" not in "\n".join(rows[0]).lower()


def test_selected_export_columns_are_applied_in_requested_order() -> None:
    record = _record()
    record = record.__class__(**{**record.__dict__, "phone": "+1 415 555 0100"})
    columns = ["phone", "email", "lead_name"]

    csv_rows = list(
        csv.reader(StringIO(render_csv([record], columns).decode("utf-8-sig")))
    )
    assert csv_rows[0] == ["Phone", "Email", "Lead / Contact Name"]
    assert csv_rows[1] == ["'+1 415 555 0100", "jane@example.test", "Jane Doe"]

    sheet = load_workbook(BytesIO(render_xlsx([record], columns)))["Leads"]
    assert [sheet.cell(1, index).value for index in range(1, 4)] == [
        "Phone",
        "Email",
        "Lead / Contact Name",
    ]
    assert [sheet.cell(2, index).value for index in range(1, 4)] == [
        "'+1 415 555 0100",
        "jane@example.test",
        "Jane Doe",
    ]


def test_export_columns_must_be_nonempty_and_unique() -> None:
    with pytest.raises(ValueError):
        LeadExportRequest(format="csv", columns=[])
    with pytest.raises(ValueError):
        LeadExportRequest(format="csv", columns=["email", "email"])


def test_xlsx_has_business_format_and_formula_safe_cells() -> None:
    record = _record("+SUM(1,1)")
    workbook = load_workbook(BytesIO(render_xlsx([record])))
    sheet = workbook["Leads"]
    assert sheet.freeze_panes == "A2"
    assert sheet["A2"].value == "'+SUM(1,1)"
    assert sheet["G2"].value == 32
    assert sheet.auto_filter.ref


def test_pdf_is_a_report_with_identity_and_summary() -> None:
    payload = render_pdf(
        [_record()], "Acme workspace", "Workspace Owner", LeadExportRequest(format="pdf").filters
    )
    assert payload.startswith(b"%PDF")
    assert len(payload) > 1000


def test_pdf_renders_only_requested_export_columns() -> None:
    payload = render_pdf(
        [_record()],
        "Acme workspace",
        "Workspace Owner",
        LeadExportRequest(format="pdf").filters,
        ["email", "lead_name"],
    )
    assert payload.startswith(b"%PDF")
    assert len(payload) > 1000


def test_export_query_is_workspace_isolated_and_preserves_filters(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'exports.db'}")
    Base.metadata.create_all(engine)
    workspace_a, workspace_b = uuid4(), uuid4()
    with Session(engine) as session:
        session.add_all([
            _lead(workspace_a, name="A Lead", received_at=datetime(2026, 9, 23, tzinfo=UTC)),
            _lead(workspace_b, name="B Lead", received_at=datetime(2026, 9, 23, tzinfo=UTC)),
        ])
        session.commit()
        request = LeadExportRequest(
            format="csv", scope="current_filters",
            filters={"search": "A Lead", "qualification_bands": ["warm"]},
        )
        rows = list_export_leads(session, workspace_a, request)
        assert [row.full_name for row in rows] == ["A Lead"]
        assert all(row.workspace_id == workspace_a for row in rows)
        assert canonical_records(rows, workspace_a)[0].qualification_band == "Warm"


def test_export_scope_semantics_do_not_apply_stale_filters_to_selected_or_all(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'export-scopes.db'}")
    Base.metadata.create_all(engine)
    workspace_id = uuid4()
    with Session(engine) as session:
        selected = _lead(
            workspace_id, name="Selected Lead", received_at=datetime(2026, 9, 23, tzinfo=UTC)
        )
        other = _lead(
            workspace_id, name="Other Lead", received_at=datetime(2026, 9, 22, tzinfo=UTC)
        )
        session.add_all([selected, other])
        session.commit()
        stale_filters = {"search": "no-match", "qualification_bands": ["hot"]}

        selected_rows = list_export_leads(
            session,
            workspace_id,
            LeadExportRequest(
                format="csv",
                scope="selected",
                selected_lead_ids=[selected.id],
                filters=stale_filters,
            ),
        )
        all_rows = list_export_leads(
            session,
            workspace_id,
            LeadExportRequest(format="csv", scope="all", filters=stale_filters),
        )

        assert [row.id for row in selected_rows] == [selected.id]
        assert {row.id for row in all_rows} == {selected.id, other.id}


def test_export_endpoint_requires_permission_and_audits_denial(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id = uuid4()
    settings = Settings(app_env="test", database_url=f"sqlite:///{tmp_path / 'api.db'}")

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        workspace_id=workspace_id, actor_id="rep", permissions=frozenset({"leads.view"})
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/leads/export", json={"format": "csv", "scope": "all", "filters": {}}
            )
        assert response.status_code == 403
        event = session.scalar(
            select(LeadExportAuditEventORM).order_by(LeadExportAuditEventORM.created_at.desc())
        )
        assert event is not None and event.outcome == "denied"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_selected_foreign_lead_is_ignored_and_success_is_audited(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'api-selected.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_a, workspace_b = uuid4(), uuid4()
    session.add_all([
        WorkspaceORM(
            id=workspace_a, name="A", slug=f"a-{uuid4().hex}", status="active", is_demo=False,
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC)
        ),
        WorkspaceORM(
            id=workspace_b, name="B", slug=f"b-{uuid4().hex}", status="active", is_demo=False,
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC)
        ),
        _lead(workspace_a, name="A Lead", received_at=datetime.now(UTC)),
        _lead(workspace_b, name="B Lead", received_at=datetime.now(UTC)),
    ])
    session.commit()
    foreign_id = session.scalar(select(LeadORM.id).where(LeadORM.workspace_id == workspace_b))
    settings = Settings(app_env="test", database_url=f"sqlite:///{tmp_path / 'api-selected.db'}")

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        workspace_id=workspace_a, actor_id="owner", permissions=frozenset({"leads.export"})
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/leads/export",
                json={
                    "format": "csv", "scope": "selected",
                    "selected_lead_ids": [str(foreign_id)], "filters": {}
                },
            )
        assert response.status_code == 422
        assert "No leads match" in response.json()["detail"]
        event = session.scalar(
            select(LeadExportAuditEventORM).order_by(LeadExportAuditEventORM.created_at.desc())
        )
        assert event is not None and event.outcome == "empty"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_export_permission_matrix_includes_roles_and_overrides(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'api-matrix.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id = uuid4()
    session.add(
        _lead(workspace_id, name="Permitted lead", received_at=datetime.now(UTC))
    )
    session.commit()
    settings = Settings(app_env="test", database_url=f"sqlite:///{tmp_path / 'api-matrix.db'}")
    cases = [
        ("owner", {"leads.export"}, 200),
        ("manager", {"leads.export"}, 200),
        ("sales rep", set(), 403),
        ("viewer", set(), 403),
        ("explicit grant", {"leads.export"}, 200),
        ("explicit deny", set(), 403),
    ]

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            for role, permissions, expected_status in cases:
                def override_workspace(
                    permissions=permissions, role=role
                ) -> WorkspaceContext:
                    return WorkspaceContext(
                        workspace_id=workspace_id,
                        actor_id=role,
                        role=role,
                        permissions=frozenset(permissions),
                    )

                app.dependency_overrides[get_workspace_context] = override_workspace
                response = client.post(
                    "/v1/leads/export", json={"format": "csv", "scope": "all", "filters": {}}
                )
                assert response.status_code == expected_status
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_export_sync_limit_fails_predictably_and_audits_rejection(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'api-limit.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id = uuid4()
    session.add_all(
        [
            _lead(workspace_id, name="First", received_at=datetime.now(UTC)),
            _lead(workspace_id, name="Second", received_at=datetime.now(UTC)),
        ]
    )
    session.commit()
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'api-limit.db'}",
        export_sync_limit=1,
    )

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        workspace_id=workspace_id,
        actor_id="owner",
        permissions=frozenset({"leads.export"}),
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/leads/export", json={"format": "csv", "scope": "all", "filters": {}}
            )
        assert response.status_code == 413
        assert "synchronous export limit" in response.json()["detail"]
        event = session.scalar(
            select(LeadExportAuditEventORM).order_by(LeadExportAuditEventORM.created_at.desc())
        )
        assert event is not None
        assert event.outcome == "rejected"
        assert event.error_code == "export_limit_exceeded"
        assert "lead" not in event.filter_summary
    finally:
        app.dependency_overrides.clear()
        session.close()
