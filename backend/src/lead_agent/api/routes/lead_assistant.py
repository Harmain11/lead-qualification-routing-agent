from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission, require_permissions
from lead_agent.api.workspace_assistant_context import build_workspace_assistant_context
from lead_agent.application.governance import operations_policy
from lead_agent.application.manual_review import lead_manual_review_reasons
from lead_agent.application.workspace_retrieval import build_workspace_record_context
from lead_agent.domain.analysis import ReasoningProviderError
from lead_agent.domain.assistant_capabilities import workspace_action_capabilities
from lead_agent.domain.lead_assistant import (
    LeadAssistantAction,
    WorkspaceAssistantAction,
    WorkspaceAssistantReply,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import (
    LeadORM,
    PipelineEventORM,
    WorkspaceAgentConversationORM,
)
from lead_agent.infrastructure.db.operations import LeadNoteORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.reasoning.factory import resolve_workspace_reasoning_provider
from lead_agent.infrastructure.repositories import get_current_routing, get_lead

router = APIRouter(prefix="/v1/leads", tags=["lead assistant"])
workspace_router = APIRouter(prefix="/v1/agent", tags=["workspace assistant"])
_SIMPLE_ACKNOWLEDGEMENTS = {"hi", "hello", "hey", "thanks", "thank you"}
_LEAD_CONFIRMATION_MARKER = "Reply yes to confirm this lead"
_LEAD_ASSISTANT_ACKNOWLEDGEMENTS = {
    "yes", "yeah", "yep", "yup", "sure", "correct", "right", "exactly",
    "ok", "okay", "open it", "go ahead", "that s right", "that is right",
    "that s the one", "the first one", "the second one",
}
logger = logging.getLogger(__name__)


def _is_simple_acknowledgement(content: str) -> bool:
    normalized = re.sub(r"[\W_]+", " ", content.casefold()).strip()
    return normalized in _SIMPLE_ACKNOWLEDGEMENTS


def _lead_confirmation_state(
    messages: list[AssistantMessage],
) -> tuple[str | None, str | None]:
    latest_user_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if messages[index].role == "user"
        ),
        None,
    )
    if latest_user_index is None:
        return None, None

    latest_user_message = messages[latest_user_index].content
    normalized = re.sub(
        r"[\W_]+",
        " ",
        latest_user_message.casefold().replace("’", "").replace("'", ""),
    ).strip()
    assistant_index = next(
        (
            index
            for index in range(latest_user_index - 1, -1, -1)
            if messages[index].role == "assistant"
        ),
        None,
    )
    if assistant_index is None or _LEAD_CONFIRMATION_MARKER.casefold() not in (
        messages[assistant_index].content.casefold()
    ):
        return None, None

    original_query = next(
        (
            messages[index].content
            for index in range(assistant_index - 1, -1, -1)
            if messages[index].role == "user"
        ),
        None,
    )
    if not original_query:
        return None, None

    if normalized in {
        "yes",
        "yeah",
        "yep",
        "correct",
        "thats right",
        "thats the one",
        "that one",
        "yes please",
        "yes thats right",
        "yes thats the one",
        "use this lead",
        "use that lead",
    }:
        return "confirmed", original_query
    if normalized in {"no", "nope", "no thanks", "not that one", "not this one"}:
        return "declined", original_query
    return "correction", latest_user_message


def _is_explicit_lead_write_request(query: str) -> bool:
    return bool(
        re.search(
            r"\b(add|write|save|leave|create|record|update|change|correct|edit|"
            r"note|re-?evaluate|mark|set|move|delete|remove)\b",
            query.casefold(),
        )
    )


def _is_explicit_manual_review_completion_request(query: str) -> bool:
    return bool(
        re.search(r"\b(mark|set|change|complete|finish|close)\b", query.casefold())
        and re.search(r"\b(review|hold)\b", query.casefold())
    )


def _is_read_only_lead_lookup(query: str) -> bool:
    normalized = query.casefold()
    if not re.search(r"\b(leads?|prospects?|contacts?)\b", normalized):
        return False
    if re.search(
        r"\b(open|add|write|save|create|record|update|change|correct|edit|"
        r"delete|remove|send|book|set|mark|move)\b",
        normalized,
    ):
        return False
    return bool(
        re.search(
            r"\b(is there|are there|do (?:i|we|you) have|"
            r"find|search|look\s+up|show|list)\b",
            normalized,
        )
    )


def _format_lead_lookup_item(lead: dict[str, Any]) -> str:
    name = lead.get("name") if isinstance(lead.get("name"), str) else "Unnamed lead"
    lines = [f"**{name}**"]

    def add(label: str, value: Any) -> None:
        if value is not None and str(value).strip():
            lines.append(f"- {label}: {value}")

    add("Email", lead.get("email"))
    add("Phone", lead.get("phone"))
    add("Company", lead.get("company"))
    add("Job title", lead.get("job_title"))
    add("Industry", lead.get("industry"))
    add("Product or service", lead.get("product_or_service"))
    add("Geography", lead.get("geography"))
    add("Source", lead.get("source_detail") or lead.get("source"))
    add("Received", lead.get("received_at"))

    qualification = lead.get("qualification")
    if isinstance(qualification, dict):
        classification = qualification.get("classification")
        score = qualification.get("score")
        if classification is not None:
            value = (
                f"{classification} (score {score})"
                if score is not None
                else classification
            )
            add("Qualification", value)

    routing = lead.get("routing")
    if isinstance(routing, dict):
        add("Assigned representative", routing.get("assigned_rep"))
        add("Queue", routing.get("queue"))

    outcome = lead.get("outcome")
    if isinstance(outcome, dict):
        add("Sales stage", outcome.get("stage"))

    add("Inquiry", lead.get("inquiry"))
    return "\n".join(lines)


def _workspace_lead_links(
    workspace_context: dict[str, Any],
) -> list[dict[str, Any]]:
    records = workspace_context.get("workspace_records")
    if not isinstance(records, dict):
        return []

    links: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for key, suggested in (("similar_leads", True), ("matched_leads", False)):
        candidates = records.get(key)
        if not isinstance(candidates, list):
            continue
        for lead in candidates:
            if not isinstance(lead, dict):
                continue
            lead_id = lead.get("id")
            name = lead.get("name")
            if not isinstance(lead_id, (str, UUID)) or not isinstance(name, str):
                continue
            normalized_id = str(lead_id)
            if not normalized_id or not name.strip() or normalized_id in seen_ids:
                continue
            company = lead.get("company")
            links.append(
                {
                    "id": normalized_id,
                    "name": name.strip(),
                    "company": company.strip()
                    if isinstance(company, str) and company.strip()
                    else None,
                    "suggested": suggested,
                }
            )
            seen_ids.add(normalized_id)
    return links


def _lead_assistant_search_query(messages: list[AssistantMessage]) -> str:
    for message in reversed(messages):
        if message.role != "user":
            continue
        normalized = re.sub(r"[\W_]+", " ", message.content.casefold()).strip()
        if not normalized or normalized in _LEAD_ASSISTANT_ACKNOWLEDGEMENTS:
            continue
        return message.content.strip()[:3000]
    return ""


def _lead_assistant_links(
    session: Session,
    workspace: WorkspaceContext,
    messages: list[AssistantMessage],
    lead: LeadORM,
    reply: str,
) -> list[dict[str, Any]]:
    if not _wants_lead_links(messages):
        return []
    search_query = _lead_assistant_search_query(messages)
    records: dict[str, Any] = {}
    if search_query:
        try:
            with session.begin_nested():
                records = build_workspace_record_context(
                    session, workspace.workspace_id, search_query,
                )
        except Exception:
            logger.warning("lead_assistant.lead_link_search_unavailable", exc_info=True)

    links = _workspace_lead_links({"workspace_records": records})
    reply_folded = reply.casefold()
    if search_query and not (
        _is_read_only_lead_lookup(search_query)
        or re.search(r"\b(find|search|look(?:\s+up)?|check|show|list)\b", search_query.casefold())
    ):
        links = [
            link for link in links
            if link["name"].casefold() in reply_folded
        ]

    lead_id = str(lead.id)
    if lead.full_name.casefold() in reply_folded and not any(
        link["id"] == lead_id for link in links
    ):
        links.append(
            {
                "id": lead_id,
                "name": lead.full_name,
                "company": lead.company_name,
                "suggested": False,
            }
        )
    return links


def _wants_lead_links(messages: list[AssistantMessage]) -> bool:
    latest = next((message.content for message in reversed(messages) if message.role == "user"), "")
    normalized = re.sub(r"[\W_]+", " ", latest.casefold()).strip()
    # A brief confirmation can continue an actual lead-match selection, but
    # thanks/done/corrections must not resurrect earlier searches.
    if normalized in {"yes", "yes please", "that one", "thats the one"}:
        previous = next((message.content for message in reversed(messages) if message.role == "assistant"), "")
        if not re.search(r"\b(match|matches|which lead|which one)\b", previous.casefold()):
            return False
        latest = _lead_assistant_search_query(messages)
    return (_is_read_only_lead_lookup(latest) and bool(re.search(
        r"\b(is there|are there|do (?:i|we|you) have|show|list|find|search|look(?:\s+up)?)\b",
        latest.casefold(),
    ))) or bool(re.search(
        r"\b(open|view)\b.{0,35}\b(lead|prospect|contact|profile|record)\b",
        latest.casefold(),
    )) or (
        bool(re.search(r"\b(find|search|look\s+up)\b", latest.casefold()))
        and not re.search(r"\b(notes?|comments?|e-?mails?|calls?|meetings?|history|progress)\b", latest.casefold())
    )


def _is_conversation_only_message(messages: list[AssistantMessage]) -> bool:
    latest = next((message.content for message in reversed(messages) if message.role == "user"), "")
    normalized = re.sub(r"[\W_]+", " ", latest.casefold()).strip()
    return normalized in {
        "thanks", "thank you", "ok", "okay", "done", "sorry", "my mistake",
        "sorry my mistake", "that was a mistake", "i made a mistake",
        "sorry i made a mistake", "i meant something else", "never mind", "nevermind",
        "right", "correct", "got it", "understood",
    }


_PENDING_LEAD_ACTION_REPLIES = {
    "update_profile": (
        "I can update this lead's profile. Review the proposed fields below and choose "
        "Apply changes. Nothing has been saved yet."
    ),
    "add_note": (
        "I can save the proposed note after you review it below. "
        "No note has been saved yet."
    ),
    "record_progress": (
        "I can record this progress update after you review it below. "
        "The lead's status has not changed yet."
    ),
    "draft_email": (
        "I can prepare an editable email draft. Review its contents in the composer; "
        "nothing has been sent."
    ),
    "send_follow_up": (
        "I can open an editable email composer. Review the recipient and message, "
        "then choose Send yourself; nothing has been sent yet."
    ),
    "refresh_email": (
        "I can refresh this lead's Gmail conversation after you choose Refresh Gmail "
        "below. It has not been refreshed yet."
    ),
    "sync_crm": (
        "I can sync this lead to the selected CRM after you confirm below. "
        "No CRM write has happened yet."
    ),
    "delete_lead": "I can delete this lead after you confirm below. It is still in the workspace.",
    "reevaluate_lead": (
        "I can re-evaluate this lead after you confirm below. "
        "Its assessment has not changed yet."
    ),
    "complete_manual_review": (
        "I can complete this lead's manual review after you confirm below. "
        "The review hold has not been cleared yet."
    ),
    "open_email": "I can open this lead's Emails section. No email has been sent.",
    "open_meetings": (
        "I can open meeting controls in Follow up. I haven't booked, cancelled, "
        "or rescheduled anything. Review the calendar status and confirm any change there."
    ),
    "open_dialer": (
        "I can open the Dialer for the saved number. "
        "You will need to place the call yourself."
    ),
    "open_closing": (
        "I can open the Deal section to review progress and closing options. "
        "No status has changed."
    ),
    "open_history": "I can open this lead's Activity section. This does not change the lead.",
}


def _truthful_lead_assistant_reply(
    reply: str, action: LeadAssistantAction, latest_user: str,
) -> str:
    """Keep provider prose from reporting a proposed or unsupported action as completed."""
    if action.kind != "none":
        return _PENDING_LEAD_ACTION_REPLIES[action.kind]
    if re.search(
        r"^\s*(?:please\s+)?(?:cancel|reschedule|book|schedule)\b|"
        r"\b(?:can|could|would|will)\s+you\b.{0,50}\b(?:cancel|reschedule|book|schedule)\b|"
        r"\bi\s+(?:need|want)\s+to\s+(?:cancel|reschedule|book|schedule)\b",
        latest_user, re.I,
    ):
        return (
            "I haven't changed the meeting. Open Follow up to check the booking and "
            "use the available cancel or reschedule controls. A calendar change needs "
            "confirmation from the calendar provider."
        )
    requested_change = re.search(
        r"\b(cancel|reschedule|book|schedule|send|save|update|change|delete|"
        r"remove|complete|mark|sync|refresh|create|add|record|call)\b",
        latest_user, re.I,
    )
    completion_claim = re.search(
        r"\b(?:done|all set|completed)\b|\bi(?:'ve| have)?\s+"
        r"(?:cancelled|canceled|rescheduled|booked|scheduled|sent|saved|updated|changed|"
        r"deleted|removed|completed|marked|synced|refreshed|created|added|recorded|called)\b|"
        r"\b(?:meeting|appointment|email|lead|record|note|draft)\b.{0,60}"
        r"\b(?:has been|was|is now)\s+"
        r"(?:cancelled|canceled|rescheduled|booked|scheduled|sent|saved|updated|changed|"
        r"deleted|removed|completed|marked|synced|refreshed|created|added|recorded)\b",
        reply, re.I,
    )
    if requested_change and completion_claim:
        return (
            "I haven't made that change. I can propose supported lead actions here, "
            "but a change is complete only after you confirm it and the app reports success."
        )
    return reply


def _is_lead_creation_request(messages: list[AssistantMessage]) -> bool:
    if _is_conversation_only_message(messages):
        return False
    latest = next((message.content for message in reversed(messages) if message.role == "user"), "")
    creation = r"\b(?:new\s+lead|(?:create|add|enter|register)\s+(?:(?:a|the|new)\s+){0,2}lead)\b"
    if re.search(creation, latest, re.I):
        return True
    previous = next((message.content for message in reversed(messages) if message.role == "assistant"), "")
    # Comma-separated details may answer the previous request to create a lead.
    # A UI-reported successful creation ends that pending workflow.
    return bool(
        re.search(creation, previous, re.I)
        and not re.search(r'"state"\s*:\s*"(?:applied|completed|dismissed)"', previous)
        and latest.strip()
    )


def _lead_lookup_reply(
    workspace_context: dict[str, Any],
    permissions: frozenset[str],
) -> str:
    if "leads.view" not in permissions:
        return (
            "I can’t search workspace leads with your current access. "
            "Ask a workspace admin to grant lead-view permission."
        )

    if any(
        "Workspace record search is unavailable" in warning
        for warning in workspace_context.get("context_warnings", [])
        if isinstance(warning, str)
    ):
        return (
            "I couldn’t search workspace leads just now, so I can’t confirm whether "
            "there’s a match. Please try again shortly."
        )

    records = workspace_context.get("workspace_records")
    if not isinstance(records, dict) or not isinstance(records.get("matched_leads"), list):
        return (
            "I couldn’t verify the workspace lead search, so I can’t confirm whether "
            "there’s a match."
        )

    matched_leads = [
        lead for lead in records["matched_leads"] if isinstance(lead, dict)
    ]
    raw_similar_leads = records.get("similar_leads", [])
    similar_leads = (
        [lead for lead in raw_similar_leads if isinstance(lead, dict)]
        if isinstance(raw_similar_leads, list)
        else []
    )
    if similar_leads:
        suggestions = []
        for lead in similar_leads:
            name = lead.get("name") if isinstance(lead.get("name"), str) else "Unnamed lead"
            company = lead.get("company")
            suggestions.append(
                f"**{name}**"
                + (f" — {company}" if isinstance(company, str) and company.strip() else "")
            )
        if len(suggestions) == 1:
            return (
                "I couldn’t find an exact name, but I found a close match among the leads "
                f"saved in this workspace: {suggestions[0]}. Is this the lead you mean? "
                f"{_LEAD_CONFIRMATION_MARKER}, or tell me another name, company, or email."
            )
        return (
            "I couldn’t find an exact name. These are the closest leads saved in this "
            "workspace:\n\n" + "\n".join(f"- {item}" for item in suggestions)
            + "\n\nWhich one do you mean? Reply with the name or company."
        )

    if not matched_leads:
        return (
            "I couldn’t find a matching lead in the stored Corral workspace records. "
            "This search doesn’t query live external CRM or email archives."
        )

    details = "\n\n".join(_format_lead_lookup_item(lead) for lead in matched_leads)
    if len(matched_leads) == 1:
        return f"Yes — I found this matching lead:\n\n{details}"
    if len(matched_leads) >= 8:
        return (
            f"I found at least {len(matched_leads)} matching leads. The search returns "
            f"up to 8 records, so there may be more. Which full name do you mean?\n\n{details}"
        )
    return (
        f"I found {len(matched_leads)} matching leads. Which one do you mean? "
        f"Reply with the full name.\n\n{details}"
    )


def _resolve_unambiguous_lead_mention(
    matched_leads: list[dict[str, Any]],
    query: str,
) -> dict[str, Any] | None:
    query_normalized = re.sub(r"\s+", " ", query.casefold()).strip()
    exact_matches = [
        lead
        for lead in matched_leads
        if isinstance(lead.get("name"), str)
        and lead["name"].casefold() in query_normalized
        or isinstance(lead.get("email"), str)
        and lead["email"].casefold() in query_normalized
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if exact_matches or len(matched_leads) >= 8:
        return None

    ignored_words = {
        "there", "this", "that", "with", "from", "into", "about", "have",
        "lead", "leads", "contact", "contacts", "find", "open", "show",
        "check", "look", "search", "for", "the", "and", "are", "was",
        "were", "who", "what", "when", "where", "which", "does", "did",
        "has", "had", "is", "in", "on", "my", "our", "your", "me", "us",
    }
    query_words = {
        word
        for word in re.findall(r"[^\W_]{3,}", query_normalized)
        if word not in ignored_words
    }
    if not query_words:
        return None
    partial_matches = [
        lead
        for lead in matched_leads
        if isinstance(lead.get("name"), str)
        and query_words.intersection(
            re.findall(r"[^\W_]{3,}", lead["name"].casefold())
        )
    ]
    return partial_matches[0] if len(partial_matches) == 1 else None


class AssistantMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=3000)


class AssistantChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[AssistantMessage] = Field(min_length=1, max_length=16)
    conversation_id: UUID | None = None
    message_count: int | None = Field(default=None, ge=1, le=500)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("context")
    @classmethod
    def bound_context_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, ensure_ascii=False)) > 24000:
            raise ValueError("Workspace context is too large")
        return value


class AssistantTranscriptMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=128)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=3000)
    action: dict[str, Any] | None = None
    actionStatus: Literal["applying", "sending", "applied", "dismissed"] | None = None
    actionResult: str | None = Field(default=None, max_length=1000)
    actionError: str | None = Field(default=None, max_length=1000)
    needsVerification: bool = False
    contextWarnings: list[str] | None = None
    leadLinks: list[dict[str, Any]] | None = None


class AssistantConversationSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[AssistantTranscriptMessage] = Field(min_length=1, max_length=500)

    @field_validator("messages")
    @classmethod
    def bound_transcript_size(cls, value: list[AssistantTranscriptMessage]) -> list[AssistantTranscriptMessage]:
        if len(json.dumps([message.model_dump(mode="json") for message in value])) > 500_000:
            raise ValueError("Conversation history is too large")
        return value


def _owned_conversation(
    session: Session,
    workspace: WorkspaceContext,
    conversation_id: UUID,
) -> WorkspaceAgentConversationORM:
    row = session.scalar(
        select(WorkspaceAgentConversationORM).where(
            WorkspaceAgentConversationORM.id == conversation_id,
            WorkspaceAgentConversationORM.workspace_id == workspace.workspace_id,
            WorkspaceAgentConversationORM.actor_id == workspace.actor_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return row


@workspace_router.get("/conversations")
def list_agent_conversations(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.execute")),
) -> dict[str, object]:
    rows = list(session.scalars(
        select(WorkspaceAgentConversationORM)
        .where(
            WorkspaceAgentConversationORM.workspace_id == workspace.workspace_id,
            WorkspaceAgentConversationORM.actor_id == workspace.actor_id,
        )
        .order_by(WorkspaceAgentConversationORM.updated_at.desc())
        .limit(100)
    ))
    return {"items": [
        {
            "id": str(row.id),
            "title": row.title,
            "updated_at": row.updated_at.isoformat(),
            "message_count": len(row.messages),
            "preview": next(
                (message["content"] for message in reversed(row.messages)
                 if message.get("role") == "user" and isinstance(message.get("content"), str)),
                "",
            )[:180],
        }
        for row in rows
    ]}


@workspace_router.post("/conversations", status_code=status.HTTP_201_CREATED)
def create_agent_conversation(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.execute")),
) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    row = WorkspaceAgentConversationORM(
        id=uuid4(),
        workspace_id=workspace.workspace_id,
        actor_id=workspace.actor_id,
        title="New conversation",
        messages=[],
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return {"id": str(row.id), "title": row.title, "messages": []}


@workspace_router.get("/conversations/{conversation_id}")
def get_agent_conversation(
    conversation_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.execute")),
) -> dict[str, object]:
    row = _owned_conversation(session, workspace, conversation_id)
    return {
        "id": str(row.id),
        "title": row.title,
        "messages": row.messages,
        "updated_at": row.updated_at.isoformat(),
    }


@workspace_router.put("/conversations/{conversation_id}")
def save_agent_conversation(
    conversation_id: UUID,
    request: AssistantConversationSave,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.execute")),
) -> dict[str, object]:
    row = _owned_conversation(session, workspace, conversation_id)
    transcript = [message.model_dump(mode="json", exclude_none=True) for message in request.messages]
    first_user_text = next((item["content"] for item in transcript if item["role"] == "user"), "")
    row.messages = transcript
    if first_user_text:
        row.title = first_user_text[:117] + ("…" if len(first_user_text) > 117 else "")
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    return {"id": str(row.id), "title": row.title, "message_count": len(transcript)}


@workspace_router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent_conversation(
    conversation_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.execute")),
) -> Response:
    row = _owned_conversation(session, workspace, conversation_id)
    session.delete(row)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _history_before_latest_request(
    session: Session, workspace: WorkspaceContext, request: AssistantChatRequest
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    if request.conversation_id is None:
        return [], []
    row = _owned_conversation(session, workspace, request.conversation_id)
    stored = [item for item in row.messages if item.get("role") in {"user", "assistant"}]
    def contextual_text(item: dict[str, Any]) -> str:
        text = item.get("content", "")
        if not isinstance(text, str):
            return ""
        if item.get("role") == "assistant" and isinstance(item.get("action"), dict):
            action = item["action"]
            outcome = {
                "kind": action.get("kind"),
                "title": action.get("title"),
                "state": item.get("actionStatus", "awaiting_approval"),
                "result": item.get("actionResult"),
                "error": item.get("actionError"),
            }
            text = f"{text}\n[Previously reviewed action state, not a new request]\n{json.dumps(outcome, ensure_ascii=False)}"
        return text[:3000]

    message_count = request.message_count or len(stored)
    incoming_count = len(request.messages)
    cut = max(0, min(len(stored), message_count - incoming_count))
    current_history = [
        {"role": item["role"], "content": contextual_text(item)}
        for item in stored[:cut]
        if isinstance(item.get("content"), str)
    ][-18:]

    latest = next((item.content for item in reversed(request.messages) if item.role == "user"), "")
    query_terms = {
        token for token in re.findall(r"[^\W_]{4,}", latest.casefold())
        if token not in {"this", "that", "with", "from", "have", "about", "what", "when"}
    }
    related: list[tuple[int, list[dict[str, str]]]] = []
    if query_terms:
        rows = session.scalars(
            select(WorkspaceAgentConversationORM).where(
                WorkspaceAgentConversationORM.workspace_id == workspace.workspace_id,
                WorkspaceAgentConversationORM.actor_id == workspace.actor_id,
                WorkspaceAgentConversationORM.id != request.conversation_id,
            ).order_by(WorkspaceAgentConversationORM.updated_at.desc()).limit(20)
        )
        for past in rows:
            turns = [
                {"role": item["role"], "content": contextual_text(item)}
                for item in past.messages[-12:]
                if item.get("role") in {"user", "assistant"}
                and isinstance(item.get("content"), str)
            ]
            matches = sum(
                bool(query_terms & set(re.findall(r"[^\W_]{4,}", turn["content"].casefold())))
                for turn in turns if turn["role"] == "user"
            )
            if matches:
                related.append((matches, turns))
    related.sort(key=lambda item: item[0], reverse=True)
    related_context = [
        {"role": item["role"], "content": item["content"][:900]}
        for _, turns in related[:2] for item in turns[-4:]
    ]
    return current_history, related_context


@workspace_router.post("/chat")
def chat_with_workspace_assistant(
    request: AssistantChatRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.execute")),
) -> dict[str, object]:
    conversation_history, related_conversation_history = _history_before_latest_request(
        session, workspace, request,
    )
    latest_user_message = next(
        (message.content for message in reversed(request.messages) if message.role == "user"),
        "",
    )
    creation_requested = _is_lead_creation_request(request.messages)
    record_query = " ".join(
        message.content for message in reversed(request.messages)
        if message.role == "user"
    )[:3000]
    confirmation_state, confirmation_query = _lead_confirmation_state(request.messages)
    simple_acknowledgement = _is_simple_acknowledgement(latest_user_message)
    read_only_lead_lookup = _is_read_only_lead_lookup(latest_user_message)
    declined_lead_match = confirmation_state == "declined"
    if simple_acknowledgement:
        # Greetings and thanks need neither a workspace snapshot nor prior turns.
        workspace_context = {
            "permissions": sorted(workspace.permissions),
            "context_warnings": [],
        }
        assistant_messages = [{"role": "user", "content": latest_user_message}]
    else:
        # Legacy clients may still send context, but it is never a trusted snapshot.
        workspace_context = build_workspace_assistant_context(session, workspace)
        assistant_messages = [message.model_dump() for message in request.messages]
    if confirmation_state == "confirmed":
        search_query = confirmation_query or record_query
    elif confirmation_state == "correction":
        search_query = latest_user_message
    else:
        search_query = latest_user_message if read_only_lead_lookup else record_query
    if (
        not simple_acknowledgement
        and not declined_lead_match
        and "leads.view" in workspace.permissions
        and latest_user_message
    ):
        try:
            with session.begin_nested():
                if confirmation_state == "confirmed":
                    workspace_context["workspace_records"] = build_workspace_record_context(
                        session,
                        workspace.workspace_id,
                        search_query,
                        confirm_similar_lead=True,
                    )
                else:
                    workspace_context["workspace_records"] = build_workspace_record_context(
                        session, workspace.workspace_id, search_query,
                    )
        except Exception:
            workspace_context.setdefault("context_warnings", []).append(
                "Workspace record search is unavailable; do not infer lead details "
                "or propose lead changes."
            )
    payload = {
        "messages": assistant_messages,
        "workspace_context": workspace_context,
        "workspace_id": str(workspace.workspace_id),
        "scope": (
            "Corral workspace analytics, automations, ICP, settings, integrations, "
            "team access, Dialer navigation, guidance, and content generation"
        ),
        "requested_workflow": "create_lead_draft" if creation_requested else None,
        "available_actions": workspace_action_capabilities(workspace.permissions),
        "conversation_history": conversation_history,
        "related_conversation_history": related_conversation_history,
    }
    records = workspace_context.get("workspace_records")
    similar_leads = records.get("similar_leads", []) if isinstance(records, dict) else []
    if not isinstance(similar_leads, list):
        similar_leads = []
    if declined_lead_match:
        result = WorkspaceAssistantReply(
            reply=(
                "No problem. Tell me another name, company, or email and I’ll search "
                "the leads saved in this workspace."
            ),
        )
    elif (not creation_requested) and (read_only_lead_lookup or (similar_leads and (
        _wants_lead_links(request.messages) or _is_explicit_lead_write_request(latest_user_message)
    )) or (
        confirmation_state == "confirmed"
        and not (
            isinstance(records, dict)
            and records.get("lead_match_confirmed") is True
        )
    ) or (
        confirmation_state == "confirmed"
        and _is_read_only_lead_lookup(search_query)
    )):
        result = WorkspaceAssistantReply(
            reply=_lead_lookup_reply(workspace_context, workspace.permissions),
        )
    else:
        provider = resolve_workspace_reasoning_provider(session, workspace.workspace_id)
        assistant_method = getattr(provider, "assist_with_workspace", None)
        if not callable(assistant_method):
            raise HTTPException(
                503,
                "Connect and verify a workspace AI provider before using Corral Agent chat.",
            )
        try:
            result = assistant_method(payload)
        except ReasoningProviderError as error:
            raise HTTPException(502, str(error)) from None
        except Exception:
            raise HTTPException(502, "Corral Agent could not complete this request.") from None
    lead_mutations = {
        "update_lead_profile", "add_lead_note", "record_lead_progress", "reevaluate_lead",
    }
    manual_review_mutations = {"complete_lead_manual_review"}
    lead_actions = lead_mutations | manual_review_mutations
    lead_write_query = (
        confirmation_query
        if confirmation_state == "confirmed"
        else record_query
    )
    if (
        result.action.kind in lead_actions
        and not _is_explicit_lead_write_request(lead_write_query)
    ):
        result.action = WorkspaceAssistantAction()
        result.reply = (
            "I found the lead, but need a specific update request before proposing a change."
        )
    if _is_conversation_only_message(request.messages) and result.action.kind != "none":
        result.action = WorkspaceAssistantAction()
        result.reply = "No new changes were made. If you meant to correct something, tell me what should change."
    if result.action.kind == "open_lead" and not _wants_lead_links(request.messages):
        result.action = WorkspaceAssistantAction()
    if result.action.kind == "open_deal_desk" and not any(
        term in record_query.casefold()
        for term in ("quote", "proposal", "pricing", "discount", "deal desk")
    ):
        result.action = WorkspaceAssistantAction()
        result.reply = (
            "I can only open the Deal Desk for a quote, proposal, pricing, or discount "
            "request. Tell me the verified lead’s name or email."
        )
    if result.action.kind == "create_lead":
        if "leads.edit" not in workspace.permissions:
            result.action = WorkspaceAssistantAction()
            result.reply = "You need lead-edit access to create a lead."
        else:
            from lead_agent.api.workspace_lead_actions import validate_lead_creation_proposal
            try:
                validate_lead_creation_proposal(result.action)
            except ValueError as error:
                result.action = WorkspaceAssistantAction()
                result.reply = f"The new lead draft needs clarification: {error}"[:3000]
    if result.action.kind in {"open_lead", "open_deal_desk"} or result.action.kind in lead_actions:
        target_id = result.action.target_id
        matched_lead_ids = {
            item.get("id")
            for item in workspace_context.get("workspace_records", {}).get(
                "matched_leads", []
            )
            if isinstance(item, dict)
        }
        matched_leads = [
            item
            for item in workspace_context.get("workspace_records", {}).get(
                "matched_leads", []
            )
            if isinstance(item, dict)
        ]
        if result.action.kind == "open_deal_desk":
            folded_query = record_query.casefold()
            explicitly_named = [
                item for item in matched_leads
                if (
                    isinstance(item.get("name"), str)
                    and item["name"].casefold() in folded_query
                ) or (
                    isinstance(item.get("email"), str)
                    and item["email"].casefold() in folded_query
                )
            ]
            if len(explicitly_named) != 1:
                result.action = WorkspaceAssistantAction()
                result.reply = (
                    "Please name one verified lead by full name or email before I open "
                    "the Deal Desk. Nothing has been created, approved, or sent."
                )
            else:
                result.action.target_id = str(explicitly_named[0].get("id") or "")
                result.action.target_label = (
                    explicitly_named[0].get("name") or "Selected lead"
                )
                target_id = result.action.target_id
        try:
            parsed_target_id = UUID(target_id or "")
        except ValueError:
            parsed_target_id = None
        if not (
            parsed_target_id is not None
            and target_id in matched_lead_ids
        ):
            mentioned_lead = _resolve_unambiguous_lead_mention(
                matched_leads, record_query
            )
            if mentioned_lead and (
                result.action.kind in {"open_lead", "open_deal_desk"}
                or (
                    result.action.kind in lead_actions
                    and _is_explicit_lead_write_request(lead_write_query)
                )
            ):
                result.action.target_id = str(mentioned_lead.get("id") or "")
                target_id = result.action.target_id
                try:
                    parsed_target_id = UUID(target_id)
                except ValueError:
                    parsed_target_id = None
        target_is_available = (
            parsed_target_id is not None
            and target_id in matched_lead_ids
            and "leads.view" in workspace.permissions
            and session.scalar(
                select(LeadORM.id).where(
                    LeadORM.id == parsed_target_id,
                    LeadORM.workspace_id == workspace.workspace_id,
                )
            )
            is not None
        )
        if (
            not target_is_available
            or (result.action.kind in lead_mutations and "leads.edit" not in workspace.permissions)
            or (
                result.action.kind in manual_review_mutations
                and "manual_review.manage" not in workspace.permissions
            )
        ):
            result.action = WorkspaceAssistantAction()
            result.reply = (
                "I could not verify an accessible target lead for that action. "
                "Please give the lead's name or email, and make sure you have the required access."
            )
        else:
            matched_lead = next(
                item for item in workspace_context["workspace_records"]["matched_leads"]
                if item.get("id") == target_id
            )
            result.action.target_label = matched_lead.get("name") or "Selected lead"
            if result.action.kind in lead_mutations:
                from lead_agent.api.workspace_lead_actions import validate_lead_proposal
                try:
                    validate_lead_proposal(result.action)
                    if result.action.kind == "record_lead_progress":
                        values = json.loads(result.action.payload_json or "{}")
                        credited_id = values.get("credited_user_id")
                        if credited_id:
                            credited = next((
                                item for item in workspace_context.get("lead_closing_options", [])
                                if item.get("user_id") == credited_id
                            ), None)
                            if credited is None:
                                raise ValueError(
                                    "Choose an active credited member from this workspace."
                                )
                            if values.get("stage") == "converted":
                                result.action.summary = (
                                    f"Record a {values['conversion_type']} win for "
                                    f"{result.action.target_label}; credit "
                                    f"{credited['display_name']}. Configured team congratulations "
                                    "may be posted. No direct message will be sent."
                                )
                except ValueError as error:
                    result.action = WorkspaceAssistantAction()
                    result.reply = (
                        f"The lead change needs clarification before review: {error}"
                    )[:3000]
            elif result.action.kind in manual_review_mutations:
                if not matched_lead.get("manual_review_active"):
                    result.action = WorkspaceAssistantAction()
                    result.reply = (
                        f"{matched_lead.get('name') or 'This lead'} is not currently "
                        "waiting for manual review."
                    )
                else:
                    result.action.title = "Complete manual review"
                    result.action.summary = (
                        f"Clear {result.action.target_label}'s review hold and apply "
                        "current routing rules. The resulting assignee may be notified."
                    )
    response = result.model_dump(mode="json")
    response["context_warnings"] = workspace_context.get("context_warnings", [])
    if "leads.view" in workspace.permissions and _wants_lead_links(request.messages):
        lead_links = _workspace_lead_links(workspace_context)
        if lead_links:
            response["lead_links"] = lead_links
    response["drafting_context"] = {
        "workspace_profile": workspace_context.get("workspace_profile", {}),
    }
    response["conversation_id"] = (
        str(request.conversation_id) if request.conversation_id is not None else None
    )
    return response


@router.post("/{lead_id}/assistant/chat")
def chat_with_lead_assistant(
    lead_id: UUID,
    request: AssistantChatRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permissions("agents.execute", "leads.view")),
) -> dict[str, object]:
    lead = get_lead(session, lead_id, workspace.workspace_id)
    if lead is None:
        raise HTTPException(404, "Lead not found")

    current_routing = get_current_routing(session, lead.id, workspace.workspace_id)
    review_reasons = lead_manual_review_reasons(session, lead, current_routing)
    provider = resolve_workspace_reasoning_provider(session, workspace.workspace_id)
    assistant_method = getattr(provider, "assist_with_lead", None)
    if not callable(assistant_method):
        raise HTTPException(
            503,
            "Connect and verify a workspace AI provider before using the lead assistant.",
        )

    events = list(
        session.scalars(
            select(PipelineEventORM)
            .where(
                PipelineEventORM.lead_id == lead_id,
                PipelineEventORM.workspace_id == workspace.workspace_id,
            )
            .order_by(PipelineEventORM.created_at.desc())
            .limit(20)
        )
    )
    notes = list(
        session.scalars(
            select(LeadNoteORM)
            .where(
                LeadNoteORM.lead_id == lead_id,
                LeadNoteORM.workspace_id == workspace.workspace_id,
            )
            .order_by(LeadNoteORM.updated_at.desc(), LeadNoteORM.id.desc())
            .limit(30)
        )
    )
    payload = {
        "permissions": sorted(workspace.permissions),
        "lead": {
            "id": str(lead.id),
            "full_name": lead.full_name,
            "email": lead.email,
            "phone": lead.phone,
            "job_title": lead.job_title,
            "company_name": lead.company_name,
            "industry": lead.industry,
            "employee_count": lead.employee_count,
            "annual_revenue": (
                float(lead.annual_revenue)
                if lead.annual_revenue is not None
                else None
            ),
            "geography": lead.geography,
            "budget": float(lead.budget) if lead.budget is not None else None,
            "product_or_service": lead.product_or_service,
            "original_inquiry_or_context": lead.message,
            "source": lead.source,
            "received_at": lead.received_at.isoformat(),
            "manual_review": {
                "active": bool(review_reasons),
                "reasons": review_reasons,
            },
            "qualification": (
                {
                    "score": lead.qualification.total_score,
                    "classification": lead.qualification.classification,
                    "rationale": lead.qualification.rationale,
                }
                if lead.qualification
                else None
            ),
            "outcome": (
                {
                    "pipeline_stage": lead.outcome.pipeline_stage,
                    "conversion_type": lead.outcome.conversion_type,
                    "closed_revenue": (
                        float(lead.outcome.closed_revenue)
                        if lead.outcome.closed_revenue is not None
                        else None
                    ),
                    "credited_name": lead.outcome.credited_name,
                }
                if lead.outcome
                else None
            ),
        },
        "workspace_business_profile": operations_policy(
            session, workspace.workspace_id
        ).business_profile.model_dump(mode="json"),
        "recent_history": [
            {
                "stage": event.stage,
                "status": event.status,
                "created_at": event.created_at.isoformat(),
                "details": event.details,
            }
            for event in reversed(events)
        ],
        "team_notes": [
            {
                "body": note.body,
                "updated_at": note.updated_at.isoformat(),
            }
            for note in reversed(notes)
        ],
        "messages": [message.model_dump() for message in request.messages],
    }
    try:
        result = assistant_method(payload)
    except ReasoningProviderError as error:
        raise HTTPException(502, str(error)) from None
    except Exception:
        raise HTTPException(502, "The lead assistant could not complete this request.") from None
    if result.action.kind == "complete_manual_review":
        latest_user_message = next(
            (
                message.content
                for message in reversed(request.messages)
                if message.role == "user"
            ),
            "",
        )
        if "manual_review.manage" not in workspace.permissions:
            result.action = LeadAssistantAction()
            result.reply = (
                "You need manual-review management access to complete this lead's review."
            )
        elif not _is_explicit_manual_review_completion_request(latest_user_message):
            result.action = LeadAssistantAction()
            result.reply = "Please explicitly ask me to complete this lead's manual review."
        elif not review_reasons:
            result.action = LeadAssistantAction()
            result.reply = "This lead is not currently waiting for manual review."
    if _is_conversation_only_message(request.messages):
        result.action = LeadAssistantAction()
    if result.action.kind == "open_dialer" and (
        "dialer.call" not in workspace.permissions or not lead.phone
    ):
        result.action = LeadAssistantAction()
        if not lead.phone:
            result.reply = (
                f"I don’t have a phone number saved for {lead.full_name}. "
                "Share the number and I can help update the lead before you call."
            )
        else:
            result.reply = (
                f"{lead.full_name}’s saved number is {lead.phone}, but you don’t have "
                "permission to open the phone from this workspace. Ask an admin for Dialer access."
            )
    lead_edit_actions = {
        "update_profile", "add_note", "record_progress", "draft_email",
        "send_follow_up", "refresh_email", "delete_lead", "reevaluate_lead",
    }
    email_actions = {"draft_email", "send_follow_up", "refresh_email", "open_email"}
    if result.action.kind in lead_edit_actions and "leads.edit" not in workspace.permissions:
        result.action = LeadAssistantAction()
        result.reply = (
            "I can't make that change with your current access. "
            "Ask an admin for lead-edit access."
        )
    elif result.action.kind == "sync_crm" and "crm.sync" not in workspace.permissions:
        result.action = LeadAssistantAction()
        result.reply = (
            "I can't sync this lead to a CRM with your current access. "
            "Ask an admin for CRM-sync access."
        )
    elif result.action.kind in email_actions and "integrations.view" not in workspace.permissions:
        result.action = LeadAssistantAction()
        result.reply = (
            "I can't open this lead's email with your current access. "
            "Ask an admin for integration-view access."
        )
    latest_user_message = next(
        (message.content for message in reversed(request.messages) if message.role == "user"),
        "",
    )
    result.reply = _truthful_lead_assistant_reply(
        result.reply, result.action, latest_user_message,
    )
    response = result.model_dump(mode="json")
    if "leads.view" in workspace.permissions:
        lead_links = _lead_assistant_links(
            session, workspace, request.messages, lead, result.reply,
        )
        if lead_links:
            response["lead_links"] = lead_links
    return response
