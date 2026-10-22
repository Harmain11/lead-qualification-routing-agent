from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from lead_agent.api.routes import lead_assistant
from lead_agent.api.routes.lead_assistant import (
    AssistantChatRequest,
    AssistantMessage,
    chat_with_workspace_assistant,
)
from lead_agent.domain.lead_assistant import (
    WorkspaceAssistantAction,
    WorkspaceAssistantReply,
)
from lead_agent.domain.workspaces import WorkspaceContext


def test_workspace_chat_returns_provider_answer_and_conversation(monkeypatch) -> None:
    workspace_id = uuid4()
    captured: dict[str, object] = {}

    class Provider:
        def assist_with_workspace(self, payload):
            captured.update(payload)
            return WorkspaceAssistantReply(
                reply="I can turn off automatic confirmations.",
                action=WorkspaceAssistantAction(
                    kind="set_lead_confirmation",
                    title="Turn off automatic confirmations",
                    summary="Stop sending automatic lead confirmation emails.",
                    enabled=False,
                ),
            )

    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda _session, _workspace_id: Provider(),
    )
    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[
                AssistantMessage(role="user", content="Generate discovery questions.")
            ],
            context={"lead_confirmation_setting": {"enabled": True}},
        ),
        session=object(),
        workspace=WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute"}),
        ),
    )
    assert result["reply"] == "I can turn off automatic confirmations."
    assert result["action"]["kind"] == "set_lead_confirmation"
    assert captured["workspace_id"] == str(workspace_id)
    assert captured["messages"] == [
        {"role": "user", "content": "Generate discovery questions."},
    ]
    assert "lead_confirmation_setting" not in captured["workspace_context"]
    assert captured["workspace_context"]["permissions"] == ["agents.execute"]


def test_workspace_chat_uses_minimal_context_for_standalone_thanks(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Provider:
        def assist_with_workspace(self, payload):
            captured.update(payload)
            return WorkspaceAssistantReply(reply="You're welcome.")

    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_args: Provider(),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_assistant_context",
        lambda *_args: pytest.fail("workspace snapshot should not be loaded"),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        lambda *_args: pytest.fail("record search should not be run"),
    )
    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[
                AssistantMessage(role="user", content="Please draft a follow-up."),
                AssistantMessage(role="assistant", content="Here is a draft."),
                AssistantMessage(role="user", content="Thanks!"),
            ]
        ),
        session=object(),
        workspace=WorkspaceContext(
            workspace_id=uuid4(),
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view"}),
        ),
    )
    assert result["reply"] == "You're welcome."
    assert captured["messages"] == [{"role": "user", "content": "Thanks!"}]
    assert captured["workspace_context"] == {
        "permissions": ["agents.execute", "leads.view"],
        "context_warnings": [],
    }


def test_workspace_chat_requires_a_supported_ai_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda _session, _workspace_id: SimpleNamespace(),
    )
    with pytest.raises(HTTPException) as error:
        chat_with_workspace_assistant(
            AssistantChatRequest(
                messages=[AssistantMessage(role="user", content="Explain the dashboard.")]
            ),
            session=object(),
            workspace=WorkspaceContext(
                workspace_id=uuid4(),
                actor_id="qa-user",
                permissions=frozenset({"agents.execute"}),
            ),
        )
    assert error.value.status_code == 503


def test_workspace_chat_retrieves_records_only_with_lead_view_permission(monkeypatch) -> None:
    workspace_id = uuid4()
    captured: dict[str, object] = {}
    retrieved: dict[str, object] = {"matched_leads": [{"id": "trusted-lead"}]}

    class Provider:
        def assist_with_workspace(self, payload):
            captured.update(payload)
            return WorkspaceAssistantReply(reply="Found the matching record.")

    def fake_retrieval(session, current_workspace_id, query):
        assert session is db_session
        assert current_workspace_id == workspace_id
        assert query == "Find Mina's recent calls."
        return retrieved

    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_args: Provider(),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        fake_retrieval,
    )

    db_session = SimpleNamespace(begin_nested=nullcontext)
    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[
                AssistantMessage(role="user", content="Find Mina's recent calls.")
            ],
            context={"workspace_records": {"matched_leads": [{"id": "spoofed"}]}},
        ),
        session=db_session,
        workspace=WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view"}),
        ),
    )

    assert result["reply"] == "Found the matching record."
    assert captured["workspace_context"]["workspace_records"] == retrieved


def test_workspace_chat_only_opens_a_record_returned_by_scoped_search(monkeypatch) -> None:
    workspace_id = uuid4()
    lead_id = uuid4()
    matched = {"matched_leads": [{"id": str(lead_id), "name": "Mina Chen"}]}

    class Provider:
        def assist_with_workspace(self, _payload):
            return WorkspaceAssistantReply(
                reply="Open the matching lead to continue.",
                action=WorkspaceAssistantAction(
                    kind="open_lead",
                    title="Open Mina Chen",
                    summary="Open this lead to continue with lead-specific actions.",
                    target_id=str(lead_id),
                    target_label="Mina Chen",
                ),
            )

    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_args: Provider(),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        lambda *_args: matched,
    )
    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[AssistantMessage(role="user", content="Open Mina Chen's lead.")]
        ),
        session=SimpleNamespace(scalar=lambda _query: lead_id, begin_nested=nullcontext),
        workspace=WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view"}),
        ),
    )
    assert result["action"]["kind"] == "open_lead"
    assert result["action"]["target_id"] == str(lead_id)


def test_workspace_chat_resolves_unique_partial_lead_name_for_note_proposal(
    monkeypatch,
) -> None:
    workspace_id = uuid4()
    lead_id = uuid4()
    matched = {
        "matched_leads": [
            {"id": str(lead_id), "name": "Kethi Ahmed", "email": "kethi@example.test"}
        ]
    }

    class Provider:
        def assist_with_workspace(self, _payload):
            return WorkspaceAssistantReply(
                reply="I can add that note to Kethi Ahmed.",
                action=WorkspaceAssistantAction(
                    kind="add_lead_note",
                    title="Add lead note",
                    summary="Record the information on this lead.",
                    payload_json='{"note":"Revisit the project next month."}',
                ),
            )

    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_args: Provider(),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        lambda *_args: matched,
    )
    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[
                AssistantMessage(
                    role="user",
                    content="Add a note to Kethi that we should revisit the project next month.",
                )
            ]
        ),
        session=SimpleNamespace(
            scalar=lambda _query: lead_id,
            begin_nested=nullcontext,
        ),
        workspace=WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view", "leads.edit"}),
        ),
    )

    assert result["action"]["kind"] == "add_lead_note"
    assert result["action"]["target_id"] == str(lead_id)
    assert result["action"]["target_label"] == "Kethi Ahmed"


def test_workspace_chat_resolves_unique_partial_name_for_lead_search(monkeypatch) -> None:
    workspace_id = uuid4()
    lead_id = uuid4()

    class Provider:
        def assist_with_workspace(self, _payload):
            return WorkspaceAssistantReply(
                reply="I found one matching lead.",
                action=WorkspaceAssistantAction(
                    kind="open_lead",
                    title="Open matching lead",
                    summary="Open the matching lead.",
                ),
            )

    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_args: Provider(),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        lambda *_args: {
            "matched_leads": [
                {"id": str(lead_id), "name": "Kethi Ahmed", "email": "kethi@example.test"}
            ]
        },
    )
    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[
                AssistantMessage(role="user", content="Is there a Kethi in my leads?")
            ]
        ),
        session=SimpleNamespace(
            scalar=lambda _query: lead_id,
            begin_nested=nullcontext,
        ),
        workspace=WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view"}),
        ),
    )

    assert result["action"]["kind"] == "open_lead"
    assert result["action"]["target_id"] == str(lead_id)


def test_workspace_chat_does_not_guess_between_partial_name_matches(monkeypatch) -> None:
    workspace_id = uuid4()
    first_lead_id = uuid4()

    class Provider:
        def assist_with_workspace(self, _payload):
            return WorkspaceAssistantReply(
                reply="Open the matching lead.",
                action=WorkspaceAssistantAction(
                    kind="open_lead",
                    title="Open matching lead",
                    summary="Open the matching lead.",
                ),
            )

    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_args: Provider(),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        lambda *_args: {
            "matched_leads": [
                {"id": str(first_lead_id), "name": "Kethi Ahmed"},
                {"id": str(uuid4()), "name": "Kethi Iqbal"},
            ]
        },
    )
    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[AssistantMessage(role="user", content="Open Kethi's lead.")]
        ),
        session=SimpleNamespace(
            scalar=lambda _query: first_lead_id,
            begin_nested=nullcontext,
        ),
        workspace=WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view"}),
        ),
    )

    assert result["action"]["kind"] == "none"


def test_workspace_chat_rejects_unmatched_open_lead_proposal(monkeypatch) -> None:
    workspace_id = uuid4()
    lead_id = uuid4()

    class Provider:
        def assist_with_workspace(self, _payload):
            return WorkspaceAssistantReply(
                reply="Open this lead.",
                action=WorkspaceAssistantAction(
                    kind="open_lead",
                    title="Open lead",
                    summary="Open the lead.",
                    target_id=str(lead_id),
                    target_label="Unknown lead",
                ),
            )

    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_args: Provider(),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        lambda *_args: {"matched_leads": []},
    )
    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[AssistantMessage(role="user", content="Open some lead.")]
        ),
        session=SimpleNamespace(scalar=lambda _query: lead_id, begin_nested=nullcontext),
        workspace=WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view"}),
        ),
    )
    assert result["action"]["kind"] == "none"


def test_workspace_email_action_keeps_the_draft_fields_typed_and_editable() -> None:
    action = WorkspaceAssistantAction(
        kind="send_email",
        title="Review email before sending",
        summary="Check the recipient and message before you send.",
        email_recipient="pat@example.test",
        email_cc="sales@example.test",
        email_bcc="archive@example.test",
        email_subject="Following up",
        email_body="Hello Pat,\n\nHere is the information you requested.",
    )

    assert action.kind == "send_email"
    assert action.email_recipient == "pat@example.test"
    assert action.email_cc == "sales@example.test"
    assert action.email_bcc == "archive@example.test"
    assert action.email_subject == "Following up"
    assert "Hello Pat" in action.email_body


def test_dialer_navigation_is_a_typed_workspace_action() -> None:
    action = WorkspaceAssistantAction(
        kind="open_module",
        area="dialer",
        title="Open Dialer",
        summary="Choose a lead or number and place the call yourself.",
    )

    assert action.kind == "open_module"
    assert action.area == "dialer"
