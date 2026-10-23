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
    lead_id = str(uuid4())
    monkeypatch.setattr(lead_assistant, "resolve_workspace_reasoning_provider",
                        lambda *_args: pytest.fail("read-only lookup should not need AI"))
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_assistant_context",
        lambda *_args: {"context_warnings": []},
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        lambda *_args: {
            "matched_leads": [
                {
                    "id": lead_id,
                    "name": "Kethi Ahmed",
                    "email": "kethi@example.test",
                    "phone": "+1 555 010 2000",
                    "company": "Northstar",
                    "job_title": "Operations Director",
                    "inquiry": "Interested in a discovery call.",
                }
            ]
        },
    )
    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[
                AssistantMessage(role="user", content="Is there a Kethi in my leads?")
            ]
        ),
        session=SimpleNamespace(begin_nested=nullcontext),
        workspace=WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view"}),
        ),
    )

    assert result["action"]["kind"] == "none"
    assert "Yes — I found this matching lead" in result["reply"]
    assert "**Kethi Ahmed**" in result["reply"]
    assert "kethi@example.test" in result["reply"]
    assert "+1 555 010 2000" in result["reply"]
    assert "Northstar" in result["reply"]
    assert "Operations Director" in result["reply"]
    assert result["lead_links"] == [
        {
            "id": lead_id,
            "name": "Kethi Ahmed",
            "company": "Northstar",
            "suggested": False,
        }
    ]


def test_workspace_chat_asks_for_full_name_when_lead_lookup_has_multiple_matches(
    monkeypatch,
) -> None:
    workspace_id = uuid4()
    first_lead_id = str(uuid4())
    second_lead_id = str(uuid4())
    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_args: pytest.fail("read-only lookup should not need AI"),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_assistant_context",
        lambda *_args: {"context_warnings": []},
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        lambda *_args: {
            "matched_leads": [
                {
                    "id": first_lead_id,
                    "name": "Kethi Ahmed",
                    "email": "kethi.ahmed@example.test",
                    "company": "Northstar",
                },
                {
                    "id": second_lead_id,
                    "name": "Kethi Iqbal",
                    "email": "kethi.iqbal@example.test",
                    "company": "Bluebird",
                },
            ]
        },
    )

    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[
                AssistantMessage(role="user", content="Is there a Kethi in my leads?")
            ]
        ),
        session=SimpleNamespace(begin_nested=nullcontext),
        workspace=WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view"}),
        ),
    )

    assert result["action"]["kind"] == "none"
    assert "Which one do you mean?" in result["reply"]
    assert "Reply with the full name." in result["reply"]
    assert "Kethi Ahmed" in result["reply"]
    assert "Kethi Iqbal" in result["reply"]
    assert "Northstar" in result["reply"]
    assert "Bluebird" in result["reply"]
    assert [lead["id"] for lead in result["lead_links"]] == [
        first_lead_id,
        second_lead_id,
    ]


def test_workspace_chat_reports_no_matching_lead_from_search_results(monkeypatch) -> None:
    workspace_id = uuid4()
    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_args: pytest.fail("read-only lookup should not need AI"),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_assistant_context",
        lambda *_args: {"context_warnings": []},
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        lambda *_args: {"matched_leads": []},
    )

    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[
                AssistantMessage(role="user", content="Is there a Kethi in my leads?")
            ]
        ),
        session=SimpleNamespace(begin_nested=nullcontext),
        workspace=WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view"}),
        ),
    )

    assert result["action"]["kind"] == "none"
    assert "couldn’t find a matching lead" in result["reply"]


def test_workspace_chat_asks_before_using_a_similar_lead(monkeypatch) -> None:
    workspace_id = uuid4()
    candidate_id = str(uuid4())
    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_args: pytest.fail("fuzzy matches must be confirmed before AI use"),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_assistant_context",
        lambda *_args: {"context_warnings": []},
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        lambda *_args: {
            "matched_leads": [],
            "similar_leads": [
                {
                    "id": candidate_id,
                    "name": "John Smith",
                    "company": "Lunar Robotics",
                    "similarity": 0.78,
                }
            ],
        },
    )

    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[
                AssistantMessage(role="user", content="Find the lead Jhon Smth.")
            ]
        ),
        session=SimpleNamespace(begin_nested=nullcontext),
        workspace=WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view"}),
        ),
    )

    assert result["action"]["kind"] == "none"
    assert "John Smith" in result["reply"]
    assert "Lunar Robotics" in result["reply"]
    assert "Reply yes to confirm this lead" in result["reply"]
    assert result["lead_links"] == [
        {
            "id": candidate_id,
            "name": "John Smith",
            "company": "Lunar Robotics",
            "suggested": True,
        }
    ]


def test_workspace_chat_returns_all_similar_lead_options(monkeypatch) -> None:
    workspace_id = uuid4()
    candidates = [
        {
            "id": str(uuid4()),
            "name": f"John Smith {index}",
            "company": f"Lunar Robotics {index}",
            "similarity": 0.82,
        }
        for index in range(9)
    ]
    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_args: pytest.fail("fuzzy matches must be confirmed before AI use"),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_assistant_context",
        lambda *_args: {"context_warnings": []},
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        lambda *_args: {"matched_leads": [], "similar_leads": candidates},
    )

    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[AssistantMessage(role="user", content="Find the lead Jhon Smth.")]
        ),
        session=SimpleNamespace(begin_nested=nullcontext),
        workspace=WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view"}),
        ),
    )

    assert len(result["lead_links"]) == len(candidates)
    assert {link["id"] for link in result["lead_links"]} == {
        candidate["id"] for candidate in candidates
    }
    assert all(link["suggested"] for link in result["lead_links"])


def test_workspace_lead_links_include_exact_and_similar_matches() -> None:
    exact_id = str(uuid4())
    similar_ids = [str(uuid4()), str(uuid4())]

    links = lead_assistant._workspace_lead_links(
        {
            "workspace_records": {
                "matched_leads": [
                    {"id": exact_id, "name": "Harmain Mughal", "company": "Corral"}
                ],
                "similar_leads": [
                    {"id": similar_id, "name": f"Harmain M. {index}", "company": None}
                    for index, similar_id in enumerate(similar_ids, start=1)
                ],
            }
        }
    )

    assert [link["id"] for link in links] == [*similar_ids, exact_id]
    assert [link["suggested"] for link in links] == [True, True, False]


def test_lead_assistant_keeps_all_matched_lead_links_after_yes(monkeypatch) -> None:
    workspace_id = uuid4()
    current_id = str(uuid4())
    candidates = [
        {
            "id": str(uuid4()),
            "name": f"Harmain Mughal {index}",
            "company": f"Company {index}",
        }
        for index in range(2)
    ]
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        lambda *_args, **_kwargs: {"matched_leads": [], "similar_leads": candidates},
    )
    messages = [
        AssistantMessage(role="user", content="is there any lead name harmain"),
        AssistantMessage(role="assistant", content="I found a possible lead match."),
        AssistantMessage(role="user", content="yes"),
    ]

    links = lead_assistant._lead_assistant_links(
        SimpleNamespace(begin_nested=nullcontext),
        WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view"}),
        ),
        messages,
        SimpleNamespace(
            id=current_id,
            full_name="Harmain Mughal",
            company_name="Corral",
        ),
        "The lead is Harmain Mughal. I found two other close matches.",
    )

    assert len(links) == 3
    assert links[0]["id"] == candidates[0]["id"]
    assert links[1]["id"] == candidates[1]["id"]
    assert links[2]["id"] == current_id


def test_lead_assistant_chat_returns_multiple_openable_matches(monkeypatch) -> None:
    from datetime import datetime, timezone

    from lead_agent.domain.lead_assistant import LeadAssistantAction, LeadAssistantReply

    workspace_id = uuid4()
    lead_id = uuid4()
    candidate_ids = [str(lead_id), str(uuid4()), str(uuid4())]
    candidates = [
        {
            "id": candidate_id,
            "name": f"Harmain Mughal {index}",
            "company": f"Company {index}",
        }
        for index, candidate_id in enumerate(candidate_ids, start=1)
    ]
    lead = SimpleNamespace(
        id=lead_id,
        full_name="Harmain Mughal 1",
        company_name="Company 1",
        email=None,
        phone=None,
        job_title=None,
        industry=None,
        employee_count=None,
        annual_revenue=None,
        geography=None,
        budget=None,
        product_or_service=None,
        message=None,
        source="manual",
        received_at=datetime.now(timezone.utc),
        qualification=None,
        outcome=None,
    )
    monkeypatch.setattr(lead_assistant, "get_lead", lambda *_args: lead)
    monkeypatch.setattr(
        lead_assistant,
        "operations_policy",
        lambda *_args: SimpleNamespace(
            business_profile=SimpleNamespace(model_dump=lambda **_kwargs: {})
        ),
    )
    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_args: SimpleNamespace(
            assist_with_lead=lambda _payload: LeadAssistantReply(
                reply="I found three matching leads: Harmain Mughal 1, Harmain Mughal 2, and Harmain Mughal 3.",
                action=LeadAssistantAction(),
            )
        ),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        lambda *_args, **_kwargs: {
            "matched_leads": [],
            "similar_leads": candidates,
        },
    )
    session = SimpleNamespace(scalars=lambda *_args: [], begin_nested=nullcontext)

    result = lead_assistant.chat_with_lead_assistant(
        lead_id,
        AssistantChatRequest(
            messages=[
                AssistantMessage(role="user", content="is there any lead name harmain")
            ]
        ),
        session,
        WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view"}),
        ),
    )

    assert [link["id"] for link in result["lead_links"]] == candidate_ids


def test_workspace_chat_uses_a_similar_lead_only_after_confirmation(monkeypatch) -> None:
    workspace_id = uuid4()
    candidate_id = str(uuid4())
    retrieval_calls: list[tuple[object, str, dict[str, object]]] = []
    candidate = {"id": candidate_id, "name": "John Smith", "company": "Lunar Robotics"}

    def confirmed_retrieval(_session, current_workspace_id, query, **kwargs):
        assert current_workspace_id == workspace_id
        assert query == "Find the lead Jhon Smth."
        assert kwargs == {"confirm_similar_lead": True}
        retrieval_calls.append((current_workspace_id, query, kwargs))
        return {
            "matched_leads": [candidate],
            "similar_leads": [],
            "lead_match_confirmed": True,
        }

    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_args: pytest.fail("confirmed read-only lookups do not need AI"),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_assistant_context",
        lambda *_args: {"context_warnings": []},
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        confirmed_retrieval,
    )

    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[
                AssistantMessage(role="user", content="Find the lead Jhon Smth."),
                AssistantMessage(
                    role="assistant",
                    content=(
                        "I found a close match. Reply yes to confirm this lead, "
                        "or tell me another name."
                    ),
                ),
                AssistantMessage(role="user", content="Yes, that's the one."),
            ]
        ),
        session=SimpleNamespace(begin_nested=nullcontext),
        workspace=WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view"}),
        ),
    )

    assert "Yes — I found this matching lead" in result["reply"]
    assert "John Smith" in result["reply"]
    assert len(retrieval_calls) == 1


def test_confirming_a_lead_name_does_not_authorize_an_unrequested_write(
    monkeypatch,
) -> None:
    workspace_id = uuid4()
    candidate_id = str(uuid4())

    class Provider:
        def assist_with_workspace(self, _payload):
            return WorkspaceAssistantReply(
                reply="I can update the lead.",
                action=WorkspaceAssistantAction(
                    kind="update_lead_profile",
                    title="Update lead",
                    summary="Change the lead's job title.",
                    target_id=candidate_id,
                    payload_json='{"job_title":"VP"}',
                ),
            )

    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_args: Provider(),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_assistant_context",
        lambda *_args: {"context_warnings": []},
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        lambda *_args, **_kwargs: {
            "matched_leads": [
                {
                    "id": candidate_id,
                    "name": "John Smith",
                    "company": "Lunar Robotics",
                }
            ],
            "similar_leads": [],
            "lead_match_confirmed": True,
        },
    )

    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[
                AssistantMessage(role="user", content="Open the lead Jhon Smth."),
                AssistantMessage(
                    role="assistant",
                    content=(
                        "I found a close match. Reply yes to confirm this lead, "
                        "or tell me another name."
                    ),
                ),
                AssistantMessage(role="user", content="Correct."),
            ]
        ),
        session=SimpleNamespace(
            begin_nested=nullcontext,
            scalar=lambda *_args: pytest.fail("unrequested writes must be blocked"),
        ),
        workspace=WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view", "leads.edit"}),
        ),
    )

    assert result["action"]["kind"] == "none"
    assert "specific update request" in result["reply"]


def test_workspace_chat_does_not_claim_no_match_when_search_is_unavailable(
    monkeypatch,
) -> None:
    workspace_id = uuid4()
    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_args: pytest.fail("read-only lookup should not need AI"),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_assistant_context",
        lambda *_args: {"context_warnings": []},
    )

    def failed_retrieval(*_args):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(lead_assistant, "build_workspace_record_context", failed_retrieval)
    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[
                AssistantMessage(role="user", content="Is there a Kethi in my leads?")
            ]
        ),
        session=SimpleNamespace(begin_nested=nullcontext),
        workspace=WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute", "leads.view"}),
        ),
    )

    assert result["action"]["kind"] == "none"
    assert "couldn’t search workspace leads" in result["reply"]
    assert "can’t confirm" in result["reply"]


def test_workspace_chat_explains_missing_lead_view_permission_for_lookup(
    monkeypatch,
) -> None:
    workspace_id = uuid4()
    monkeypatch.setattr(
        lead_assistant,
        "resolve_workspace_reasoning_provider",
        lambda *_args: pytest.fail("read-only lookup should not need AI"),
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_assistant_context",
        lambda *_args: {"context_warnings": []},
    )
    monkeypatch.setattr(
        lead_assistant,
        "build_workspace_record_context",
        lambda *_args: pytest.fail("lead search requires leads.view"),
    )

    result = chat_with_workspace_assistant(
        AssistantChatRequest(
            messages=[
                AssistantMessage(role="user", content="Is there a Kethi in my leads?")
            ]
        ),
        session=SimpleNamespace(begin_nested=nullcontext),
        workspace=WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="qa-user",
            permissions=frozenset({"agents.execute"}),
        ),
    )

    assert result["action"]["kind"] == "none"
    assert "current access" in result["reply"]
    assert "lead-view permission" in result["reply"]


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
