"""Validate and normalize proposed lead writes before displaying an approval card."""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from lead_agent.api.routes.leads import LeadProfileUpdateRequest, LeadProgressUpdate
from lead_agent.domain.lead_assistant import WorkspaceAssistantAction


class LeadNoteProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    note: str = Field(min_length=1, max_length=5000)


def validate_lead_proposal(action: WorkspaceAssistantAction) -> None:
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
