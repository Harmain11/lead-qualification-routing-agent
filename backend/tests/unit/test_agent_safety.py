from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.routes import lead_assistant
from lead_agent.api.routes.lead_assistant import AssistantChatRequest, AssistantMessage
from lead_agent.api.workspace_assistant_context import build_workspace_assistant_context
from lead_agent.api import workspace_assistant_context as snapshots
from lead_agent.api.workspace_lead_actions import validate_lead_proposal
from lead_agent.domain.lead_assistant import (
    LeadAssistantAction,
    LeadAssistantReply,
    WorkspaceAssistantAction,
    WorkspaceAssistantReply,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import WorkspaceORM
from lead_agent.domain.rbac import PERMISSION_CATALOG


@pytest.mark.parametrize("permissions", [{"agents.execute"}, {"leads.view"}, set()])
def test_lead_chat_requires_both_permissions_before_reading_lead(monkeypatch, permissions):
    app = FastAPI()
    app.include_router(lead_assistant.router)
    workspace = WorkspaceContext(workspace_id=uuid4(), actor_id="qa", permissions=frozenset(permissions))
    app.dependency_overrides[get_workspace_context] = lambda: workspace
    app.dependency_overrides[get_db] = lambda: object()
    read = MagicMock()
    monkeypatch.setattr(lead_assistant, "get_lead", read)
    with TestClient(app) as client:
        response = client.post(f"/v1/leads/{uuid4()}/assistant/chat", json={
            "messages": [{"role": "user", "content": "Summarize this lead"}],
        })
    assert response.status_code == 403
    read.assert_not_called()


def test_context_without_view_permissions_never_reads_database():
    context = build_workspace_assistant_context(
        object(), WorkspaceContext(workspace_id=uuid4(), actor_id="qa", permissions=frozenset({"agents.execute"})),
    )
    assert context == {"permissions": ["agents.execute"], "available_areas": [], "context_warnings": []}


def test_trusted_context_can_load_all_permitted_sections_from_database():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    workspace_id = uuid4()
    with Session(engine) as session:
        now = datetime.now(UTC)
        session.add(WorkspaceORM(
            id=workspace_id, name="Test workspace", slug="agent-test",
            created_at=now, updated_at=now,
        ))
        session.commit()
        context = build_workspace_assistant_context(session, WorkspaceContext(
            workspace_id=workspace_id, actor_id="qa", permissions=PERMISSION_CATALOG,
        ))
    assert context["context_warnings"] == []
    for key in (
        "analytics_monthly", "automations", "icp_versions", "routing_settings",
        "workspace_profile", "integrations", "team", "lead_closing_options",
    ):
        assert key in context
    assert "business_profile" in context["workspace_profile"]


def test_context_reports_a_failed_read_without_fabricating_metrics(monkeypatch):
    monkeypatch.setattr(snapshots, "dashboard", MagicMock(side_effect=RuntimeError("offline")))
    session = SimpleNamespace(begin_nested=nullcontext)
    context = build_workspace_assistant_context(session, WorkspaceContext(
        workspace_id=uuid4(), actor_id="qa", permissions=frozenset({"agents.execute", "analytics.view"}),
    ))
    assert "analytics_monthly" not in context
    assert any("analytics_monthly is unavailable" in item for item in context["context_warnings"])


def test_context_reads_server_data_and_bounds_large_snapshots(monkeypatch):
    monkeypatch.setattr(snapshots, "dashboard", lambda *_: {"metrics": [{"value": 42}]})
    monkeypatch.setattr(snapshots, "versions", lambda *_: [{"name": str(i), "configuration": "x" * 4000} for i in range(10)])
    context = build_workspace_assistant_context(
        SimpleNamespace(begin_nested=nullcontext),
        WorkspaceContext(workspace_id=uuid4(), actor_id="qa", permissions=frozenset({"analytics.view", "icp.view"})),
    )
    assert context["analytics_monthly"]["metrics"][0]["value"] == 42
    assert len(json.dumps(context)) <= 24000
    assert context["context_warnings"]


@pytest.mark.parametrize(("kind", "payload"), [
    ("update_lead_profile", {"phone": "+442079460000", "job_title": "Director"}),
    ("update_lead_profile", {"phone": None}),
    ("add_lead_note", {"note": "Customer requested a proposal next week."}),
    ("record_lead_progress", {"stage": "responded", "note": "Customer replied to the proposal."}),
    ("record_lead_progress", {"stage": "converted", "note": "Signed project contract.", "conversion_type": "project", "credited_user_id": str(uuid4())}),
    ("reevaluate_lead", None),
])
def test_lead_proposals_are_typed_without_executing_writes(kind, payload):
    action = WorkspaceAssistantAction(kind=kind, target_id=str(uuid4()), payload_json=json.dumps(payload))
    validate_lead_proposal(action)
    if payload is not None:
        assert json.loads(action.payload_json) == payload
    else:
        assert action.payload_json is None


@pytest.mark.parametrize(("kind", "payload"), [
    ("update_lead_profile", {"workspace_id": str(uuid4()), "phone": "123"}),
    ("update_lead_profile", {"email": None}),
    ("update_lead_profile", {}),
    ("add_lead_note", {"note": "  "}),
    ("record_lead_progress", {"stage": "invented", "note": "No such stage"}),
    ("record_lead_progress", {"stage": "converted", "note": "Won the account"}),
    ("record_lead_progress", {"stage": "converted", "note": "Won the account", "conversion_type": "sale"}),
    ("record_lead_progress", {"stage": "converted", "note": "Won the account", "conversion_type": "sale", "credited_user_id": str(uuid4()), "send_direct_message": True}),
])
def test_invalid_or_unsafe_lead_proposals_are_rejected(kind, payload):
    with pytest.raises(ValueError):
        validate_lead_proposal(WorkspaceAssistantAction(kind=kind, payload_json=json.dumps(payload)))


@pytest.mark.parametrize(("can_edit", "target_matches", "exists", "expected"), [
    (True, True, True, "update_lead_profile"),
    (False, True, True, "none"),
    (True, False, True, "none"),
    (True, True, False, "none"),
])
def test_chat_only_proposes_writes_for_verified_accessible_leads(monkeypatch, can_edit, target_matches, exists, expected):
    lead_id = uuid4()
    action = WorkspaceAssistantAction(
        kind="update_lead_profile", target_id=str(lead_id), target_label="Untrusted label",
        payload_json='{"phone": "+442079460000"}',
    )
    monkeypatch.setattr(lead_assistant, "build_workspace_assistant_context", lambda *_: {})
    monkeypatch.setattr(lead_assistant, "build_workspace_record_context", lambda *_: {
        "matched_leads": [{"id": str(lead_id), "name": "Verified lead"}] if target_matches else [],
    })
    monkeypatch.setattr(lead_assistant, "resolve_workspace_reasoning_provider", lambda *_: SimpleNamespace(
        assist_with_workspace=lambda _: WorkspaceAssistantReply(reply="Review this lead update.", action=action),
    ))
    session = MagicMock()
    session.scalar.return_value = lead_id if exists else None
    permissions = {"agents.execute", "leads.view"} | ({"leads.edit"} if can_edit else set())
    result = lead_assistant.chat_with_workspace_assistant(
        AssistantChatRequest(messages=[AssistantMessage(role="user", content="Update this lead's phone")]),
        session, WorkspaceContext(workspace_id=uuid4(), actor_id="qa", permissions=frozenset(permissions)),
    )
    assert result["action"]["kind"] == expected
    if expected != "none":
        assert result["action"]["target_label"] == "Verified lead"
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_record_search_failure_is_visible_and_blocks_lead_proposals(monkeypatch):
    monkeypatch.setattr(lead_assistant, "build_workspace_assistant_context", lambda *_: {})
    monkeypatch.setattr(lead_assistant, "build_workspace_record_context", MagicMock(side_effect=RuntimeError("offline")))
    monkeypatch.setattr(lead_assistant, "resolve_workspace_reasoning_provider", lambda *_: SimpleNamespace(
        assist_with_workspace=lambda _: WorkspaceAssistantReply(
            reply="Propose update", action=WorkspaceAssistantAction(kind="add_lead_note", target_id=str(uuid4()), payload_json='{"note":"test"}'),
        ),
    ))
    result = lead_assistant.chat_with_workspace_assistant(
        AssistantChatRequest(messages=[AssistantMessage(role="user", content="Add a lead note")]),
        SimpleNamespace(begin_nested=nullcontext),
        WorkspaceContext(workspace_id=uuid4(), actor_id="qa", permissions=frozenset({"agents.execute", "leads.view", "leads.edit"})),
    )
    assert result["action"]["kind"] == "none"
    assert any("record search is unavailable" in warning for warning in result["context_warnings"])


@pytest.mark.parametrize(
    ("permissions", "phone", "expected"),
    [
        ({"agents.execute", "leads.view", "dialer.call"}, "+1 415 555 0100", "open_dialer"),
        ({"agents.execute", "leads.view"}, "+1 415 555 0100", "none"),
        ({"agents.execute", "leads.view", "dialer.call"}, None, "none"),
    ],
)
def test_lead_call_suggestion_requires_dialer_permission_and_saved_phone(
    monkeypatch, permissions, phone, expected
):
    lead_id = uuid4()
    lead = SimpleNamespace(
        id=lead_id,
        full_name="Kathi",
        email=None,
        phone=phone,
        job_title=None,
        company_name=None,
        industry=None,
        employee_count=None,
        annual_revenue=None,
        geography=None,
        budget=None,
        product_or_service=None,
        message="",
        source="website",
        received_at=datetime.now(UTC),
        qualification=None,
        outcome=None,
    )
    monkeypatch.setattr(lead_assistant, "get_lead", lambda *_: lead)
    monkeypatch.setattr(
        lead_assistant,
        "operations_policy",
        lambda *_: SimpleNamespace(
            business_profile=SimpleNamespace(model_dump=lambda **_: {})
        ),
    )
    provider_payload = {}

    def assist(payload):
        provider_payload.update(payload)
        return LeadAssistantReply(
            reply="I can open the phone for you.",
            action=LeadAssistantAction(kind="open_dialer"),
        )

    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_: SimpleNamespace(assist_with_lead=assist),
    )
    session = MagicMock()
    session.scalars.return_value = []
    workspace = WorkspaceContext(
        workspace_id=uuid4(),
        actor_id="qa",
        permissions=frozenset(permissions),
    )
    result = lead_assistant.chat_with_lead_assistant(
        lead_id,
        AssistantChatRequest(
            messages=[AssistantMessage(role="user", content="I want to call Kathi")]
        ),
        session,
        workspace,
    )

    assert result["action"]["kind"] == expected
    assert provider_payload["permissions"] == sorted(permissions)
