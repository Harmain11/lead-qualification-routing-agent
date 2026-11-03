from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.main import app
from lead_agent.application.routing import route_and_notify
from lead_agent.application.services import process_lead
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import UserORM, WorkspaceMembershipORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.repositories import ensure_workspace, get_current_routing


def test_generated_marker_and_manual_assignment_persist(tmp_path, icp, routing) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lead-row-edits.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id = uuid4()
    now = datetime.now(UTC)
    ensure_workspace(session, workspace_id, name="Lead Row Edit Workspace")

    representatives = []
    for index, name in enumerate(("Alex Rep", "Sam Rep")):
        user = UserORM(
            id=uuid4(),
            email=f"rep-{index}@example.test",
            display_name=name,
            password_hash="test-hash",
            status="active",
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        session.add(
            WorkspaceMembershipORM(
                id=uuid4(),
                workspace_id=workspace_id,
                user_id=user.id,
                role="sales_rep",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        representatives.append(user)

    viewer = UserORM(
        id=uuid4(),
        email="viewer@example.test",
        display_name="Workspace Viewer",
        password_hash="test-hash",
        status="active",
        created_at=now,
        updated_at=now,
    )
    session.add(viewer)
    session.add(
        WorkspaceMembershipORM(
            id=uuid4(),
            workspace_id=workspace_id,
            user_id=viewer.id,
            role="viewer",
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    session.commit()

    payload = {
        "external_id": "lead-row-edit-test",
        "source": "website",
        "full_name": "Row Edit Lead",
        "email": "row-edit@example.test",
        "job_title": "Operations Director",
        "company_name": "Row Edit Co",
        "industry": "Software",
        "employee_count": 80,
        "geography": "United States",
        "budget": 12000,
        "product_or_service": "Lead workflow",
        "message": "We need a lead workflow soon and would like a proposal.",
    }
    _, _, lead = process_lead(
        session, payload, icp, routing, workspace_id=workspace_id
    )
    initial_routing = get_current_routing(session, lead.id, workspace_id)
    assert initial_routing is not None
    assert initial_routing.assigned_rep_id in {
        str(user.id) for user in representatives
    }
    auto_assignee_id = UUID(initial_routing.assigned_rep_id)
    viewer_context = WorkspaceContext(
        workspace_id=workspace_id,
        actor_id="lead-row-editor",
        user_id=viewer.id,
        permissions=frozenset({"leads.view", "leads.edit", "routing.manage"}),
    )
    workspace_context = viewer_context

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_workspace_context] = lambda: workspace_context
    try:
        with TestClient(app) as client:
            workspace_context = WorkspaceContext(
                workspace_id=workspace_id,
                actor_id="auto-assignee",
                user_id=auto_assignee_id,
                permissions=frozenset({"leads.view"}),
            )
            auto_notifications = client.get("/v1/notifications/assignments")
            assert auto_notifications.status_code == 200
            assert len(auto_notifications.json()) == 1
            assert auto_notifications.json()[0]["lead_id"] == str(lead.id)

            workspace_context = viewer_context
            generated = client.patch(
                f"/v1/leads/{lead.id}/generated",
                json={"is_generated": True},
            )
            options = client.get("/v1/leads/assignment-options")
            invalid_assignment = client.patch(
                f"/v1/leads/{lead.id}/assignment",
                json={"assigned_rep_id": str(viewer.id)},
            )
            assigned = client.patch(
                f"/v1/leads/{lead.id}/assignment",
                json={"assigned_rep_id": str(representatives[1].id)},
            )
            viewer_notifications = client.get("/v1/notifications/assignments")

        assert generated.status_code == 200
        assert generated.json()["is_generated"] is True
        assert options.status_code == 200
        assert {item["user_id"] for item in options.json()} == {
            str(user.id) for user in representatives
        }
        assert invalid_assignment.status_code == 422
        assert assigned.status_code == 200
        assert assigned.json()["routing"]["assigned_rep_id"] == str(
            representatives[1].id
        )
        assert assigned.json()["routing"]["assigned_rep_name"] == "Sam Rep"
        assert viewer_notifications.status_code == 200
        assert viewer_notifications.json() == []

        workspace_context = WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="sam-rep",
            user_id=representatives[1].id,
            permissions=frozenset({"leads.view"}),
        )
        with TestClient(app) as client:
            assigned_notifications = client.get("/v1/notifications/assignments")
            assert assigned_notifications.status_code == 200
            assert len(assigned_notifications.json()) == 1
            assignment_notification = assigned_notifications.json()[0]
            assert assignment_notification["lead_id"] == str(lead.id)
            assert assignment_notification["lead_name"] == "Row Edit Lead"
            assert assignment_notification["company_name"] == "Row Edit Co"
            marked_read = client.patch(
                f"/v1/notifications/assignments/{assignment_notification['id']}/read"
            )
            assert marked_read.status_code == 200
            assert marked_read.json()["read_at"] is not None
            assert client.get("/v1/notifications/assignments").json() == []

        session.refresh(lead)
        decision = get_current_routing(session, lead.id, workspace_id)
        assert lead.is_generated is True
        assert decision is not None
        assert decision.is_manually_assigned is True
        forced_decision = route_and_notify(
            session,
            lead,
            routing,
            force=True,
            send_notification=False,
        )
        assert forced_decision.assigned_rep_id == str(representatives[1].id)
        assert forced_decision.is_manually_assigned is True
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_lead_list_accepts_legacy_analysis_without_intent_scoring(
    tmp_path, icp, routing
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'legacy-analysis-list.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id = uuid4()
    ensure_workspace(session, workspace_id, name="Legacy Analysis Workspace")
    _, _, lead = process_lead(
        session,
        {
            "external_id": "legacy-analysis-list",
            "source": "website",
            "full_name": "Legacy Analysis Lead",
            "email": "legacy-analysis@example.test",
            "company_name": "Legacy Analysis Co",
            "product_or_service": "Lead workflow",
            "message": "Please share information about your service.",
        },
        icp,
        routing,
        workspace_id=workspace_id,
    )
    legacy_output = dict(lead.analysis.structured_output)
    legacy_output.pop("intent_scoring")
    lead.analysis.structured_output = legacy_output
    session.commit()

    workspace_context = WorkspaceContext(
        workspace_id=workspace_id,
        actor_id="legacy-analysis-reader",
        user_id=uuid4(),
        permissions=frozenset({"leads.view"}),
    )

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_workspace_context] = lambda: workspace_context
    try:
        with TestClient(app) as client:
            response = client.get("/v1/leads", params={"limit": 50})

        assert response.status_code == 200, response.text
        intent_scoring = response.json()["items"][0]["analysis"]["intent_scoring"]
        assert intent_scoring["urgency"]["points"] == 0
        assert intent_scoring["buying_stage"]["points"] == 0
        assert intent_scoring["budget_signal"]["points"] == 0
        assert intent_scoring["specific_request"]["points"] == 0
        assert "intent_scoring" not in lead.analysis.structured_output
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()
