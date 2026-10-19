from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.main import app
from lead_agent.application.follow_up import FollowUpNotEligible, generate_follow_up_draft
from lead_agent.domain.follow_up import FollowUpEmailContent
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    LeadFollowUpEmailORM,
    LeadORM,
    ProviderConnectionORM,
    QualificationResultORM,
    RoutingDecisionORM,
    WorkspaceEmailTemplateORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.repositories import ensure_workspace

_LEAD_NUMBERS = count(1)


def _lead(workspace_id, *, manual_review: bool = False, disqualified: bool = False) -> LeadORM:
    now, lead_id, qualification_id = datetime.now(UTC), uuid4(), uuid4()
    lead = LeadORM(
        id=lead_id,
        workspace_id=workspace_id,
        lead_number=next(_LEAD_NUMBERS),
        source="website",
        source_detail=None,
        external_id=None,
        full_name="Riley Prospect",
        email="riley@example.test",
        phone=None,
        job_title="Operations Director",
        company_name="Northstar Labs",
        industry="Software",
        employee_count=42,
        annual_revenue=None,
        geography="US",
        budget=None,
        product_or_service="lead workflow automation",
        message="We are evaluating a lead workflow and want to discuss a demo.",
        received_at=now,
        raw_payload={},
        normalization_version="test-v1",
        created_at=now,
        updated_at=now,
    )
    qualification = QualificationResultORM(
        id=qualification_id,
        lead_id=lead_id,
        total_score=78,
        classification="disqualified" if disqualified else "warm",
        routing_queue="sales_development",
        routing_action="follow_up",
        hard_disqualified=disqualified,
        disqualifier_evidence=[],
        rationale=[],
        score_breakdown=[],
        component_scores={"icp_fit": 32},
        policy_version="test-v1",
        icp_version_id=None,
        icp_source="workspace",
        icp_profile_version="v1",
        icp_configuration_hash="test",
        confidence=0.9,
        created_at=now,
    )
    lead.qualification = qualification
    lead.routing_decisions.append(
        RoutingDecisionORM(
            id=uuid4(),
            workspace_id=workspace_id,
            lead_id=lead_id,
            qualification_id=qualification_id,
            qualification_band=qualification.classification,
            assigned_queue="sales_development",
            assigned_rep_id=None,
            assigned_rep_name=None,
            routing_reason="Strong buying signal",
            matched_rules=[],
            action_plan={"next_step_recommendation": "Schedule a short discovery call."},
            priority="high",
            notification_state="pending",
            manual_review=manual_review,
            idempotency_key=str(uuid4()),
            is_current=True,
            created_at=now,
        )
    )
    return lead


def test_draft_requires_ai_instead_of_silent_template_fallback(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'follow-up.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        workspace_id = uuid4()
        ensure_workspace(session, workspace_id, name="Draft QA")
        lead = _lead(workspace_id)
        session.add(lead)
        session.commit()
        monkeypatch.setattr(
            "lead_agent.application.follow_up.resolve_workspace_reasoning_provider",
            lambda *_args: object(),
        )
        with pytest.raises(FollowUpNotEligible, match="Connect and verify OpenAI"):
            generate_follow_up_draft(session, lead, actor_id="qa-user")
        assert session.query(LeadFollowUpEmailORM).filter_by(lead_id=lead.id).count() == 0
    engine.dispose()


def test_workspace_sample_guides_ai_and_sent_followups_are_preserved(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'workspace-email-sample.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        workspace_id = uuid4()
        ensure_workspace(session, workspace_id, name="Template QA")
        lead = _lead(workspace_id)
        session.add(lead)
        session.add(WorkspaceEmailTemplateORM(
            id=uuid4(), workspace_id=workspace_id, kind="follow_up",
            subject="Hello {{lead_name}} at {{company_name}}",
            body="Hi {{lead_name}}, can we discuss {{product_or_service}}?",
            generation_method="manual", updated_by="qa-user", updated_at=datetime.now(UTC),
        ))
        session.commit()
        facts_seen = []
        def ai_draft(facts):
            facts_seen.append(facts)
            return FollowUpEmailContent(
                subject="A fresh next step for Northstar Labs",
                body="Hi Riley, let's explore your lead workflow automation requirements.",
                rationale="Grounded in the original inquiry.",
            )
        monkeypatch.setattr(
            "lead_agent.application.follow_up.resolve_workspace_reasoning_provider",
            lambda *_args: SimpleNamespace(draft_follow_up=ai_draft),
        )

        row = generate_follow_up_draft(session, lead, actor_id="qa-user")

        assert row.subject == "A fresh next step for Northstar Labs"
        assert row.generation_method == "openai"
        assert facts_seen[0]["workspace_email_template"]["subject"] == (
            "Hello {{lead_name}} at {{company_name}}"
        )
        assert row.status == "draft"
        original_id = row.id
        row.status = "sent"
        row.sent_at = datetime.now(UTC)
        session.commit()
        next_draft = generate_follow_up_draft(session, lead, actor_id="qa-user")
        assert next_draft.id != original_id
        assert next_draft.status == "draft"
        assert len(next_draft.kind) <= 32
        assert session.get(LeadFollowUpEmailORM, original_id).status == "sent"
        assert session.query(LeadFollowUpEmailORM).filter_by(lead_id=lead.id).count() == 2
    engine.dispose()


@pytest.mark.parametrize("flags", [{"manual_review": True}, {"disqualified": True}])
def test_manual_review_and_disqualified_leads_cannot_get_outreach(
    tmp_path, monkeypatch, flags
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'blocked-follow-up.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        workspace_id = uuid4()
        ensure_workspace(session, workspace_id, name="Blocked QA")
        lead = _lead(workspace_id, **flags)
        session.add(lead)
        session.commit()
        monkeypatch.setattr(
            "lead_agent.application.follow_up.resolve_workspace_reasoning_provider",
            lambda *_args: object(),
        )
        with pytest.raises(FollowUpNotEligible):
            generate_follow_up_draft(session, lead, actor_id="qa-user")
        assert session.query(LeadFollowUpEmailORM).count() == 0
    engine.dispose()


def test_follow_up_is_edited_then_sent_only_on_explicit_crm_action(tmp_path, monkeypatch) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'follow-up-send.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id, other_workspace_id = uuid4(), uuid4()
    ensure_workspace(session, workspace_id, name="Send QA")
    ensure_workspace(session, other_workspace_id, name="Other QA")
    lead = _lead(workspace_id)
    session.add(lead)
    session.commit()
    monkeypatch.setattr(
        "lead_agent.application.follow_up.resolve_workspace_reasoning_provider",
        lambda *_args: SimpleNamespace(draft_follow_up=lambda facts: FollowUpEmailContent(
            subject=f"A next step for {facts['company']}",
            body=f"Hi {facts['lead_name']}, let's discuss {facts['requested_product_or_service']}.",
            rationale="Grounded in the lead inquiry.",
        )),
    )
    draft = generate_follow_up_draft(session, lead, actor_id="qa-user")
    connection = ProviderConnectionORM(
        id=uuid4(), workspace_id=workspace_id, provider_type="gmail",
        external_account_id="qa@example.test", account_name="qa@example.test", auth_type="oauth",
        status="verified", scopes=["https://www.googleapis.com/auth/gmail.send"],
        secret_reference="test-secret", credential_state="configured", last_validation_result={},
        connection_metadata={"gmail_email": "qa@example.test"}, is_active=True,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    session.add(connection)
    session.commit()

    sent: list[dict[str, str]] = []

    class FakeResolver:
        def resolve_connection(self, *_args, **_kwargs):
            return SimpleNamespace(credentials={"access_token": "not-a-real-token"})

    def fake_send(_self, **kwargs):
        sent.append(kwargs)
        return "mock-message-id"

    monkeypatch.setattr(
        "lead_agent.application.follow_up.get_workspace_connection_resolver",
        lambda: FakeResolver(),
    )
    monkeypatch.setattr(
        "lead_agent.application.follow_up.GmailOutboundProvider.send_email", fake_send
    )
    from lead_agent.application.follow_up import send_approved_follow_up

    with pytest.raises(FollowUpNotEligible):
        send_approved_follow_up(session, draft, workspace_id=other_workspace_id, actor_id="wrong")
    app.dependency_overrides[get_db] = lambda: session
    active_workspace = WorkspaceContext(
        workspace_id=workspace_id, actor_id="qa-user", permissions=frozenset({"leads.view"})
    )
    app.dependency_overrides[get_workspace_context] = lambda: active_workspace
    try:
        with TestClient(app) as client:
            wrong_workspace_send = client.post(f"/v1/leads/{lead.id}/follow-up/{draft.id}/send")
            active_workspace = WorkspaceContext(
                workspace_id=workspace_id,
                actor_id="viewer",
                permissions=frozenset({"leads.view"}),
            )
            denied_send = client.post(f"/v1/leads/{lead.id}/follow-up/{draft.id}/send")
            active_workspace = WorkspaceContext(
                workspace_id=workspace_id,
                actor_id="qa-user",
                permissions=frozenset({"leads.edit", "leads.view"}),
            )
            edited = client.patch(
                f"/v1/leads/{lead.id}/follow-up/{draft.id}",
                json={
                    "subject": "A personal follow-up",
                    "body": (
                        "Thanks for sharing your goals. Would a brief call next week be useful?"
                    ),
                },
            )
            sent_response = client.post(f"/v1/leads/{lead.id}/follow-up/{draft.id}/send")
            own = client.get(f"/v1/leads/{lead.id}/follow-up")
            active_workspace = WorkspaceContext(
                workspace_id=other_workspace_id,
                actor_id="other",
                permissions=frozenset({"leads.view"}),
            )
            cross_workspace = client.get(f"/v1/leads/{lead.id}/follow-up")
        assert wrong_workspace_send.status_code == 403
        assert denied_send.status_code == 403
        assert sent_response.status_code == 200
        assert sent_response.json()["status"] == "sent"
        assert edited.status_code == 200
        assert edited.json()["subject"] == "A personal follow-up"
        assert own.status_code == 200 and own.json()["status"] == "sent"
        assert cross_workspace.status_code == 404
        assert sent_response.json()["approved_by"] == "qa-user"
        assert sent_response.json()["external_message_id"] == "mock-message-id"
        assert len(sent) == 1
        assert sent[0]["recipient"] == lead.email
        assert sent[0]["subject"] == "A personal follow-up"
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def test_follow_up_content_rejects_blank_fields() -> None:
    with pytest.raises(ValueError):
        FollowUpEmailContent(subject=" ", body="long enough body", rationale="grounded")


def test_manual_progress_is_saved_and_rechecks_temperature(icp, routing, tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'progress.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id = uuid4()
    ensure_workspace(session, workspace_id, name="Progress QA")
    lead = _lead(workspace_id)
    session.add(lead)
    session.commit()

    from lead_agent.api.routes.leads import LeadProgressUpdate, update_lead_progress

    result = update_lead_progress(
        lead.id,
        LeadProgressUpdate(stage="responded", note="Asked for a proposal and pricing by Friday."),
        session=session,
        policies=(icp, routing),
        notification_provider=None,
        workspace=WorkspaceContext(
            workspace_id=workspace_id, actor_id="qa-user", permissions=frozenset({"leads.edit"})
        ),
    )

    session.refresh(lead)
    events = list(session.query(models.PipelineEventORM).filter_by(lead_id=lead.id))
    assert result.stage == "responded"
    assert result.classification == lead.qualification.classification
    assert lead.message == "We are evaluating a lead workflow and want to discuss a demo."
    assert any(event.stage == "manual_progress" for event in events)
    assert any(event.stage == "qualification_rechecked" for event in events)
    session.close()
    engine.dispose()


def test_lead_momentum_explains_score_change_evidence_and_delivery(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'momentum.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id = uuid4()
    ensure_workspace(session, workspace_id, name="Momentum QA")
    lead = _lead(workspace_id)
    session.add(lead)
    session.flush()
    now = datetime.now(UTC)
    routing = lead.routing_decisions[0]
    session.add_all([
        models.PipelineEventORM(
            id=uuid4(), workspace_id=workspace_id, lead_id=lead.id,
            stage="qualification_rechecked", status="completed",
            details={"previous_qualification": {"classification": "warm", "score": 70}},
            created_at=now,
        ),
        models.PipelineEventORM(
            id=uuid4(), workspace_id=workspace_id, lead_id=lead.id,
            stage="manual_progress", status="responded",
            details={"stage": "responded", "note": "Asked for a proposal this week."},
            created_at=now,
        ),
        models.NotificationORM(
            id=uuid4(), workspace_id=workspace_id, routing_decision_id=routing.id,
            provider="slack", status="sent", attempts=1, external_message_id="message-1",
            error_message=None, payload={}, created_at=now, sent_at=now, updated_at=now,
        ),
    ])
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    active_workspace = WorkspaceContext(
        workspace_id=workspace_id,
        actor_id="qa-user",
        permissions=frozenset({"leads.view", "routing.view"}),
    )
    app.dependency_overrides[get_workspace_context] = lambda: active_workspace
    try:
        with TestClient(app) as client:
            momentum = client.get(f"/v1/leads/{lead.id}/momentum")
            history = client.get(f"/v1/leads/{lead.id}/pipeline-history")
        assert momentum.status_code == 200
        payload = momentum.json()
        assert payload["state"] == "warming"
        assert payload["previous_score"] == 70
        assert payload["score_delta"] == 8
        assert any("proposal" in item["excerpt"].lower() for item in payload["evidence"])
        assert payload["delivery"][0]["destination"] == "Slack notification"
        assert payload["delivery"][0]["status"] == "sent"
        assert history.status_code == 200
        assert [event["stage"] for event in history.json()] == [
            "qualification_rechecked", "manual_progress"
        ]
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()
