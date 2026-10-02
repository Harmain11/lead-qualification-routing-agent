from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.main import app
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.repositories import ensure_workspace


def test_email_template_settings_are_workspace_scoped_and_editable(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'email-template-settings.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id = uuid4()
    ensure_workspace(session, workspace_id, name="Email template QA")
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        workspace_id=workspace_id,
        actor_id="qa-user",
        permissions=frozenset({"integrations.manage"}),
    )
    try:
        with TestClient(app) as client:
            initial = client.get("/v1/settings/email-templates")
            saved = client.put(
                "/v1/settings/email-templates/follow_up",
                json={
                    "subject": "Hello {{lead_name}}",
                    "body": "A saved sample that can be personalized for every lead.",
                    "generation_method": "manual",
                },
            )
            updated = client.get("/v1/settings/email-templates")
        assert initial.status_code == 200
        assert {row["kind"] for row in initial.json()} == {"confirmation", "follow_up"}
        assert saved.status_code == 200
        assert saved.json()["subject"] == "Hello {{lead_name}}"
        assert updated.json()[1]["generation_method"] == "manual"
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()
