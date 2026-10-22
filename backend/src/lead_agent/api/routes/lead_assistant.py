from __future__ import annotations

import json
import re
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission, require_permissions
from lead_agent.api.workspace_assistant_context import build_workspace_assistant_context
from lead_agent.application.governance import operations_policy
from lead_agent.application.workspace_retrieval import build_workspace_record_context
from lead_agent.domain.analysis import ReasoningProviderError
from lead_agent.domain.lead_assistant import (
    LeadAssistantAction,
    WorkspaceAssistantAction,
    WorkspaceAssistantReply,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import LeadORM, PipelineEventORM
from lead_agent.infrastructure.db.operations import LeadNoteORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.reasoning.factory import resolve_workspace_reasoning_provider
from lead_agent.infrastructure.repositories import get_lead

router = APIRouter(prefix="/v1/leads", tags=["lead assistant"])
workspace_router = APIRouter(prefix="/v1/agent", tags=["workspace assistant"])
_SIMPLE_ACKNOWLEDGEMENTS = {"hi", "hello", "hey", "thanks", "thank you"}


def _is_simple_acknowledgement(content: str) -> bool:
    normalized = re.sub(r"[\W_]+", " ", content.casefold()).strip()
    return normalized in _SIMPLE_ACKNOWLEDGEMENTS


def _is_explicit_lead_write_request(query: str) -> bool:
    return bool(
        re.search(
            r"\b(add|write|save|leave|create|record|update|change|correct|edit|"
            r"note|re-?evaluate|mark|set|move|delete|remove)\b",
            query.casefold(),
        )
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
            r"\b(is there|are there|do (?:i|we|you) have|does .* have|whether|"
            r"find|search|look(?:\s+up)?|check|show|list|who|which|details?|"
            r"tell me about|any)\b",
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
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("context")
    @classmethod
    def bound_context_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, ensure_ascii=False)) > 24000:
            raise ValueError("Workspace context is too large")
        return value


@workspace_router.post("/chat")
def chat_with_workspace_assistant(
    request: AssistantChatRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.execute")),
) -> dict[str, object]:
    latest_user_message = next(
        (message.content for message in reversed(request.messages) if message.role == "user"),
        "",
    )
    record_query = " ".join(
        message.content for message in reversed(request.messages)
        if message.role == "user"
    )[:3000]
    simple_acknowledgement = _is_simple_acknowledgement(latest_user_message)
    read_only_lead_lookup = _is_read_only_lead_lookup(latest_user_message)
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
    search_query = latest_user_message if read_only_lead_lookup else record_query
    if (
        not simple_acknowledgement
        and "leads.view" in workspace.permissions
        and latest_user_message
    ):
        try:
            with session.begin_nested():
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
    }
    if read_only_lead_lookup:
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
    if result.action.kind == "open_lead" or result.action.kind in lead_mutations:
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
                result.action.kind == "open_lead"
                or (
                    result.action.kind in lead_mutations
                    and _is_explicit_lead_write_request(record_query)
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
    response = result.model_dump(mode="json")
    response["context_warnings"] = workspace_context.get("context_warnings", [])
    response["drafting_context"] = {
        "workspace_profile": workspace_context.get("workspace_profile", {}),
    }
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
    return result.model_dump(mode="json")
