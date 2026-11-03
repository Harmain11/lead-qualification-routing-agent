from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_db, get_workspace_context
from lead_agent.api.main import app
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import LeadORM, PipelineEventORM
from lead_agent.infrastructure.repositories import ensure_workspace


def test_google_calendar_booking_link_is_workspace_scoped_and_validated(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'google-booking-link.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    first_workspace_id = uuid4()
    second_workspace_id = uuid4()
    ensure_workspace(session, first_workspace_id, name="First booking workspace")
    ensure_workspace(session, second_workspace_id, name="Second booking workspace")
    session.commit()

    selected = {"workspace_id": first_workspace_id}
    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        workspace_id=selected["workspace_id"],
        actor_id="booking-admin",
        permissions=frozenset({"integrations.manage"}),
    )
    endpoint = "/v1/settings/email-automation/google-calendar-booking-link"
    try:
        with TestClient(app) as client:
            assert client.get(endpoint).json() == {"url": ""}
            saved_url = "https://calendar.app.google/team-appointment"
            assert client.put(endpoint, json={"url": saved_url}).json() == {
                "url": saved_url
            }
            assert client.get(endpoint).json() == {"url": saved_url}
            assert client.put(
                endpoint, json={"url": "https://example.com/book"}
            ).status_code == 422

            selected["workspace_id"] = second_workspace_id
            assert client.get(endpoint).json() == {"url": ""}
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)
        session.close()
        engine.dispose()


def test_saved_meeting_status_is_visible_and_workspace_scoped(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'saved-meeting-status.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id = uuid4()
    other_workspace_id = uuid4()
    lead_id = uuid4()
    now = datetime.now(UTC)
    ensure_workspace(session, workspace_id, name="Meeting status workspace")
    ensure_workspace(session, other_workspace_id, name="Other workspace")
    session.add(
        LeadORM(
            id=lead_id,
            workspace_id=workspace_id,
            lead_number=1,
            source="gmail",
            source_detail="inbound",
            external_id=None,
            full_name="Harmain Mughal",
            email="harmain@example.com",
            phone=None,
            job_title=None,
            company_name="Harmain Construction",
            industry=None,
            employee_count=None,
            annual_revenue=None,
            geography=None,
            budget=None,
            product_or_service="",
            message="Can we schedule a meeting next week?",
            received_at=now,
            raw_payload={},
            normalization_version="1.0",
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()
    session.add(
        PipelineEventORM(
            workspace_id=workspace_id,
            lead_id=lead_id,
            stage="meeting_scheduled",
            status="scheduled",
            details={
                "provider": "calendly",
                "title": "Intro meeting",
                "start_at": now.isoformat().replace("+00:00", "Z"),
            },
            created_at=now,
        )
    )
    session.commit()

    selected = {"workspace_id": workspace_id}
    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        workspace_id=selected["workspace_id"],
        actor_id="meeting-viewer",
        permissions=frozenset({"leads.view"}),
    )
    try:
        with TestClient(app) as client:
            endpoint = f"/v1/leads/{lead_id}/meeting-status"
            response = client.get(endpoint)
            assert response.status_code == 200
            assert response.json() == {
                "status": "scheduled",
                "start_at": now.isoformat().replace("+00:00", "Z"),
                "title": "Intro meeting",
                "provider": "calendly",
                "cancellation_reason": None,
            }

            selected["workspace_id"] = other_workspace_id
            assert client.get(endpoint).status_code == 404
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)
        session.close()
        engine.dispose()
