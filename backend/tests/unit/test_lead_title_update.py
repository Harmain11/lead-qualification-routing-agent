from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.main import app
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import LeadORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.repositories import ensure_workspace


def test_title_patch_is_explicit_local_edit_and_workspace_scoped(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lead-title.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id = uuid4()
    other_workspace_id = uuid4()
    ensure_workspace(session, workspace_id, name="Workspace A")
    ensure_workspace(session, other_workspace_id, name="Workspace B")
    now = datetime.now(UTC)
    lead = LeadORM(
        workspace_id=workspace_id,
        lead_number=1,
        source="salesforce",
        source_detail="Web",
        external_id="00Q-test",
        full_name="Outbound Guard Test",
        email="outbound-guard-test@example.com",
        phone=None,
        job_title="Integration QA Tester",
        company_name="Local Only Testing Co",
        industry="Software",
        employee_count=None,
        annual_revenue=None,
        geography="Remote",
        budget=None,
        product_or_service="Integration QA",
        message="Artificial local-only record for update certification.",
        received_at=now,
        raw_payload={},
        normalization_version="test-v1",
        created_at=now,
        updated_at=now,
    )
    session.add(lead)
    session.commit()
    lead_id = lead.id

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    active_workspace = WorkspaceContext(
        workspace_id=workspace_id,
        actor_id="tester",
        permissions=frozenset({"leads.edit", "leads.view"}),
    )
    app.dependency_overrides[get_workspace_context] = lambda: active_workspace
    try:
        with TestClient(app) as client:
            updated = client.patch(
                f"/v1/leads/{lead_id}",
                json={"job_title": "Integration QA Updated Locally"},
            )
            blank = client.patch(
                f"/v1/leads/{lead_id}", json={"job_title": "   "}
            )
            active_workspace = WorkspaceContext(
                workspace_id=other_workspace_id,
                actor_id="other-tenant-user",
                permissions=frozenset({"leads.edit"}),
            )
            cross_workspace = client.patch(
                f"/v1/leads/{lead_id}",
                json={"job_title": "Must Not Cross Workspaces"},
            )

        assert updated.status_code == 200
        assert updated.json()["job_title"] == "Integration QA Updated Locally"
        assert updated.json()["crm_sync"] is None
        assert blank.status_code == 422
        assert cross_workspace.status_code == 404
        session.refresh(lead)
        assert lead.job_title == "Integration QA Updated Locally"
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_title_patch_requires_leads_edit_permission(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lead-title-permission.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id = uuid4()
    ensure_workspace(session, workspace_id, name="Viewer Workspace")
    now = datetime.now(UTC)
    lead = LeadORM(
        workspace_id=workspace_id,
        lead_number=1,
        source="website",
        source_detail=None,
        external_id=None,
        full_name="Viewer Lead",
        email="viewer@example.test",
        phone=None,
        job_title="Original Title",
        company_name="Example Co",
        industry=None,
        employee_count=None,
        annual_revenue=None,
        geography=None,
        budget=None,
        product_or_service="Lead routing",
        message="Permission test.",
        received_at=now,
        raw_payload={},
        normalization_version="test-v1",
        created_at=now,
        updated_at=now,
    )
    session.add(lead)
    session.commit()
    lead_id = lead.id

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        workspace_id=workspace_id,
        actor_id="viewer",
        role="viewer",
        permissions=frozenset({"leads.view"}),
    )
    try:
        with TestClient(app) as client:
            response = client.patch(
                f"/v1/leads/{lead_id}", json={"job_title": "Unauthorized"}
            )
        assert response.status_code == 403
        session.refresh(lead)
        assert lead.job_title == "Original Title"
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()
