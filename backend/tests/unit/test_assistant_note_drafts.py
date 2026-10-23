from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from lead_agent.api.routes.lead_assistant import (
    AssistantMessage,
    _is_conversation_only_message,
    _lead_assistant_links,
    _wants_lead_links,
)
from lead_agent.api.workspace_lead_actions import validate_lead_proposal
from lead_agent.domain.lead_assistant import LeadAssistantAction, WorkspaceAssistantAction


def test_note_only_profile_action_routes_to_note_save():
    action = LeadAssistantAction(kind="update_profile", note="Customer asked for pricing.")
    assert action.kind == "add_note"
    assert action.note == "Customer asked for pricing."
    # Do not silently drop actual profile changes or explicit clears.
    action = LeadAssistantAction(
        kind="update_profile", note="Context", profile_updates={"phone": "+442079460000"},
    )
    assert action.kind == "update_profile"
    assert LeadAssistantAction(
        kind="update_profile", note="Context", clear_fields=["phone"],
    ).kind == "update_profile"


@pytest.mark.parametrize("kind", ["update_lead_profile", "add_lead_note"])
@pytest.mark.parametrize("field", ["note", "body", "comment"])
def test_workspace_narrative_proposal_uses_note_endpoint(kind, field):
    import json

    action = WorkspaceAssistantAction(
        kind=kind, payload_json=json.dumps({field: "  Customer requested a demo.  "}),
    )
    validate_lead_proposal(action)
    assert action.kind == "add_lead_note"
    assert json.loads(action.payload_json) == {"note": "Customer requested a demo."}


def test_invalid_note_is_not_disguised_as_profile_update():
    action = WorkspaceAssistantAction(
        kind="update_lead_profile", payload_json='{"note":"Context","phone":"123"}',
    )
    with pytest.raises(ValidationError):
        validate_lead_proposal(action)


@pytest.mark.parametrize("query", [
    "Thanks", "done", "Sorry, my mistake", "Summarize this lead",
    "What details are still missing?", "What details are missing for this lead?",
    "Draft a comment for this lead",
    "Why is Mina Chen interested?", "What was the last note?",
])
def test_normal_answers_do_not_attach_lead_buttons(query):
    messages = [AssistantMessage(role="user", content=query)]
    assert not _wants_lead_links(messages)
    # Mentioning the current lead in an answer must not produce a button.
    assert _lead_assistant_links(
        SimpleNamespace(), SimpleNamespace(), messages,
        SimpleNamespace(id=uuid4(), full_name="Mina Chen", company_name="Acme"),
        "Mina Chen requested a demo.",
    ) == []


@pytest.mark.parametrize("query", [
    "Find Mina Chen", "Show this lead", "Open the lead record",
    "Is there any lead named Mina?", "List leads at Acme",
])
def test_explicit_lead_requests_can_include_buttons(query):
    assert _wants_lead_links([AssistantMessage(role="user", content=query)])


@pytest.mark.parametrize("latest", ["done", "thanks", "sorry my mistake"])
def test_acknowledgements_do_not_resurrect_previous_lead_search(latest):
    messages = [
        AssistantMessage(role="user", content="Find Mina Chen"),
        AssistantMessage(role="assistant", content="I found a possible lead match."),
        AssistantMessage(role="user", content=latest),
    ]
    assert not _wants_lead_links(messages)
    assert _is_conversation_only_message(messages)


def test_specific_correction_remains_actionable():
    assert not _is_conversation_only_message([
        AssistantMessage(role="user", content="Sorry, correct the phone to +442079460000"),
    ])


@pytest.mark.parametrize("latest", [
    "Sorry, my mistake", "What details are missing for this lead?",
])
def test_old_search_matches_do_not_replace_contextual_answer(monkeypatch, latest):
    from contextlib import nullcontext
    from lead_agent.api.routes import lead_assistant
    from lead_agent.domain.lead_assistant import WorkspaceAssistantReply
    from lead_agent.domain.workspaces import WorkspaceContext

    candidates = [{"id": str(uuid4()), "name": "Mina Chen", "company": "Acme"}]
    monkeypatch.setattr(
        lead_assistant, "build_workspace_assistant_context", lambda *_: {},
    )
    monkeypatch.setattr(
        lead_assistant, "build_workspace_record_context",
        lambda *_: {"matched_leads": [], "similar_leads": candidates},
    )
    captured = {}

    def answer(payload):
        captured.update(payload)
        return WorkspaceAssistantReply(reply="Please clarify the detail you meant; no change was made.")

    monkeypatch.setattr(
        lead_assistant, "resolve_workspace_reasoning_provider",
        lambda *_: SimpleNamespace(assist_with_workspace=answer),
    )
    result = lead_assistant.chat_with_workspace_assistant(
        lead_assistant.AssistantChatRequest(messages=[
            AssistantMessage(role="user", content="Find Mina Chen"),
            AssistantMessage(role="assistant", content="I found a possible match."),
            AssistantMessage(role="user", content=latest),
        ]),
        SimpleNamespace(begin_nested=nullcontext),
        WorkspaceContext(
            workspace_id=uuid4(), actor_id="test",
            permissions=frozenset({"agents.execute", "leads.view", "leads.edit"}),
        ),
    )
    assert captured["messages"][-1]["content"] == latest
    assert result["reply"] == "Please clarify the detail you meant; no change was made."
    assert result["action"]["kind"] == "none"
    assert "lead_links" not in result


@pytest.mark.parametrize("query", [
    "Create a lead for Alex Demo", "Add a new lead",
    "Register a lead with this email", "Here is a new lead",
])
def test_varied_lead_creation_requests_are_recognized(query):
    from lead_agent.api.routes.lead_assistant import _is_lead_creation_request
    assert _is_lead_creation_request([AssistantMessage(role="user", content=query)])


def test_informal_details_continue_pending_creation_but_done_does_not():
    from lead_agent.api.routes.lead_assistant import _is_lead_creation_request
    conversation = [
        AssistantMessage(role="assistant", content="I can create a new lead entry. Share the known details."),
        AssistantMessage(role="user", content="Alex Demo,alex@example.test,03000000000,Acme,a i"),
    ]
    assert _is_lead_creation_request(conversation)
    conversation[-1] = AssistantMessage(role="user", content="Done")
    assert not _is_lead_creation_request(conversation)
    conversation = [
        AssistantMessage(
            role="assistant",
            content='New lead draft [Action review/outcome] {"state":"applied","result":"Created Alex"}',
        ),
        AssistantMessage(role="user", content="Alex has asked about pricing"),
    ]
    assert not _is_lead_creation_request(conversation)


def test_creation_proposal_is_partial_has_no_invented_target_and_keeps_note():
    import json
    from lead_agent.api.workspace_lead_actions import validate_lead_creation_proposal
    action = WorkspaceAssistantAction(
        kind="create_lead", target_id=str(uuid4()),
        payload_json='{"full_name":"Alex Demo","phone":"03000000000","follow_up_note":"Discuss AI services."}',
    )
    validate_lead_creation_proposal(action)
    assert action.target_id is None
    assert json.loads(action.payload_json) == {
        "full_name": "Alex Demo", "phone": "03000000000",
        "follow_up_note": "Discuss AI services.",
    }
    assert "email" not in json.loads(action.payload_json)


@pytest.mark.parametrize("payload", [
    "{}", '{"full_name":"  "}', '{"follow_up_note":"Only a note"}',
    '{"full_name":"Alex","workspace_id":"other-workspace"}',
    '{"full_name":"Alex","budget":-1}',
])
def test_invalid_creation_drafts_are_rejected_before_review(payload):
    from lead_agent.api.workspace_lead_actions import validate_lead_creation_proposal
    with pytest.raises(ValueError):
        validate_lead_creation_proposal(WorkspaceAssistantAction(kind="create_lead", payload_json=payload))


def test_capabilities_cover_all_typed_actions_and_respect_permissions():
    from typing import get_args
    from lead_agent.domain.assistant_capabilities import (
        WORKSPACE_ACTION_PERMISSIONS, workspace_action_capabilities,
    )
    assert set(get_args(WorkspaceAssistantAction.model_fields["kind"].annotation)) - {"none"} == set(WORKSPACE_ACTION_PERMISSIONS)
    assert workspace_action_capabilities(set()) == []
    kinds = {item["kind"] for item in workspace_action_capabilities({"leads.view"})}
    assert kinds == {"open_lead"}
    kinds = {item["kind"] for item in workspace_action_capabilities({"leads.edit"})}
    assert kinds == {"create_lead", "send_email"}


def test_saved_agent_history_is_private_resumable_and_supplies_relevant_context():
    from datetime import datetime, timezone
    from fastapi import HTTPException
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from lead_agent.api.routes.lead_assistant import (
        AssistantChatRequest,
        AssistantConversationSave,
        AssistantTranscriptMessage,
        _history_before_latest_request,
        create_agent_conversation,
        get_agent_conversation,
        list_agent_conversations,
        save_agent_conversation,
        delete_agent_conversation,
    )
    from lead_agent.domain.workspaces import WorkspaceContext
    from lead_agent.infrastructure.db.base import Base
    from lead_agent.infrastructure.db.models import WorkspaceORM

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        owner = WorkspaceContext(workspace_id=uuid4(), actor_id="user-one")
        session.add(WorkspaceORM(id=owner.workspace_id, name="Owner", slug="history-owner",
            status="active", is_demo=False, created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)))
        session.commit()
        created = create_agent_conversation(session, owner)
        conversation_id = UUID(created["id"])
        history = AssistantConversationSave(messages=[
            AssistantTranscriptMessage(id=f"m-{i}", role=role, content=text)
            for i, (role, text) in enumerate([
                ("user", "Look up Acme Workshop"),
                ("assistant", "We discussed Acme Workshop and renewal dates."),
                ("user", "Add a note about the renewal"),
                ("assistant", "Review this unsaved note."),
                ("user", "Renewal follows the April call"),
                ("assistant", "Draft ready to review."),
            ])
        ])
        saved = save_agent_conversation(conversation_id, history, session, owner)
        assert saved["message_count"] == 6
        assert saved["title"] == "Look up Acme Workshop"
        loaded = get_agent_conversation(conversation_id, session, owner)
        assert loaded["messages"][-1]["content"] == "Draft ready to review."

        request = AssistantChatRequest(
            conversation_id=conversation_id,
            message_count=6,
            messages=[
                {"role": "user", "content": "Renewal follows the April call"},
                {"role": "assistant", "content": "Draft ready to review."},
            ],
        )
        current, related = _history_before_latest_request(session, owner, request)
        assert [item["content"] for item in current] == [
            "Look up Acme Workshop", "We discussed Acme Workshop and renewal dates.",
            "Add a note about the renewal", "Review this unsaved note.",
        ]
        assert related == []
        assert list_agent_conversations(session, owner)["items"][0]["message_count"] == 6

        other = WorkspaceContext(workspace_id=owner.workspace_id, actor_id="user-two")
        with pytest.raises(HTTPException) as error:
            get_agent_conversation(conversation_id, session, other)
        assert error.value.status_code == 404

        related_chat = create_agent_conversation(session, owner)
        save_agent_conversation(
            UUID(related_chat["id"]),
            AssistantConversationSave(messages=[
                AssistantTranscriptMessage(id="past-1", role="user", content="Plan Acme Workshop renewal"),
                AssistantTranscriptMessage(id="past-2", role="assistant", content="Use the April renewal review."),
            ]),
            session, owner,
        )
        other_chat = create_agent_conversation(session, other)
        save_agent_conversation(
            UUID(other_chat["id"]),
            AssistantConversationSave(messages=[
                AssistantTranscriptMessage(id="private", role="user", content="Acme Workshop secret renewal"),
            ]),
            session, other,
        )
        _, related = _history_before_latest_request(
            session, owner,
            AssistantChatRequest(
                conversation_id=conversation_id,
                message_count=6,
                messages=[{"role": "user", "content": "Tell me about Acme Workshop renewal"}],
            ),
        )
        assert any("April renewal review" in item["content"] for item in related)
        assert not any("secret" in item["content"] for item in related)

        delete_agent_conversation(conversation_id, session, owner)
        with pytest.raises(HTTPException) as error:
            get_agent_conversation(conversation_id, session, owner)
        assert error.value.status_code == 404


@pytest.mark.parametrize("allowed", [True, False])
def test_creation_chat_is_not_misrouted_to_missing_existing_lead(monkeypatch, allowed):
    from contextlib import nullcontext
    from lead_agent.api.routes import lead_assistant
    from lead_agent.domain.lead_assistant import WorkspaceAssistantReply
    from lead_agent.domain.workspaces import WorkspaceContext

    captured = {}

    def answer(payload):
        captured.update(payload)
        return WorkspaceAssistantReply(
            reply="Here is the new lead draft; nothing has been saved.",
            action=WorkspaceAssistantAction(
                kind="create_lead",
                payload_json='{"full_name":"Alex Demo","email":"alex@example.test","phone":"03000000000","company_name":"Acme","product_or_service":"AI services","follow_up_note":"Discuss AI services."}',
            ),
        )

    monkeypatch.setattr(lead_assistant, "build_workspace_assistant_context", lambda *_: {})
    monkeypatch.setattr(lead_assistant, "build_workspace_record_context", lambda *_: {
        "matched_leads": [], "similar_leads": [],
    })
    monkeypatch.setattr(lead_assistant, "resolve_workspace_reasoning_provider", lambda *_: SimpleNamespace(assist_with_workspace=answer))
    result = lead_assistant.chat_with_workspace_assistant(
        lead_assistant.AssistantChatRequest(messages=[
            AssistantMessage(role="user", content="Create a new lead then add a note about AI services."),
            AssistantMessage(role="assistant", content="I can create a new lead entry. Share known details."),
            AssistantMessage(role="user", content="Alex Demo,alex@example.test,03000000000,Acme,a i"),
        ]),
        SimpleNamespace(begin_nested=nullcontext, scalar=lambda *_: pytest.fail("Creation must not require an existing target")),
        WorkspaceContext(
            workspace_id=uuid4(), actor_id="test",
            permissions=frozenset({"agents.execute", "leads.view"} | ({"leads.edit"} if allowed else set())),
        ),
    )
    assert captured["requested_workflow"] == "create_lead_draft"
    assert result["action"]["kind"] == ("create_lead" if allowed else "none")
    if allowed:
        assert result["action"]["target_id"] is None
    else:
        assert "lead-edit access" in result["reply"]
