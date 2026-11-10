"""Server-side field classification and mutation authorization."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import HTTPException

from lead_agent.domain.workspaces import WorkspaceContext

FIELD_CLASSIFICATIONS: dict[str, str] = {
    "email": "contact",
    "phone": "contact",
    "annual_revenue": "financial",
    "budget": "financial",
    "message": "communications",
}

SENSITIVE_READ_PERMISSIONS: dict[str, str] = {
    "email": "fields.contact.view",
    "phone": "fields.contact.view",
    "recipient": "fields.contact.view",
    "recipients": "fields.contact.view",
    "sender": "fields.contact.view",
    "annual_revenue": "fields.financial.view",
    "budget": "fields.financial.view",
    "opportunity_value": "fields.financial.view",
    "closed_revenue": "fields.financial.view",
    "revenue_value": "fields.financial.view",
    "message": "fields.communications.view",
    "subject": "fields.communications.view",
    "snippet": "fields.communications.view",
    "body": "fields.communications.view",
    "transcript": "calls.transcript.view",
    "transcript_excerpt": "calls.transcript.view",
    "recording_url": "calls.recording.play",
    "analysis": "calls.analysis.view",
    "analysis_payload": "calls.analysis.view",
    "insights": "calls.analysis.view",
    "error_message": "fields.communications.view",
}


def assert_field_writes(workspace: WorkspaceContext, fields: Iterable[str]) -> None:
    for field in fields:
        classification = FIELD_CLASSIFICATIONS.get(field)
        if classification is None:
            continue
        permission = f"fields.{classification}.edit"
        if permission not in workspace.permissions:
            raise HTTPException(403, f"permission required: {permission}")


def filter_sensitive_mapping(
    payload: Mapping[str, Any], permissions: frozenset[str] | set[str]
) -> dict[str, Any]:
    """Remove classified values from generic exports and audit payloads."""
    def filtered(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: filtered(item)
                for key, item in value.items()
                if (required := SENSITIVE_READ_PERMISSIONS.get(str(key))) is None
                or required in permissions
            }
        if isinstance(value, list):
            return [filtered(item) for item in value]
        if isinstance(value, tuple):
            return tuple(filtered(item) for item in value)
        return value

    return filtered(payload)
