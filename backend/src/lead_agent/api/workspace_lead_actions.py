"""Validate and normalize proposed lead writes before displaying an approval card."""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from lead_agent.api.routes.leads import LeadProfileUpdateRequest, LeadProgressUpdate
from lead_agent.domain.lead_assistant import LeadProfileChanges, WorkspaceAssistantAction


class LeadNoteProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    note: str = Field(min_length=1, max_length=5000)


class LeadCreationProposal(LeadProfileChanges):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str | None = Field(default=None, max_length=10000)
    follow_up_note: str | None = Field(default=None, max_length=5000)


def validate_lead_creation_proposal(action: WorkspaceAssistantAction) -> None:
    try:
        payload = json.loads(action.payload_json or "")
        draft = LeadCreationProposal.model_validate(payload)
    except (ValueError, TypeError):
        raise ValueError("Provide the known lead details for review; missing fields may stay blank.") from None
    details = draft.model_dump(exclude={"follow_up_note"})
    if not any(
        (isinstance(value, str) and value.strip())
        or (isinstance(value, (int, float)) and value >= 0)
        for value in details.values()
    ):
        raise ValueError("Provide at least one known lead detail before creating a draft.")
    action.target_id = None
    action.target_label = draft.full_name or draft.company_name or "New lead"
    action.title = "Review new lead draft"
    action.summary = (
        "Create this lead only after approval and successful AI analysis. "
        "Any follow-up note remains a separate draft for review after creation."
    )
    action.payload_json = draft.model_dump_json(exclude_unset=True)


def validate_lead_proposal(action: WorkspaceAssistantAction) -> None:
    if action.kind in {"update_lead_profile", "add_lead_note"}:
        try:
            values = json.loads(action.payload_json or "{}")
        except (ValueError, TypeError):
            values = None
        if isinstance(values, dict) and len(values) == 1:
            for key in ("note", "body", "comment"):
                if key in values and isinstance(values[key], str):
                    action.kind = "add_lead_note"
                    action.payload_json = json.dumps({"note": values[key]})
                    break
    action.title = {
        "update_lead_profile": "Review lead details",
        "add_lead_note": "Review lead note",
        "record_lead_progress": "Review lead progress",
        "reevaluate_lead": "Re-evaluate lead",
    }[action.kind]
    action.summary = {
        "update_lead_profile": f"Apply the reviewed fields to {action.target_label or 'this lead'}.",
        "add_lead_note": f"Save the reviewed note on {action.target_label or 'this lead'}.",
        "record_lead_progress": "Record the reviewed pipeline progress. This does not book meetings or send email.",
        "reevaluate_lead": "Re-run qualification and routing using this lead's saved details and history.",
    }[action.kind]
    if action.kind == "reevaluate_lead":
        action.payload_json = None
        return
    try:
        payload = json.loads(action.payload_json or "")
    except (ValueError, TypeError):
        raise ValueError("Provide the exact fields to change.") from None
    model = {
        "update_lead_profile": LeadProfileUpdateRequest,
        "add_lead_note": LeadNoteProposal,
        "record_lead_progress": LeadProgressUpdate,
    }[action.kind]
    validated = model.model_validate(payload)
    if action.kind == "record_lead_progress":
        if validated.stage == "converted" and not validated.credited_user_id:
            raise ValueError("Choose Project or Sale and a credited member before recording a win.")
        if validated.send_direct_message:
            raise ValueError("Direct congratulations must be opted into from the lead page.")
    action.payload_json = validated.model_dump_json(exclude_unset=True, indent=2)
