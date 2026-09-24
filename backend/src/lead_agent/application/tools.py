from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.application.crm import manually_sync_lead_to_crm
from lead_agent.domain.crm import CrmProvider
from lead_agent.domain.tools import AgentTool, ToolContext, ToolResult
from lead_agent.infrastructure.crm.factory import WorkspaceCrmProviderResolver
from lead_agent.infrastructure.db.models import AgentToolAuditORM
from lead_agent.infrastructure.observability import get_observability, provider_operation
from lead_agent.infrastructure.rate_limit import get_rate_limiter, policy_for
from lead_agent.infrastructure.repositories import get_lead
from lead_agent.settings.config import get_settings

_SENSITIVE_AUDIT_KEYS = ("secret", "token", "password", "credential", "api_key")
_LOGGER = logging.getLogger("lead_agent.agent_tools")


class ToolRateLimitError(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("agent operation rate limit exceeded")
        self.retry_after = retry_after


def _audit_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[redacted]"
                if any(fragment in str(key).lower() for fragment in _SENSITIVE_AUDIT_KEYS)
                else _audit_safe(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_audit_safe(item) for item in value]
    return value


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
        settings = get_settings()
        policy = policy_for(settings, "agent")
        try:
            allowed, retry_after = get_rate_limiter(settings).hit(
                f"{settings.app_name}:{settings.app_env}:agent:{context.workspace_id}:{context.actor_id}",
                policy,
            )
        except Exception as error:
            if settings.app_env == "production":
                raise ToolRateLimitError(5) from error
            allowed, retry_after = True, 0
        if not allowed:
            raise ToolRateLimitError(retry_after)
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
        input_summary = _audit_safe(
            {key: value for key, value in raw_input.items() if key in tool.input_model.model_fields}
        )
        accepted_permissions = frozenset({tool.required_permission})
        if not accepted_permissions.intersection(context.permissions):
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
            with provider_operation("agent_tool") as operation_context:
                _LOGGER.info(
                    "agent_tool.started",
                    extra={
                        "fields": {
                            **operation_context,
                            "workspace_id": str(context.workspace_id),
                            "actor_id": context.actor_id,
                            "tool_name": tool_name,
                        }
                    },
                )
                output = tool.execute(context, validated)
        except Exception as error:
            audit.status = "failed"
            audit.error_message = f"{type(error).__name__}: tool execution failed"
            audit.completed_at = datetime.now(UTC)
            self.session.commit()
            get_observability().record_error(
                "agent_tool.failed",
                workspace_id=str(context.workspace_id),
                actor_id=context.actor_id,
                tool_name=tool_name,
                error_type=type(error).__name__,
            )
            raise
        audit.status = "completed"
        audit.output = _audit_safe(output)
        audit.completed_at = datetime.now(UTC)
        self.session.commit()
        return ToolResult(status="completed", output=output, audit_event_id=audit.id)


class CrmSyncToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead_id: UUID


class CrmCreateOrUpdateLeadTool:
    name = "crm.create_or_update_lead"
    required_permission = "crm.sync"
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
        provider = self.resolver.resolve(
            self.session,
            context.workspace_id,
            self.fallback,
            actor_id=context.actor_id,
        )
        row = manually_sync_lead_to_crm(
            self.session,
            lead,
            provider,
            actor_id=context.actor_id,
            requested_action="agent_manual_crm_send_or_update",
        )
        return {
            "crm_sync_id": str(row.id),
            "provider": row.provider,
            "status": row.status,
            "provider_entity_ids": row.provider_entity_ids,
        }
