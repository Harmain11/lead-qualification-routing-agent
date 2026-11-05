from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from lead_agent.api.routes import analytics, intelligence, lead_assistant
from lead_agent.api.routes.governance import audit_export
from lead_agent.api.routes.routing import retrieve_pipeline_history
from lead_agent.api.workspace_assistant_context import build_workspace_assistant_context
from lead_agent.application.coaching_privacy import personal_data_user_ids
from lead_agent.application.intelligence import coach_lead, prepare_digest
from lead_agent.application.services import process_lead
from lead_agent.application.workspace_retrieval import build_workspace_record_context
from lead_agent.domain.lead_assistant import LeadAssistantReply
from lead_agent.domain.rbac import role_permissions
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    LeadORM,
    LeadOutcomeORM,
    PipelineEventORM,
    UserORM,
    WorkspaceMembershipORM,
)
from lead_agent.infrastructure.db.operations import AgentInsightORM
from lead_agent.infrastructure.db.teams import WorkspaceTeamMemberORM, WorkspaceTeamORM
from lead_agent.infrastructure.repositories import ensure_workspace


@pytest.fixture
def privacy(tmp_path, icp, routing):
    engine = create_engine(f"sqlite:///{tmp_path / 'privacy.db'}")
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    workspace_id, other_id = uuid4(), uuid4()
    contexts, users, memberships, leads = {}, {}, {}, {}
    with Session(engine, expire_on_commit=False) as session:
        ensure_workspace(session, workspace_id)
        ensure_workspace(session, other_id)
        for name, role in (
            ("admin", "admin"),
            ("manager", "sales_manager"),
            ("rep", "sales_rep"),
            ("teammate", "sales_rep"),
            ("outsider", "sales_rep"),
            ("viewer", "viewer"),
        ):
            user = UserORM(
                id=uuid4(),
                email=f"{name}@example.test",
                display_name=name,
                password_hash="test",
                status="active",
                created_at=now,
                updated_at=now,
            )
            member = WorkspaceMembershipORM(
                id=uuid4(),
                workspace_id=workspace_id,
                user_id=user.id,
                role=role,
                status="active",
                created_at=now,
                updated_at=now,
            )
            session.add_all([user, member])
            contexts[name] = WorkspaceContext(
                workspace_id=workspace_id,
                user_id=user.id,
                actor_id=str(user.id),
                membership_id=member.id,
                role=role,
                permissions=role_permissions(role),
            )
            users[name], memberships[name] = user, member
        team = WorkspaceTeamORM(
            id=uuid4(),
            workspace_id=workspace_id,
            name="North",
            name_key="north",
            description="",
            is_default=False,
        )
        session.add(team)
        session.flush()
        for name in ("manager", "rep", "teammate", "viewer"):
            session.add(
                WorkspaceTeamMemberORM(
                    team_id=team.id,
                    workspace_id=workspace_id,
                    membership_id=memberships[name].id,
                )
            )
        session.commit()
        for name in ("rep", "teammate", "outsider"):
            _, _, lead = process_lead(
                session,
                {
                    "external_id": name,
                    "source": "web",
                    "full_name": name,
                    "email": f"lead-{name}@example.test",
                    "company_name": f"{name} Corp",
                    "industry": "Software",
                    "geography": "Pakistan",
                    "product_or_service": "Consulting",
                    "message": f"{name} price is expensive; demo needed.",
                },
                icp,
                routing,
                workspace_id=workspace_id,
            )
            route = next(row for row in lead.routing_decisions if row.is_current)
            route.assigned_rep_id, route.assigned_rep_name = str(users[name].id), name
            session.add(
                LeadOutcomeORM(
                    workspace_id=workspace_id,
                    lead_id=lead.id,
                    pipeline_stage="closed_won",
                    opportunity_value=100,
                    closed_revenue=100,
                    currency="USD",
                    data_source="manual",
                    credited_user_id=users[name].id,
                    opened_at=now,
                    closed_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
            session.refresh(lead)
            leads[name] = lead.id
    yield SimpleNamespace(
        engine=engine,
        contexts=contexts,
        users=users,
        memberships=memberships,
        leads=leads,
        workspace_id=workspace_id,
        other_id=other_id,
        now=now,
    )
    engine.dispose()


def _insight(
    session,
    store,
    name,
    *,
    kind="coach",
    owner="rep",
    status="pending_review",
    legacy=False,
    other_workspace=False,
    created_at=None,
):
    content = {"summary": name}
    if kind == "coach" and not legacy:
        content["owner_id"] = str(store.users[owner].id) if owner else None
    if kind == "digest" and owner:
        content["recipient_id"] = str(store.users[owner].id)
    row = AgentInsightORM(
        workspace_id=store.other_id if other_workspace else store.workspace_id,
        lead_id=None if kind != "coach" or other_workspace else store.leads[owner or "rep"],
        kind=kind,
        fingerprint=uuid4().hex,
        status=status,
        content=content,
        created_by=str(store.users[owner].id) if owner else "coach",
        created_at=created_at or store.now,
    )
    session.add(row)
    session.commit()
    return row


@pytest.mark.parametrize(
    ("actor", "expected"),
    [
        ("rep", {"own", "legacy", "approved", "shared"}),
        ("manager", {"own", "legacy", "approved", "pending", "teammate", "shared"}),
        ("viewer", {"shared"}),
        (
            "admin",
            {"own", "legacy", "approved", "pending", "teammate", "outside", "orphan", "shared"},
        ),
    ],
)
def test_persisted_insights_scope_and_pagination(privacy, actor, expected):
    p = privacy
    with Session(p.engine) as session:
        _insight(session, p, "outside", owner="outsider", created_at=p.now + timedelta(seconds=3))
        _insight(session, p, "own", created_at=p.now + timedelta(seconds=2))
        _insight(session, p, "teammate", owner="teammate")
        _insight(session, p, "legacy", legacy=True)
        _insight(session, p, "orphan", owner=None, legacy=True)
        _insight(session, p, "approved", kind="digest", status="approved")
        _insight(session, p, "pending", kind="digest")
        _insight(session, p, "shared", kind="research")
        _insight(session, p, "foreign", other_workspace=True)
    # Reopen the store: authorization must work on persisted records, not creator state.
    with Session(p.engine) as session:
        rows = intelligence.insights("all", 100, 0, session, p.contexts[actor])["items"]
        assert {row.content["summary"] for row in rows} == expected
        page = intelligence.insights("all", 1, 0, session, p.contexts[actor])["items"]
        assert page[0].content["summary"] == (
            "outside" if actor == "admin" else "shared" if actor == "viewer" else "own"
        )


def test_generation_snapshots_owner_and_does_not_write_shared_history(privacy):
    p = privacy
    with Session(p.engine) as session:
        first = intelligence.coach(p.leads["rep"], session, p.contexts["rep"])
        assert first.content["owner_id"] == str(p.users["rep"].id)
        assert not list(
            session.scalars(
                select(PipelineEventORM).where(
                    PipelineEventORM.stage == "coach",
                )
            )
        )
        for actor, target in (("rep", "teammate"), ("manager", "outsider"), ("viewer", "rep")):
            with pytest.raises(HTTPException) as error:
                intelligence.coach(p.leads[target], session, p.contexts[actor])
            assert error.value.status_code == 404
        lead = session.get(LeadORM, p.leads["rep"])
        route = next(row for row in lead.routing_decisions if row.is_current)
        route.assigned_rep_id = str(p.users["teammate"].id)
        session.commit()
        second = coach_lead(session, lead, p.contexts["admin"].actor_id)
        assert first.id != second.id
        assert second.content["owner_id"] == str(p.users["teammate"].id)
        own = intelligence.insights("coach", 100, 0, session, p.contexts["rep"])["items"]
        assert [row.id for row in own] == [first.id]


def test_objections_reviews_and_digest_targets_are_private(privacy, icp, routing):
    p = privacy
    with Session(p.engine) as session:
        for name in ("rep", "teammate", "outsider"):
            coach_lead(session, session.get(LeadORM, p.leads[name]), p.contexts["admin"].actor_id)
        for actor, allowed in (
            ("rep", {"rep"}),
            ("manager", {"rep", "teammate"}),
            ("viewer", set()),
        ):
            items = intelligence.objection_library(session, p.contexts[actor])["items"]
            assert {item["lead_id"] for item in items} == {p.leads[name] for name in allowed}
        outside = prepare_digest(session, p.workspace_id, p.users["outsider"].id, "coach")
        with pytest.raises(HTTPException) as error:
            intelligence.review(
                outside.id,
                intelligence.InsightReview(approve=True),
                session,
                (icp, routing),
                p.contexts["manager"],
            )
        assert error.value.status_code == 404
        assert session.get(AgentInsightORM, outside.id).status == "pending_review"
        with pytest.raises(HTTPException) as error:
            intelligence.digest(
                intelligence.DigestInput(user_id=p.users["outsider"].id),
                session,
                p.contexts["manager"],
            )
        assert error.value.status_code == 404
        own = intelligence.digest(
            intelligence.DigestInput(user_id=p.users["rep"].id), session, p.contexts["manager"]
        )
        assert own.content["recipient_id"] == str(p.users["rep"].id)


@pytest.mark.parametrize(
    ("actor", "expected"),
    [
        ("rep", {"rep"}),
        ("manager", {"manager", "rep", "teammate", "viewer"}),
        ("viewer", set()),
        ("admin", {"admin", "manager", "rep", "teammate", "outsider", "viewer"}),
    ],
)
def test_performance_projections_and_shared_totals(privacy, actor, expected):
    p = privacy
    with Session(p.engine) as session:
        workspace = p.contexts[actor]
        report = analytics.reps("monthly", session, workspace)
        assert {item["display_name"] for item in report["members"]} == expected
        assert {item["rep_name"] for item in report["reps"]} == expected & {
            "rep",
            "teammate",
            "outsider",
        }
        revenue = analytics.revenue("monthly", session, workspace)
        assert revenue["total_revenue"] == 300  # Permitted shared aggregate remains available.
        assert {item["label"] for item in revenue["by_rep"]} == expected & {
            "rep",
            "teammate",
            "outsider",
        }
        advanced = intelligence.analytics(6, session, workspace)
        assert {item["name"] for item in advanced["members"]} == expected
        extended = analytics.extended("monthly", session, workspace)
        assert {item["rep"] for item in extended["team_utilization_trend"]} == expected & {
            "rep",
            "teammate",
            "outsider",
        }


def test_credit_overrides_assignment_and_same_names_do_not_merge_private_metrics(privacy):
    p = privacy
    with Session(p.engine) as session:
        lead = session.get(LeadORM, p.leads["rep"])
        lead.outcome.credited_user_id = p.users["outsider"].id
        session.commit()
        report = analytics.reps("monthly", session, p.contexts["rep"])
        assert report["reps"] == []
        assert report["members"][0]["closed_leads"] == 0
        assert analytics.revenue("monthly", session, p.contexts["rep"])["by_rep"] == []


def test_legacy_private_history_never_reaches_assistants_or_shared_activity(privacy, monkeypatch):
    p = privacy
    sentinel = "PRIVATE_COACHING_SENTINEL"
    captured = {}

    class Provider:
        def assist_with_lead(self, payload):
            captured.update(payload)
            return LeadAssistantReply(reply="Read the recorded inquiry.")

    monkeypatch.setattr(
        lead_assistant, "resolve_workspace_reasoning_provider", lambda *_: Provider()
    )
    with Session(p.engine) as session:
        for stage in ("coach", "digest"):
            session.add(
                PipelineEventORM(
                    workspace_id=p.workspace_id,
                    lead_id=p.leads["rep"],
                    stage=stage,
                    status="pending_review",
                    details={"summary": sentinel},
                    created_at=p.now,
                )
            )
        session.commit()
        lead_assistant.chat_with_lead_assistant(
            p.leads["rep"],
            lead_assistant.AssistantChatRequest(
                messages=[
                    lead_assistant.AssistantMessage(role="user", content="Show history"),
                ]
            ),
            session,
            p.contexts["rep"],
        )
        assert sentinel not in json.dumps(captured, default=str)
        for query in (sentinel, "rep pricing", "show recent email"):
            context = build_workspace_record_context(session, p.workspace_id, query)
            # The retrieval envelope echoes the caller's query/search terms;
            # only persisted record payloads must be free of private history.
            record_payload = {
                key: value for key, value in context.items()
                if key not in {"query", "search_terms"}
            }
            assert sentinel not in json.dumps(record_payload, default=str)
        activity = analytics.agent_activity("monthly", session, p.contexts["viewer"])
        assert sentinel not in json.dumps(activity, default=str)
        history = retrieve_pipeline_history(p.leads["rep"], session, p.contexts["viewer"])
        assert sentinel not in json.dumps([row.model_dump() for row in history], default=str)
        snapshot = build_workspace_assistant_context(session, p.contexts["viewer"])
        assert sentinel not in json.dumps(snapshot, default=str)


def test_export_filters_coaching_and_viewer_cannot_narrate(privacy):
    p = privacy
    with Session(p.engine) as session:
        own = coach_lead(session, session.get(LeadORM, p.leads["rep"]), "coach")
        export = audit_export(p.leads["rep"], session, p.contexts["viewer"])
        assert json.loads(export.body)["insights"] == []
        export = audit_export(p.leads["rep"], session, p.contexts["rep"])
        assert [row["id"] for row in json.loads(export.body)["insights"]] == [str(own.id)]
        with pytest.raises(HTTPException) as error:
            intelligence.narrate(6, session, p.contexts["viewer"])
        assert error.value.status_code == 403


def test_manager_without_team_fails_closed_to_self(privacy):
    p = privacy
    with Session(p.engine) as session:
        for row in session.scalars(
            select(WorkspaceTeamMemberORM).where(
                WorkspaceTeamMemberORM.membership_id == p.memberships["manager"].id,
            )
        ):
            session.delete(row)
        session.commit()
        assert personal_data_user_ids(session, p.contexts["manager"]) == frozenset(
            {str(p.users["manager"].id)}
        )
