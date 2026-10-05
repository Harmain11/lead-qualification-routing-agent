from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.main import app
from lead_agent.domain.follow_up import FollowUpEmailContent
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.repositories import ensure_workspace


def test_email_template_settings_are_workspace_scoped_and_editable(tmp_path, monkeypatch) -> None:
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
    captured_facts: dict[str, object] = {}

    class SampleProvider:
        def draft_follow_up(self, facts: dict[str, object]) -> FollowUpEmailContent:
            captured_facts.update(facts)
            return FollowUpEmailContent(
                subject="A question about {{product_or_service}}",
                body=(
                    "Hi {{lead_name}},\n\nYour work at {{company_name}} caught our attention. "
                    "Would you be open to discussing {{product_or_service}}?\n\n"
                    "Warm regards,\nThe team"
                ),
                rationale="Fresh wording with a warm, conversational style.",
            )

    monkeypatch.setattr(
        "lead_agent.api.routes.email_templates.resolve_workspace_reasoning_provider",
        lambda *_args: SampleProvider(),
    )
    try:
        with TestClient(app) as client:
            initial = client.get("/v1/settings/email-templates")
            sample = {
                "subject": "A friendly note about {{product_or_service}}",
                "body": (
                    "Hello {{lead_name}},\n\nI enjoyed reading about {{company_name}} and would "
                    "love to hear what you need from {{product_or_service}}.\n\nBest,\nThe team"
                ),
            }
            generated = client.post(
                "/v1/settings/email-templates/follow_up/generate",
                json=sample,
            )
            after_generation = client.get("/v1/settings/email-templates")
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
        assert generated.status_code == 200
        assert after_generation.status_code == 200
        assert next(row for row in after_generation.json() if row["kind"] == "follow_up") == next(
            row for row in initial.json() if row["kind"] == "follow_up"
        )
        assert generated.json()["subject"] != sample["subject"]
        assert generated.json()["body"] != sample["body"]
        assert generated.json()["generation_method"] == "ai"
        assert captured_facts["template_generation"] is True
        assert captured_facts["email_purpose"] == "follow_up"
        assert captured_facts["style_reference"] == sample
        assert captured_facts["required_placeholders"] == [
            "{{lead_name}}",
            "{{company_name}}",
            "{{product_or_service}}",
        ]
        assert saved.status_code == 200
        assert saved.json()["subject"] == "Hello {{lead_name}}"
        assert updated.json()[1]["generation_method"] == "manual"
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()
