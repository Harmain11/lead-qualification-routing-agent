from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.main import app
from lead_agent.application.contacts import attach_contact, duplicate_suggestions
from lead_agent.application.follow_up import FollowUpNotEligible, generate_follow_up_draft
from lead_agent.application.governance import OutreachBlocked, assert_outreach_allowed
from lead_agent.application.services import process_lead
from lead_agent.application.territories import capacity_candidates, lead_territory
from lead_agent.domain.operations import AutomationInput, OperationsPolicy
from lead_agent.domain.rbac import role_permissions
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import UserORM, WorkspaceMembershipORM
from lead_agent.infrastructure.db.operations import (
    ContactORM,
    LeadNoteORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.repositories import ensure_workspace


@pytest.fixture
def ops(icp, routing):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    event.listen(engine, "connect", lambda db, _: db.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        workspace_id, other_id, user_id = uuid4(), uuid4(), uuid4()
        ensure_workspace(session, workspace_id)
        ensure_workspace(session, other_id)
        now = datetime.now(UTC)
        user = UserORM(
            id=user_id,
            email="rep@example.test",
            display_name="Rep",
            password_hash="test",
            created_at=now,
            updated_at=now,
            email_verified=True,
            status="active",
        )
        member = WorkspaceMembershipORM(
            workspace_id=workspace_id,
            user_id=user_id,
            role="sales_rep",
            status="active",
            created_at=now,
            updated_at=now,
            permission_overrides={},
        )
        session.add_all([user, member])
        session.commit()
        _, _, lead = process_lead(
            session,
            {
                "source": "web",
                "full_name": "Alex Smith",
                "email": "alex@acme.test",
                "company_name": "Acme",
                "industry": "Software",
                "geography": "Pakistan",
                "product_or_service": "Consulting",
                "message": "We need pricing and a demo this month.",
            },
            icp,
            routing,
            workspace_id=workspace_id,
        )
        workspace = WorkspaceContext(
            workspace_id=workspace_id,
            actor_id=str(user_id),
            user_id=user_id,
            membership_id=member.id,
            role="owner",
            permissions=role_permissions("owner"),
        )
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_workspace_context] = lambda: workspace
        with TestClient(app) as client:
            yield session, client, workspace, other_id, lead, user, member
        app.dependency_overrides.clear()
    engine.dispose()


def test_contact_is_independent_and_linked_once(ops):
    session, client, workspace, _, lead, *_ = ops
    contact = attach_contact(session, lead)
    session.commit()
    assert attach_contact(session, lead).id == contact.id
    assert client.get("/v1/records/contacts").json()["total"] == 1
    details = client.get(f"/v1/records/contacts/{contact.id}").json()
    assert details["touches"][0]["id"] == str(lead.id)
    assert details["company"]["domain"] == "acme.test"
    response = client.put(
        f"/v1/records/contacts/{uuid4()}",
        json={"name": "No leads yet", "email": "new@example.test"},
    )
    assert response.status_code == 200
    assert client.get("/v1/records/contacts").json()["total"] == 2


def test_record_and_policy_cannot_cross_tenants(ops):
    session, client, _, other_id, *_ = ops
    contact = ContactORM(workspace_id=other_id, name="Other", email="other@other.test")
    session.add(contact)
    session.commit()
    assert client.get(f"/v1/records/contacts/{contact.id}").status_code == 404
    assert (
        client.put(f"/v1/records/contacts/{contact.id}", json={"name": "Overwrite"}).status_code
        == 404
    )
    assert client.get("/v1/records/contacts").json()["total"] == 1
    assert (
        client.put(
            "/v1/ops/policy",
            json={
                "version": 0,
                "configuration": {
                    "territories": [
                        {"id": str(uuid4()), "name": "Other", "member_ids": [str(uuid4())]}
                    ]
                },
            },
        ).status_code
        == 422
    )


def test_suppression_blocks_draft_before_ai_or_eligibility(ops):
    session, client, workspace, _, lead, *_ = ops
    data = {
        "email": lead.email,
        "channel": "all",
        "status": "denied",
        "basis": "objection",
        "purpose": "No outreach",
        "evidence": "Contact asked to stop",
    }
    assert client.post("/v1/ops/consent", json=data).status_code == 201
    with pytest.raises(OutreachBlocked, match="Do not contact"):
        assert_outreach_allowed(session, workspace.workspace_id, lead.email)
    with pytest.raises(FollowUpNotEligible, match="Do not contact"):
        generate_follow_up_draft(session, lead, actor_id=workspace.actor_id)
    # A channel-specific grant cannot bypass a global objection.
    data.update(channel="email", status="granted", basis="consent")
    client.post("/v1/ops/consent", json=data)
    with pytest.raises(OutreachBlocked):
        assert_outreach_allowed(session, workspace.workspace_id, lead.email)


def test_strict_consent_and_policy_version(ops):
    session, client, workspace, _, lead, *_ = ops
    response = client.put(
        "/v1/ops/policy", json={"version": 0, "configuration": {"require_recorded_consent": True}}
    )
    assert response.status_code == 200
    assert client.put("/v1/ops/policy", json={"version": 0, "configuration": {}}).status_code == 409
    with pytest.raises(OutreachBlocked, match="Record permission"):
        assert_outreach_allowed(session, workspace.workspace_id, lead.email)
    client.post(
        "/v1/ops/consent",
        json={
            "email": lead.email,
            "channel": "email",
            "status": "granted",
            "basis": "consent",
            "purpose": "Follow-up",
            "evidence": "Requested reply",
        },
    )
    assert_outreach_allowed(session, workspace.workspace_id, lead.email)


def test_territory_capacity_and_sla(ops):
    session, client, workspace, _, lead, user, member = ops
    config = {
        "territories": [
            {
                "id": str(uuid4()),
                "name": "Pakistan",
                "regions": ["Pakistan"],
                "member_ids": [str(user.id)],
                "sla_hours": 2,
            }
        ],
        "capacity": [{"user_id": str(user.id), "max_open_leads": 1, "available": True}],
    }
    assert (
        client.put("/v1/ops/policy", json={"version": 0, "configuration": config}).status_code
        == 200
    )
    assert lead_territory(session, lead).sla_hours == 2
    assert len(capacity_candidates(session, lead, [(member, user)], {str(user.id): 0})[0]) == 1
    assert not capacity_candidates(session, lead, [(member, user)], {str(user.id): 1})[0]
    lead.geography = "United Kingdom"
    assert not capacity_candidates(session, lead, [(member, user)], {})[0]


def test_duplicate_suggestions_never_merge_silently(ops):
    session, client, workspace, _, lead, *_ = ops
    original = attach_contact(session, lead)
    other = ContactORM(
        workspace_id=workspace.workspace_id,
        company_id=original.company_id,
        name=original.name,
        email="alex.smith@acme.test",
    )
    session.add(other)
    session.commit()
    assert len(duplicate_suggestions([original, other])) == 1
    assert other.merged_into_id is None
    assert (
        client.post(
            f"/v1/records/contacts/{other.id}/merge",
            json={"target_id": str(original.id), "confirm": False},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/v1/records/contacts/{other.id}/merge",
            json={"target_id": str(original.id), "confirm": True},
        ).status_code
        == 200
    )
    assert client.get("/v1/records/contacts").json()["total"] == 1


def test_compliance_export_includes_evidence_and_approval_ledgers(ops):
    _, client, _, _, lead, *_ = ops
    response = client.get(f"/v1/ops/leads/{lead.id}/audit-export")
    assert response.status_code == 200
    export = response.json()
    assert export["lead"]["id"] == str(lead.id)
    assert {"events", "routing_history", "follow_ups", "consent", "agent_tool_audit"}.issubset(
        export
    )
    assert "password_hash" not in response.text
    assert export["contact"]["email"] == lead.email
    assert export["company"]["domain"] == "acme.test"


def test_retention_requires_current_explicit_confirmation(ops):
    session, client, _, _, lead, *_ = ops
    client.put("/v1/ops/policy", json={"version": 0, "configuration": {"retention_days": 30}})
    lead.updated_at = datetime.now(UTC) - timedelta(days=40)
    session.commit()
    plan = client.get("/v1/ops/retention").json()
    assert plan["count"] == 1
    assert (
        client.post(
            "/v1/ops/retention/apply",
            json={"proof": plan["proof"], "confirm_permanent_deletion": False},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/v1/ops/retention/apply", json={"proof": "0" * 64, "confirm_permanent_deletion": True}
        ).status_code
        == 409
    )


def test_residency_cannot_claim_unavailable_region(ops):
    _, client, *_ = ops
    assert (
        client.put(
            "/v1/ops/policy", json={"version": 0, "configuration": {"required_region": "gcc"}}
        ).status_code
        == 422
    )


def test_graph_rejects_loops_and_unreachable_nodes():
    base = {
        "name": "Test",
        "kind": "playbook",
        "nodes": [{"id": "start", "type": "start", "next": "end"}, {"id": "end", "type": "end"}],
    }
    assert AutomationInput.model_validate(base).nodes[0].id == "start"
    base["nodes"][1] = {"id": "end", "type": "wait", "next": "start"}
    with pytest.raises(ValidationError):
        AutomationInput.model_validate(base)
    with pytest.raises(ValidationError):
        OperationsPolicy.model_validate({"approval_tiers": {"draft": "automatic"}})


def test_note_versions_preserve_teammate_edits_and_presence_expires(ops):
    from lead_agent.infrastructure.db.operations import LeadPresenceORM

    session, client, workspace, _, lead, *_ = ops
    note_id = uuid4()
    path = f"/v1/collaboration/leads/{lead.id}/notes/{note_id}"
    assert client.put(path, json={"body": "First", "version": 0}).status_code == 200
    assert client.put(path, json={"body": "Teammate", "version": 1}).status_code == 200
    assert client.put(path, json={"body": "Stale overwrite", "version": 1}).status_code == 409
    assert session.get(LeadNoteORM, note_id).body == "Teammate"
    assert client.post(f"/v1/collaboration/leads/{lead.id}/presence").status_code == 200
    result = client.get(f"/v1/collaboration/leads/{lead.id}").json()
    assert result["presence"][0]["user_id"] == str(workspace.user_id)
    row = session.get(LeadPresenceORM, (lead.id, workspace.user_id))
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    assert not client.get(f"/v1/collaboration/leads/{lead.id}").json()["presence"]


def test_retention_deletes_graph_but_preserves_independent_contacts(ops):
    from lead_agent.infrastructure.db.models import LeadFollowUpEmailORM, LeadORM, PipelineEventORM

    session, client, _, _, lead, *_ = ops
    contact = attach_contact(session, lead)
    lead_id = lead.id
    session.add(
        LeadNoteORM(
            workspace_id=lead.workspace_id,
            lead_id=lead_id,
            body="Expired",
            author_id="test",
            updated_by="test",
        )
    )
    client.put("/v1/ops/policy", json={"version": 0, "configuration": {"retention_days": 30}})
    lead.updated_at = datetime.now(UTC) - timedelta(days=40)
    session.commit()
    plan = client.get("/v1/ops/retention").json()
    response = client.post(
        "/v1/ops/retention/apply", json={"proof": plan["proof"], "confirm_permanent_deletion": True}
    )
    assert response.status_code == 200, response.text
    session.expire_all()
    assert session.get(LeadORM, lead_id) is None
    assert session.scalar(select(func.count()).select_from(LeadNoteORM)) == 0
    assert (
        session.scalar(
            select(func.count())
            .select_from(PipelineEventORM)
            .where(PipelineEventORM.lead_id == lead_id)
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(LeadFollowUpEmailORM)
            .where(LeadFollowUpEmailORM.lead_id == lead_id)
        )
        == 0
    )
    assert session.get(ContactORM, contact.id) is not None


def test_retention_plan_becomes_invalid_after_recent_update(ops):
    session, client, _, _, lead, *_ = ops
    client.put("/v1/ops/policy", json={"version": 0, "configuration": {"retention_days": 30}})
    lead.updated_at = datetime.now(UTC) - timedelta(days=40)
    session.commit()
    plan = client.get("/v1/ops/retention").json()
    lead.updated_at = datetime.now(UTC)
    session.commit()
    assert (
        client.post(
            "/v1/ops/retention/apply",
            json={"proof": plan["proof"], "confirm_permanent_deletion": True},
        ).status_code
        == 409
    )


def saved_workflow(client, lead, nodes, kind="playbook"):
    definition_id = uuid4()
    result = client.put(
        f"/v1/automations/{definition_id}",
        json={"name": "Test workflow", "kind": kind, "active": True, "nodes": nodes, "version": 0},
    )
    assert result.status_code == 200, result.text
    result = client.post(f"/v1/automations/{definition_id}/enroll", json={"lead_id": str(lead.id)})
    assert result.status_code == 201, result.text
    return result.json()


def test_branching_workflow_and_note_idempotency(ops):
    from lead_agent.application.automations import advance_run
    from lead_agent.infrastructure.db.operations import AutomationRunORM

    session, client, workspace, _, lead, *_ = ops
    lead.qualification.classification = "hot"
    session.commit()
    run = saved_workflow(
        client,
        lead,
        [
            {"id": "start", "type": "start", "next": "condition"},
            {
                "id": "condition",
                "type": "condition",
                "condition": "hot",
                "next": "hot_note",
                "otherwise": "cold_note",
            },
            {"id": "hot_note", "type": "note", "text": "Hot signal", "next": "end"},
            {"id": "cold_note", "type": "note", "text": "Cold signal", "next": "end"},
            {"id": "end", "type": "end"},
        ],
    )
    row = session.get(AutomationRunORM, __import__("uuid").UUID(run["id"]))
    advance_run(session, row, workspace.actor_id)
    advance_run(session, row, workspace.actor_id)
    assert row.status == "completed", row.last_error
    assert list(session.scalars(select(LeadNoteORM.body))) == ["Hot signal"]


def test_scheduled_sequence_creates_one_draft_and_never_sends(ops, monkeypatch):
    from lead_agent.application import automations
    from lead_agent.infrastructure.db.models import LeadFollowUpEmailORM
    from lead_agent.infrastructure.db.operations import AutomationRunORM

    session, client, workspace, _, lead, *_ = ops
    calls = []

    def draft(session, lead, actor_id):
        calls.append(lead.id)
        now = datetime.now(UTC)
        row = LeadFollowUpEmailORM(
            workspace_id=lead.workspace_id,
            lead_id=lead.id,
            kind="sales_follow_up",
            recipient=lead.email,
            subject="Review me",
            body="Draft only",
            status="draft",
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        return row

    monkeypatch.setattr(automations, "generate_follow_up_draft", draft)
    run = saved_workflow(
        client,
        lead,
        [
            {"id": "start", "type": "start", "next": "draft"},
            {"id": "draft", "type": "draft", "next": "end"},
            {"id": "end", "type": "end"},
        ],
        kind="sequence",
    )
    row = session.get(AutomationRunORM, __import__("uuid").UUID(run["id"]))
    automations.advance_run(session, row, workspace.actor_id)
    automations.advance_run(session, row, workspace.actor_id)
    assert len(calls) == 1 and row.status == "awaiting_draft", row.last_error
    email = session.get(LeadFollowUpEmailORM, __import__("uuid").UUID(row.state["draft_id"]))
    assert email.status == "draft" and email.sent_at is None
    email.status = "sent"
    session.commit()
    automations.advance_run(session, row, workspace.actor_id)
    assert row.status == "completed", row.last_error and len(calls) == 1


def test_reply_pauses_sequence_and_revoked_member_cannot_run(ops):
    from lead_agent.application.automations import advance_run
    from lead_agent.infrastructure.db.models import PipelineEventORM
    from lead_agent.infrastructure.db.operations import AutomationRunORM

    session, client, workspace, _, lead, _, member = ops
    run = saved_workflow(
        client,
        lead,
        [
            {"id": "start", "type": "start", "next": "note"},
            {"id": "note", "type": "note", "text": "Should pause", "next": "end"},
            {"id": "end", "type": "end"},
        ],
        kind="sequence",
    )
    row = session.get(AutomationRunORM, __import__("uuid").UUID(run["id"]))
    session.add(
        PipelineEventORM(
            workspace_id=lead.workspace_id,
            lead_id=lead.id,
            stage="gmail_message",
            status="inbound",
            details={"body": "A new reply"},
            created_at=datetime.fromisoformat(row.state["enrolled_at"]),
        )
    )
    session.commit()
    advance_run(session, row, workspace.actor_id)
    assert row.status == "paused" and not list(session.scalars(select(LeadNoteORM)))
    member.status = "suspended"
    row.status = "queued"
    session.commit()
    advance_run(session, row, workspace.actor_id)
    assert row.status == "paused" and "permission" in row.last_error


def test_full_review_requires_another_manager(ops):
    from lead_agent.application.automations import advance_run
    from lead_agent.infrastructure.db.operations import AutomationRunORM

    session, client, workspace, _, lead, *_ = ops
    client.put(
        "/v1/ops/policy",
        json={"version": 0, "configuration": {"approval_tiers": {"note": "full_review"}}},
    )
    run = saved_workflow(
        client,
        lead,
        [
            {"id": "start", "type": "start", "next": "note"},
            {"id": "note", "type": "note", "text": "Requires review", "next": "end"},
            {"id": "end", "type": "end"},
        ],
    )
    row = session.get(AutomationRunORM, __import__("uuid").UUID(run["id"]))
    advance_run(session, row, workspace.actor_id)
    assert row.status == "awaiting_approval", row.last_error
    assert (
        client.post(f"/v1/automations/runs/{row.id}", json={"action": "approve"}).status_code == 403
    )
    assert not list(session.scalars(select(LeadNoteORM)))


def test_job_claims_and_failure_backoff(ops):
    from lead_agent.application.automations import claim_job, periodic_job
    from lead_agent.infrastructure.db.operations import OperationsJobORM

    session, _, workspace, *_ = ops
    assert claim_job(session, workspace.workspace_id, "test")
    assert not claim_job(session, workspace.workspace_id, "test")

    def fail():
        raise ValueError("provider failed")

    assert not periodic_job(session, workspace.workspace_id, "failure", fail, timedelta(seconds=30))
    row = session.get(OperationsJobORM, "failure")
    from lead_agent.application.intelligence import utc

    assert utc(row.next_at) > datetime.now(UTC) + timedelta(minutes=59)
    assert "provider failed" not in row.last_error


def test_company_research_citations_and_stale_review(ops, monkeypatch):
    from types import SimpleNamespace

    from lead_agent.application import intelligence
    from lead_agent.infrastructure.db.operations import CompanyORM

    session, client, workspace, _, lead, *_ = ops
    company = session.get(CompanyORM, attach_contact(session, lead).company_id)
    company.website = "https://acme.test/about"
    company.website_reviewed = True
    session.commit()
    monkeypatch.setattr(
        intelligence,
        "fetch_company_page",
        lambda url: {"url": url, "text": "Acme builds software for teams."},
    )
    result = {
        "summary": "Acme's official page says it builds software.",
        "recommendations": [],
        "facts": [
            {
                "field": "industry",
                "value": "Software",
                "source": company.website,
                "excerpt": "builds software",
            }
        ],
    }
    monkeypatch.setattr(
        intelligence,
        "resolve_workspace_reasoning_provider",
        lambda *_: SimpleNamespace(summarize_evidence=lambda *_: dict(result)),
    )
    insight = intelligence.research_company(session, company, workspace.actor_id)
    assert "research" not in company.profile
    result["facts"] = [
        {
            "field": "industry",
            "value": "Banking",
            "source": company.website,
            "excerpt": "unsupported",
        }
    ]
    with pytest.raises(ValueError, match="unsupported citation"):
        intelligence.research_company(session, company, workspace.actor_id)
    company.website = "https://acme.test/new"
    session.commit()
    assert (
        client.post(
            f"/v1/intelligence/insights/{insight.id}/review", json={"approve": True}
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1"]
)
def test_research_rejects_private_addresses(monkeypatch, address):
    from lead_agent.infrastructure import company_research

    monkeypatch.setattr(
        company_research.socket,
        "getaddrinfo",
        lambda *_a, **_k: [(None, None, None, None, (address, 443))],
    )
    with pytest.raises(ValueError, match="public Internet"):
        company_research.public_address("example.test")


def test_page_reader_rejects_redirect_and_oversized_html(monkeypatch):
    from types import SimpleNamespace

    from lead_agent.infrastructure import company_research

    monkeypatch.setattr(company_research, "public_address", lambda _: "8.8.8.8")
    response = SimpleNamespace(status=302, getheader=lambda _: None)

    class Connection:
        def __init__(self, *_):
            pass

        def request(self, *_, **__):
            pass

        def getresponse(self):
            return response

        def close(self):
            pass

    monkeypatch.setattr(company_research, "PublicHTTPSConnection", Connection)
    with pytest.raises(ValueError, match="redirects"):
        company_research.fetch_company_page("https://example.test")
    response.status = 200
    response.getheader = lambda name: "text/html" if name == "Content-Type" else None
    response.read = lambda _b: b"x" * 8192
    with pytest.raises(ValueError, match="too large"):
        company_research.fetch_company_page("https://example.test")
    parser = company_research.PageText()
    parser.feed("<p>Evidence</p><script>ignore this</script><style>also ignored</style>")
    assert parser.parts == ["Evidence"]


def test_digest_is_weekly_deduplicated_and_requires_manager_approval(ops):
    from lead_agent.application.intelligence import prepare_digest
    from lead_agent.domain.rbac import role_permissions

    session, client, workspace, _, lead, user, *_ = ops
    first = prepare_digest(session, workspace.workspace_id, user.id, workspace.actor_id)
    assert (
        prepare_digest(session, workspace.workspace_id, user.id, workspace.actor_id).id == first.id
    )
    viewer = WorkspaceContext(
        workspace_id=workspace.workspace_id,
        actor_id=str(user.id),
        user_id=user.id,
        role="sales_rep",
        permissions=role_permissions("sales_rep"),
    )
    app.dependency_overrides[get_workspace_context] = lambda: viewer
    assert not client.get("/v1/intelligence/insights?kind=digest").json()["items"]
    app.dependency_overrides[get_workspace_context] = lambda: workspace
    assert (
        client.post(
            f"/v1/intelligence/insights/{first.id}/review", json={"approve": True}
        ).status_code
        == 200
    )
    app.dependency_overrides[get_workspace_context] = lambda: viewer
    assert len(client.get("/v1/intelligence/insights?kind=digest").json()["items"]) == 1


def test_outcome_learning_preserves_component_budgets_and_creates_draft(ops, icp):
    from lead_agent.application.icp import resolve_workspace_icp
    from lead_agent.application.intelligence import approve_learning, propose_learning
    from lead_agent.infrastructure.db.models import (
        LeadORM,
        LeadOutcomeORM,
        QualificationResultORM,
        WorkspaceIcpVersionORM,
    )

    session, _, workspace, _, lead, *_ = ops
    resolved = resolve_workspace_icp(session, workspace.workspace_id, icp)
    weights = resolved.configuration.scoring.component_points["icp_fit"]
    keys = [key for key, points in weights.items() if points > 1]
    assert len(keys) >= 2
    for index in range(20):
        row = LeadORM(
            id=uuid4(),
            workspace_id=workspace.workspace_id,
            source="web",
            full_name=f"Outcome {index}",
            email=f"person{index}@example.test",
            company_name="Example",
            product_or_service="Consulting",
            message="Evidence",
            received_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            raw_payload={},
            normalization_version="test",
        )
        sample = lead.qualification
        row.qualification = QualificationResultORM(
            **{
                column.key: getattr(sample, column.key)
                for column in sample.__table__.columns
                if column.key not in {"id", "lead_id"}
            }
        )
        row.qualification.icp_configuration_hash = resolved.configuration_hash
        row.qualification.score_breakdown = [
            {"component": "icp_fit", "key": keys[0] if index < 10 else keys[1], "points": 3}
        ]
        row.outcome = LeadOutcomeORM(
            workspace_id=workspace.workspace_id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            pipeline_stage="closed_won" if index < 10 else "closed_lost",
        )
        session.add(row)
    session.commit()
    insight = propose_learning(session, workspace.workspace_id, workspace.actor_id, icp)
    assert len(insight.content["deltas"]) == 2
    proposed = insight.content["proposed_configuration"]["scoring"]["component_points"]
    assert sum(proposed["icp_fit"].values()) == sum(weights.values())
    approve_learning(session, insight, workspace, icp)
    assert insight.status == "approved"
    assert (
        session.get(
            WorkspaceIcpVersionORM, __import__("uuid").UUID(insight.content["draft_id"])
        ).status
        == "draft"
    )
    with pytest.raises(ValueError, match="already"):
        approve_learning(session, insight, workspace, icp)


def test_analytics_does_not_mix_currencies_or_invent_missing_costs(ops):
    from lead_agent.application.advanced_analytics import advanced_analytics
    from lead_agent.infrastructure.db.models import LeadOutcomeORM

    session, _, workspace, _, lead, *_ = ops
    lead.outcome = LeadOutcomeORM(
        workspace_id=workspace.workspace_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        pipeline_stage="closed_won",
        closed_revenue=10000,
        currency="EUR",
        closed_at=datetime.now(UTC),
    )
    session.commit()
    report = advanced_analytics(session, workspace.workspace_id)
    assert report["forecast"]["closed_usd_this_month"] == 0
    assert report["forecast"]["expected_open_pipeline_usd"] is None
    assert (
        report["channels"][0]["cost_usd"] is None and report["channels"][0]["roi_percent"] is None
    )


def test_forecast_counts_old_deals_closed_now_and_current_pipeline(ops):
    from lead_agent.application.advanced_analytics import advanced_analytics
    from lead_agent.infrastructure.db.models import LeadOutcomeORM

    session, _, workspace, _, lead, *_ = ops
    now = datetime.now(UTC)
    lead.received_at = now - timedelta(days=500)
    lead.outcome = LeadOutcomeORM(
        workspace_id=workspace.workspace_id,
        created_at=now,
        updated_at=now,
        pipeline_stage="closed_won",
        closed_revenue=1200,
        currency="USD",
        closed_at=now,
    )
    session.commit()
    report = advanced_analytics(session, workspace.workspace_id, months=1)
    assert report["sample_size"] == 0
    assert report["forecast"]["closed_usd_this_month"] == 1200
    lead.outcome.pipeline_stage = "proposal"
    lead.outcome.opportunity_value = 7000
    session.commit()
    assert (
        advanced_analytics(session, workspace.workspace_id, months=1)["forecast"][
            "open_pipeline_usd"
        ]
        == 7000
    )


def test_suggested_draft_window_respects_timezone_weekends_and_observed_hour(ops):
    from lead_agent.application.automations import next_contact_window
    from lead_agent.infrastructure.db.models import PipelineEventORM

    session, _, _, _, lead, *_ = ops
    contact = attach_contact(session, lead)
    contact.timezone = "Asia/Karachi"
    for day in (1, 2, 3):
        session.add(
            PipelineEventORM(
                workspace_id=lead.workspace_id,
                lead_id=lead.id,
                stage="gmail_message",
                status="inbound",
                details={},
                created_at=datetime(2026, 10, day, 9, tzinfo=UTC),
            )
        )
    session.commit()
    chosen, evidence = next_contact_window(
        session,
        lead,
        {"preferred_hour": 9, "optimize_time": True},
        datetime(2026, 10, 9, 10, tzinfo=UTC),
    )
    assert chosen == datetime(2026, 10, 12, 9, tzinfo=UTC)
    assert evidence["hour"] == 14
    assert "observed" in evidence["method"]
    with pytest.raises(ValidationError):
        AutomationInput(
            name="Bad zone",
            timezone="Not/AZone",
            nodes=[{"id": "start", "type": "start", "next": "end"}, {"id": "end", "type": "end"}],
        )


def test_interrupted_processing_needs_review_and_never_repeats_note(ops):
    from uuid import UUID

    from lead_agent.application.automations import poll_operations_once
    from lead_agent.infrastructure.db.operations import AutomationRunORM

    session, client, _, _, lead, *_ = ops
    run = saved_workflow(
        client,
        lead,
        [
            {"id": "start", "type": "start", "next": "note"},
            {"id": "note", "type": "note", "text": "Do not repeat", "next": "end"},
            {"id": "end", "type": "end"},
        ],
    )
    row = session.get(AutomationRunORM, UUID(run["id"]))
    row.status, row.updated_at = "processing", datetime.now(UTC) - timedelta(minutes=20)
    session.commit()
    poll_operations_once(session)
    session.refresh(row)
    assert row.status == "blocked"
    assert "interrupted" in row.last_error
    assert session.scalar(select(func.count()).select_from(LeadNoteORM)) == 0


def test_watcher_rebuilds_evidence_once_instead_of_appending_recursively(ops, monkeypatch):
    from lead_agent.application import services
    from lead_agent.application.automations import watch_lead
    from lead_agent.infrastructure.db.models import PipelineEventORM

    session, _, _, _, lead, *_ = ops
    original = lead.message
    captured = []

    def reprocess(session, lead, payload, *_, **kwargs):
        assert kwargs["send_notification"] is False
        assert kwargs["send_confirmation_email"] is False
        captured.append(payload)
        assert kwargs["preserve_inquiry"] is True
        session.commit()

    monkeypatch.setattr(services, "reprocess_lead", reprocess)
    for index in (1, 2):
        session.add(
            PipelineEventORM(
                workspace_id=lead.workspace_id,
                lead_id=lead.id,
                stage="manual_progress",
                status="recorded",
                details={"note": f"Signal {index}"},
                created_at=datetime.now(UTC) + timedelta(seconds=index),
            )
        )
        session.commit()
        insight = watch_lead(session, lead)
        assert watch_lead(session, lead).id == insight.id
    assert len(captured) == 2
    assert lead.message == original
    assert captured[-1]["message"].count(original) == 1
    assert captured[-1]["message"].count("Signal 1") == 1
    assert captured[-1]["message"].count("Signal 2") == 1


def test_intake_connector_checks_no_lead_data_and_redacts_token(ops, caplog, monkeypatch):
    from lead_agent.infrastructure.db.models import WorkspaceORM, WorkspacePublicIntakeORM
    from lead_agent.infrastructure.db.operations import OperationsPolicyORM

    session, client, workspace, _, *_ = ops
    token = "a" * 43
    session.add(
        WorkspacePublicIntakeORM(
            workspace_id=workspace.workspace_id,
            public_token=token,
            created_by=workspace.user_id,
            created_at=datetime.now(UTC),
        )
    )
    session.commit()
    with caplog.at_level("INFO", logger="lead_agent.http"):
        result = client.get(f"/v1/intake/public/{token}/connection")
    assert result.status_code == 200
    assert set(result.json()) == {"workspace_id", "workspace_name", "connected"}
    logs = [record.fields["path"] for record in caplog.records if hasattr(record, "fields")]
    assert logs and all(token not in value for value in logs)
    assert client.get("/v1/intake/public/unknown/connection").status_code == 404
    session.add(
        OperationsPolicyORM(
            workspace_id=workspace.workspace_id,
            configuration={"required_region": "eu"},
            updated_by=workspace.actor_id,
        )
    )
    session.commit()
    monkeypatch.setenv("CORRAL_HOSTING_REGION", "us")
    assert client.get(f"/v1/intake/public/{token}/connection").status_code == 403
    row = session.get(WorkspaceORM, workspace.workspace_id)
    row.status = "suspended"
    session.commit()
    assert client.get(f"/v1/intake/public/{token}/connection").status_code == 404


def test_identity_relink_preserves_old_contact_and_manual_contact_edits(ops):
    session, _, _, _, lead, *_ = ops
    old = attach_contact(session, lead)
    old.name = "Rep-edited display name"
    session.commit()
    assert attach_contact(session, lead).name == "Rep-edited display name"
    lead.email = "different@acme.test"
    new = attach_contact(session, lead, relink=True)
    session.commit()
    assert new.id != old.id
    assert attach_contact(session, lead).id == new.id
    assert session.get(ContactORM, old.id).name == "Rep-edited display name"


def test_closed_playbook_can_branch_but_closed_sequence_stops_outreach(ops):
    from uuid import UUID

    from lead_agent.application.automations import advance_run
    from lead_agent.infrastructure.db.models import LeadOutcomeORM
    from lead_agent.infrastructure.db.operations import AutomationRunORM

    session, client, workspace, _, lead, *_ = ops
    now = datetime.now(UTC)
    lead.outcome = LeadOutcomeORM(
        workspace_id=workspace.workspace_id,
        created_at=now,
        updated_at=now,
        pipeline_stage="closed_won",
        closed_revenue=1200,
        currency="USD",
        closed_at=now,
    )
    session.commit()
    nodes = [
        {"id": "start", "type": "start", "next": "won"},
        {"id": "won", "type": "condition", "condition": "won", "next": "note", "otherwise": "end"},
        {"id": "note", "type": "note", "text": "Record win handoff", "next": "end"},
        {"id": "end", "type": "end"},
    ]
    result = saved_workflow(client, lead, nodes)
    row = session.get(AutomationRunORM, UUID(result["id"]))
    from lead_agent.infrastructure.db.operations import AutomationDefinitionORM

    session.get(AutomationDefinitionORM, row.definition_id).kind = "sequence"
    session.commit()
    advance_run(session, row, workspace.actor_id)
    assert row.status == "completed"
    assert session.scalar(select(LeadNoteORM.body)) == "Record win handoff"
    result = saved_workflow(
        client,
        lead,
        [
            {"id": "start", "type": "start", "next": "draft"},
            {"id": "draft", "type": "draft", "next": "end"},
            {"id": "end", "type": "end"},
        ],
        kind="sequence",
    )
    row = session.get(AutomationRunORM, UUID(result["id"]))
    advance_run(session, row, workspace.actor_id)
    assert row.status == "completed"
    assert row.state.get("draft_id") is None


def test_uvicorn_access_logs_keep_metadata_without_capability_tokens():
    import logging

    from lead_agent.api.middleware import IntakeAccessLogFilter

    token = "secret-intake-capability"
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        "%s - %s %s %s %s",
        ("127.0.0.1", "GET", f"/v1/intake/public/{token}/connection", "1.1", 200),
        None,
    )
    assert IntakeAccessLogFilter().filter(record)
    assert token not in record.getMessage()
    assert "[redacted]/connection" in record.getMessage()
    assert "GET" in record.getMessage() and "200" in record.getMessage()


def test_sqlite_schedule_responses_keep_utc_offsets(ops):
    session, client, _, _, lead, *_ = ops
    run = saved_workflow(
        client,
        lead,
        [{"id": "start", "type": "start", "next": "end"}, {"id": "end", "type": "end"}],
    )
    session.expire_all()
    items = client.get("/v1/automations/runs/list").json()["items"]
    saved = next(item for item in items if item["id"] == run["id"])
    assert saved["due_at"].endswith("Z") or saved["due_at"].endswith("+00:00")
    contact = client.get("/v1/records/contacts").json()["items"][0]
    assert contact["updated_at"].endswith("Z") or contact["updated_at"].endswith("+00:00")
