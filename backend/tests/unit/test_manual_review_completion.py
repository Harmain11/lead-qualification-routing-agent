from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.main import app
from lead_agent.api.routes import leads as lead_routes
from lead_agent.application.services import process_lead
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import PipelineEventORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.notifications.mock import MockNotificationProvider
from lead_agent.infrastructure.repositories import ensure_workspace, get_current_routing


def test_manual_review_completion_releases_hold_and_requires_permission(
    tmp_path, icp, routing
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'manual-review-completion.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id = uuid4()
    ensure_workspace(session, workspace_id, name="Review Completion Workspace")

    _, _, lead = process_lead(
        session,
        {
            "external_id": "manual-review-completion-test",
            "source": "website",
            "full_name": "Review Completion Lead",
            "email": "review-completion@example.test",
            "company_name": "Review Completion Co",
            "product_or_service": "Lead workflow",
            "message": "We need a lead workflow soon and would like a proposal.",
        },
        icp,
        routing,
        workspace_id=workspace_id,
    )
    current = get_current_routing(session, lead.id, workspace_id)
    assert current is not None
    assert lead.analysis is not None
    lead.analysis.requires_manual_review = True
    lead.analysis.status = "manual_review"
    current.manual_review = True
    current.assigned_queue = routing.manual_review_queue
    session.commit()

    workspace_context = WorkspaceContext(
        workspace_id=workspace_id,
        actor_id="review-manager",
        permissions=frozenset({"leads.view", "manual_review.manage"}),
    )

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_workspace_context] = lambda: workspace_context
    app.dependency_overrides[lead_routes.policy_dependency] = lambda: (icp, routing)
    app.dependency_overrides[lead_routes.get_notification_provider] = (
        lambda: MockNotificationProvider()
    )
    try:
        with TestClient(app) as client:
            workspace_context = WorkspaceContext(
                workspace_id=workspace_id,
                actor_id="review-viewer",
                permissions=frozenset({"leads.view"}),
            )
            denied = client.post(
                f"/v1/leads/{lead.id}/manual-review/complete"
            )
            assert denied.status_code == 403
            assert lead.analysis.requires_manual_review is True

            workspace_context = WorkspaceContext(
                workspace_id=workspace_id,
                actor_id="review-manager",
                permissions=frozenset({"leads.view", "manual_review.manage"}),
            )
            completed = client.post(
                f"/v1/leads/{lead.id}/manual-review/complete"
            )
            stale = client.post(
                f"/v1/leads/{lead.id}/manual-review/complete"
            )

        assert completed.status_code == 200
        result = completed.json()
        assert result["analysis"]["requires_manual_review"] is False
        assert result["analysis"]["status"] == "completed"
        assert result["routing"]["manual_review"] is False
        assert result["routing"]["assigned_queue"] != routing.manual_review_queue
        assert result["review_reasons"] == []
        assert stale.status_code == 409

        session.refresh(lead)
        assert lead.analysis.requires_manual_review is False
        event = session.scalar(
            select(PipelineEventORM)
            .where(
                PipelineEventORM.lead_id == lead.id,
                PipelineEventORM.stage == "manual_review",
                PipelineEventORM.status == "completed",
            )
            .order_by(PipelineEventORM.created_at.desc())
        )
        assert event is not None
        assert event.details["actor_id"] == "review-manager"
        assert event.details["cleared_analysis_hold"] is True
        assert event.details["cleared_routing_hold"] is True
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()
