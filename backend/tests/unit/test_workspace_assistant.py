from __future__ import annotations

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
    assert captured["workspace_context"] == {
        "lead_confirmation_setting": {"enabled": True}
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
