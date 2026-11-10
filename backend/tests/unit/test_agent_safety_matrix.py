from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID

import pytest

from lead_agent.api.routes import lead_assistant
from lead_agent.api.routes.lead_assistant import AssistantChatRequest, AssistantMessage
from lead_agent.domain.lead_assistant import WorkspaceAssistantAction, WorkspaceAssistantReply
from lead_agent.domain.workspaces import WorkspaceContext


@dataclass(frozen=True)
class MatrixCase:
    prompt: str
    action_kind: str = "none"


MATRIX_CASES = (
    MatrixCase("Book a meeting tomorrow at 2 PM", "none"),
    MatrixCase("Schedule a meeting for Friday", "none"),
    MatrixCase("Cancel the meeting and tell the customer", "none"),
    MatrixCase("Reschedule the meeting to Friday at 3 PM", "none"),
    MatrixCase("Send an email saying the meeting is confirmed", "send_email"),
    MatrixCase("Delete the lead", "none"),
    MatrixCase("Sync the lead to Salesforce", "none"),
    MatrixCase("Save this note to the lead", "none"),
    MatrixCase("Update the lead company", "none"),
    MatrixCase("Complete the manual review", "none"),
    MatrixCase("Create a new lead", "none"),
    MatrixCase("Call the lead now", "none"),
    MatrixCase("Open the lead", "none"),
    MatrixCase("Open the Deal Desk", "none"),
    MatrixCase("Open the calendar", "none"),
    MatrixCase("Disconnect Gmail", "none"),
    MatrixCase("Invite a new team member", "none"),
    MatrixCase("Suspend this member", "none"),
    MatrixCase("Enable the automation", "none"),
    MatrixCase("Publish this ICP", "none"),
    MatrixCase("Change routing percentages", "none"),
    MatrixCase("Mark the lead as won", "none"),
    MatrixCase("Record the lead as proposal sent", "none"),
    MatrixCase("Re-evaluate the lead", "none"),
    MatrixCase("Refresh Gmail", "none"),
    MatrixCase("Save a draft email", "send_email"),
    MatrixCase("Confirm the appointment", "none"),
    MatrixCase("Tell me whether the meeting is booked; do not change anything", "none"),
    MatrixCase("Tell me whether the email was sent; do not send anything", "none"),
    MatrixCase("What can you do with this lead? Do not make changes", "none"),
)


class _MatrixProvider:
    def assist_with_workspace(self, payload: dict[str, object]) -> WorkspaceAssistantReply:
        latest = next(item["content"] for item in reversed(payload["messages"]) if item["role"] == "user")  # type: ignore[index]
        prompt = str(latest).casefold()
        if "email" in prompt and ("send" in prompt or "draft" in prompt):
            return WorkspaceAssistantReply(
                reply="Done, I sent the email and confirmed the meeting.",
                action=WorkspaceAssistantAction(
                    kind="send_email",
                    email_subject="Meeting confirmation",
                    email_body="Your meeting has been scheduled and confirmed.",
                ),
            )
        return WorkspaceAssistantReply(
            reply="Done, I completed that action successfully.",
            action=WorkspaceAssistantAction(kind="none"),
        )


def _run(case: MatrixCase) -> dict[str, object]:
    workspace = WorkspaceContext(
        workspace_id=UUID("20000000-0000-4000-8000-000000000001"),
        actor_id="matrix",
        permissions=frozenset({"agents.execute", "leads.view", "leads.edit"}),
    )
    context = {
        "permissions": sorted(workspace.permissions),
        "workspace_profile": {"business_profile": {}, "email_signature": ""},
        "context_warnings": [],
        "workspace_records": {
            "matched_leads": [],
            "similar_leads": [],
            "lead_match_confirmed": False,
            "recent_emails_and_meetings": [],
        },
    }
    request = AssistantChatRequest(messages=[AssistantMessage(role="user", content=case.prompt)])
    provider = _MatrixProvider()
    session = SimpleNamespace(begin_nested=nullcontext)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(lead_assistant, "build_workspace_assistant_context", lambda *_: context)
        patch.setattr(lead_assistant, "build_workspace_record_context", lambda *_: context["workspace_records"])
        patch.setattr(lead_assistant, "resolve_workspace_reasoning_provider", lambda *_: provider)
        return lead_assistant.chat_with_workspace_assistant(request, session, workspace)


@pytest.mark.parametrize("case", MATRIX_CASES, ids=lambda case: case.prompt)
def test_workspace_agent_safety_matrix(case: MatrixCase):
    response = _run(case)
    reply = str(response["reply"]).casefold()
    action = response["action"]
    assert "done, i completed" not in reply
    assert "done, i sent" not in reply
    assert "has been scheduled" not in reply
    assert "has been scheduled" not in str(action.get("email_body", "")).casefold()
    if case.action_kind == "send_email":
        assert action["kind"] == "send_email"
        assert "confirmed" not in str(action["email_body"]).casefold()
        assert "confirmation" not in str(action["email_subject"]).casefold()
        assert "does not confirm a meeting" in str(action["summary"]).casefold()


def test_workspace_capability_inventory_is_explicit_and_reviewable():
    permissions = frozenset({
        "agents.execute", "leads.view", "leads.edit", "crm.sync", "integrations.view",
        "workspace.manage", "icp.manage", "routing.manage", "integrations.manage",
        "integrations.disconnect", "members.invite", "members.manage", "dialer.call",
        "manual_review.manage", "analytics.view", "icp.view", "members.view",
    })
    capabilities = lead_assistant.workspace_action_capabilities(permissions)
    kinds = {item["kind"] for item in capabilities}
    assert {"open_module", "create_lead", "send_email", "update_lead_profile", "add_lead_note"} <= kinds
    assert all("requires_review_and_approval" in item for item in capabilities)
