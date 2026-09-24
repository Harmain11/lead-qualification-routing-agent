from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

TOOL_CAPABILITY_CATALOG: dict[str, dict[str, str]] = {
    "crm.lookup_customer": {"permission": "crm:read", "status": "planned"},
    "crm.create_or_update_lead": {"permission": "crm.sync", "status": "implemented"},
    "crm.update_deal": {"permission": "crm.sync", "status": "planned"},
    "notification.send": {"permission": "notification:send", "status": "planned"},
    "email.read": {"permission": "email:read", "status": "future"},
    "email.send": {"permission": "email:send", "status": "future"},
    "calendar.schedule": {"permission": "calendar:write", "status": "future"},
}


@dataclass(frozen=True)
class ToolContext:
    workspace_id: UUID
    actor_id: str
    permissions: frozenset[str]


class ToolResult(BaseModel):
    status: str
    output: dict[str, Any]
    audit_event_id: UUID
    reused: bool = False


class AgentTool(Protocol):
    name: str
    required_permission: str
    input_model: type[BaseModel]

    def execute(self, context: ToolContext, validated_input: BaseModel) -> dict[str, Any]: ...
