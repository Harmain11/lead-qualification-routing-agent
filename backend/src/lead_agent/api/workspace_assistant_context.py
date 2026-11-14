"""Read trusted, permission-scoped assistant snapshots without accepting browser data."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.api.routes.analytics import dashboard
from lead_agent.api.routes.email_automation import get_lead_confirmation_automation
from lead_agent.api.routes.icp import versions
from lead_agent.api.routes.routing import routing_config
from lead_agent.api.routes.team import invitations, list_members
from lead_agent.application.governance import operations_policy
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import ProviderConnectionORM
from lead_agent.infrastructure.db.operations import AutomationDefinitionORM

logger = logging.getLogger(__name__)


def build_workspace_assistant_context(
    session: Session, workspace: WorkspaceContext,
) -> dict[str, Any]:
    permissions = workspace.permissions
    context: dict[str, Any] = {
        "permissions": sorted(permissions),
        "available_areas": [
            area for area, allowed in {
                "analytics": "analytics.view" in permissions,
                "agents": "agents.view" in permissions,
                "automations": "agents.view" in permissions,
                "icp": "icp.view" in permissions,
                "settings": bool(
                    {"routing.view", "integrations.view", "workspace.manage"} & permissions
                ),
                "integrations": "integrations.view" in permissions,
                "team": "members.view" in permissions,
                "dialer": "dialer.call" in permissions,
                "communication": "communication.view" in permissions,
            }.items() if allowed
        ],
        "knowledge_sources": {
            "status": "not_connected",
            "document_sources": [],
            "note": (
                "No workspace document source is connected. Do not answer document-specific "
                "questions as if a playbook, pricing sheet, policy, or product document were available."
            ),
        },
        "context_warnings": [],
    }

    def load(key: str, reader: Callable[[], Any]) -> None:
        try:
            # One failing read must not leave the transaction unusable for other reads.
            with session.begin_nested():
                context[key] = jsonable_encoder(reader())
        except Exception:
            logger.warning("assistant.context_unavailable", extra={"section": key}, exc_info=True)
            context["context_warnings"].append(f"{key} is unavailable; do not infer its values.")

    if "communication.view" in permissions:
        from lead_agent.api.routes.communication import bootstrap

        def conversations() -> list[dict[str, Any]]:
            data = bootstrap(session, workspace)
            members = {member.id: member.display_name for member in data.members}
            return [
                {
                    "id": str(item.id),
                    "kind": item.kind,
                    "name": item.name,
                    "members": [members.get(member_id, "Member") for member_id in item.member_ids],
                }
                for item in data.conversations if not item.is_archived
            ][:50]

        load("communication_conversations", conversations)

    if "analytics.view" in permissions:
        load("analytics_monthly", lambda: dashboard("monthly", session, workspace))
    if "agents.view" in permissions:
        def automations() -> list[dict[str, Any]]:
            rows = session.scalars(select(AutomationDefinitionORM).where(
                AutomationDefinitionORM.workspace_id == workspace.workspace_id,
            ).order_by(AutomationDefinitionORM.updated_at.desc()).limit(20))
            return [
                {key: getattr(row, key) for key in (
                    "id", "name", "kind", "active", "version", "specification",
                )}
                for row in rows
            ]
        load("automations", automations)
    if "icp.view" in permissions:
        load("icp_versions", lambda: versions(session, workspace)[:10])
    if "routing.view" in permissions:
        load("routing_settings", lambda: routing_config(session, workspace))
        load("workspace_profile", lambda: operations_policy(
            session, workspace.workspace_id,
        ).model_dump(mode="json"))
    if "integrations.manage" in permissions:
        load("lead_confirmation_setting", lambda: get_lead_confirmation_automation(
            session, workspace,
        ))
    if "integrations.view" in permissions:
        def integrations() -> list[dict[str, Any]]:
            rows = session.scalars(select(ProviderConnectionORM).where(
                ProviderConnectionORM.workspace_id == workspace.workspace_id,
            ).order_by(ProviderConnectionORM.account_name).limit(40))
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                grouped.setdefault(row.provider_type, []).append({
                    "id": str(row.id), "account_name": row.account_name,
                    "status": row.status, "is_active": row.is_active,
                })
            return [
                {"provider": provider, "connections": items}
                for provider, items in grouped.items()
            ]

        load("integrations", integrations)
    if "members.view" in permissions:
        load("team", lambda: {
            "members": list_members(session, workspace)[:40],
            "invitations": (
                invitations(session, workspace)[:40] if "members.invite" in permissions else []
            ),
        })
    if {"leads.edit", "leads.view"} <= permissions:
        from lead_agent.api.routes.leads import closing_options
        load("lead_closing_options", lambda: closing_options(session, workspace))
    # Trimming is disclosed rather than rejecting all chat in a large workspace.
    while len(json.dumps(context, ensure_ascii=False)) > 24_000:
        candidates = [key for key in context if key not in {
            "permissions", "available_areas", "knowledge_sources", "context_warnings",
            "workspace_profile",
        }]
        if not candidates:
            break
        key = max(candidates, key=lambda item: len(json.dumps(context[item])))
        value = context[key]
        if isinstance(value, list) and len(value) > 1:
            context[key] = value[:max(1, len(value) // 2)]
        else:
            del context[key]
        warning = f"{key} was limited for size; ask for a narrower request."
        if warning not in context["context_warnings"]:
            context["context_warnings"].append(warning)
    return context
