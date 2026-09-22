from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.application.crm import sync_lead_to_crm
from lead_agent.domain.crm import CrmProvider
from lead_agent.domain.tools import AgentTool, ToolContext, ToolResult
from lead_agent.infrastructure.crm.factory import WorkspaceCrmProviderResolver
from lead_agent.infrastructure.db.models import AgentToolAuditORM
from lead_agent.infrastructure.repositories import get_lead


class ToolPermissionError(PermissionError):
    pass


class ToolRegistry:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool is already registered: {tool.name}")
        self._tools[tool.name] = tool

    def capabilities(self) -> list[dict[str, str]]:
        return [
            {"name": tool.name, "required_permission": tool.required_permission}
            for tool in self._tools.values()
        ]

    def invoke(
        self,
        tool_name: str,
        context: ToolContext,
        raw_input: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> ToolResult:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise KeyError(f"unknown agent tool: {tool_name}")
        prior = self.session.scalar(
            select(AgentToolAuditORM).where(
                AgentToolAuditORM.workspace_id == context.workspace_id,
                AgentToolAuditORM.tool_name == tool_name,
                AgentToolAuditORM.idempotency_key == idempotency_key,
            )
        )
        input_summary = {
            key: value
            for key, value in raw_input.items()
            if key in tool.input_model.model_fields
        }
        if tool.required_permission not in context.permissions:
            if prior is None:
                now = datetime.now(UTC)
                self.session.add(
                    AgentToolAuditORM(
                        id=uuid4(),
                        workspace_id=context.workspace_id,
                        actor_id=context.actor_id,
                        tool_name=tool_name,
                        required_permission=tool.required_permission,
                        idempotency_key=idempotency_key,
                        status="denied",
                        input_summary=input_summary,
                        output={},
                        error_message="actor lacks the required tool permission",
                        created_at=now,
                        completed_at=now,
                    )
                )
                self.session.commit()
            raise ToolPermissionError(
                f"missing permission for {tool_name}: {tool.required_permission}"
            )
        if prior is not None:
            if prior.status != "completed":
                raise ValueError(f"tool invocation already recorded with status: {prior.status}")
            return ToolResult(
                status=prior.status,
                output=prior.output,
                audit_event_id=prior.id,
                reused=True,
            )

        now = datetime.now(UTC)
        audit = AgentToolAuditORM(
            id=uuid4(),
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            tool_name=tool_name,
            required_permission=tool.required_permission,
            idempotency_key=idempotency_key,
            status="pending",
            input_summary=input_summary,
            output={},
            error_message=None,
            created_at=now,
            completed_at=None,
        )
        self.session.add(audit)
        self.session.commit()
        try:
            validated = tool.input_model.model_validate(raw_input)
            output = tool.execute(context, validated)
        except Exception as error:
            audit.status = "failed"
            audit.error_message = str(error)[:2000]
            audit.completed_at = datetime.now(UTC)
            self.session.commit()
            raise
        audit.status = "completed"
        audit.output = output
        audit.completed_at = datetime.now(UTC)
        self.session.commit()
        return ToolResult(status="completed", output=output, audit_event_id=audit.id)


class CrmSyncToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead_id: UUID


class CrmCreateOrUpdateLeadTool:
    name = "crm.create_or_update_lead"
    required_permission = "crm:write"
    input_model = CrmSyncToolInput

    def __init__(
        self,
        session: Session,
        resolver: WorkspaceCrmProviderResolver,
        fallback: CrmProvider,
    ) -> None:
        self.session = session
        self.resolver = resolver
        self.fallback = fallback

    def execute(self, context: ToolContext, validated_input: BaseModel) -> dict[str, Any]:
        request = CrmSyncToolInput.model_validate(validated_input)
        lead = get_lead(self.session, request.lead_id, context.workspace_id)
        if lead is None:
            raise LookupError("lead not found in the tool workspace")
        provider = self.resolver.resolve(self.session, context.workspace_id, self.fallback)
        row = sync_lead_to_crm(self.session, lead, provider)
        return {
            "crm_sync_id": str(row.id),
            "provider": row.provider,
            "status": row.status,
            "provider_entity_ids": row.provider_entity_ids,
        }
