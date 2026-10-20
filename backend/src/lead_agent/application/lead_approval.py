"""Workspace-scoped, provider-specific lead approval policy."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lead_agent.domain.integration_directionality import directionality_for
from lead_agent.domain.integrations import PROVIDER_SETUP
from lead_agent.infrastructure.db.models import ProviderConnectionORM, WorkspaceAdminAuditEventORM
from lead_agent.infrastructure.db.operations import WorkspaceProviderLeadApprovalPolicyORM

ApprovalPolicySource = Literal["default", "legacy_migration", "user"]


LEAD_APPROVAL_PROVIDER_LABELS: dict[str, str] = {
    provider: setup.display_name
    for provider, setup in PROVIDER_SETUP.items()
    if provider != "mock" and directionality_for(provider).automatic_inbound
}


def provider_for_source_channel(channel: str) -> str | None:
    normalized = channel.strip().lower()
    if normalized.startswith("crm:"):
        normalized = normalized.partition(":")[2]
    return normalized if normalized in LEAD_APPROVAL_PROVIDER_LABELS else None


def lead_approval_policy(
    session: Session, workspace_id: UUID, provider: str
) -> tuple[bool, ApprovalPolicySource, datetime | None]:
    if provider not in LEAD_APPROVAL_PROVIDER_LABELS:
        raise ValueError(f"{provider} does not support inbound lead approval policies")
    row = session.get(WorkspaceProviderLeadApprovalPolicyORM, (workspace_id, provider))
    if row is None:
        return False, "default", None
    source: ApprovalPolicySource = (
        "legacy_migration" if row.source == "legacy_migration" else "user"
    )
    return bool(row.auto_approve_leads), source, row.updated_at


def provider_has_verified_connection(
    session: Session, workspace_id: UUID, provider: str
) -> bool:
    return bool(
        session.scalar(
            select(func.count())
            .select_from(ProviderConnectionORM)
            .where(
                ProviderConnectionORM.workspace_id == workspace_id,
                ProviderConnectionORM.provider_type == provider,
                ProviderConnectionORM.is_active.is_(True),
                ProviderConnectionORM.status == "verified",
            )
        )
    )


def provider_policy_review_reason(
    session: Session, workspace_id: UUID, provider: str | None
) -> str | None:
    if provider is None or provider not in LEAD_APPROVAL_PROVIDER_LABELS:
        return None
    enabled, _, _ = lead_approval_policy(session, workspace_id, provider)
    label = LEAD_APPROVAL_PROVIDER_LABELS[provider]
    if enabled and provider_has_verified_connection(session, workspace_id, provider):
        return None
    if enabled:
        return (
            f"{label} auto-approval is configured, but no active verified account is "
            "available; this imported lead was sent to Manual Review."
        )
    return (
        f"{label} automatic lead approval is off; this imported lead was sent to "
        "Manual Review."
    )


def combined_review_reason(*reasons: str | None) -> str | None:
    unique: list[str] = []
    for reason in reasons:
        cleaned = (reason or "").strip()
        if cleaned and cleaned not in unique:
            unique.append(cleaned)
    return " ".join(unique) or None


def update_lead_approval_policy(
    session: Session,
    workspace_id: UUID,
    provider: str,
    auto_approve_leads: bool,
    actor_id: str,
) -> WorkspaceProviderLeadApprovalPolicyORM:
    if provider not in LEAD_APPROVAL_PROVIDER_LABELS:
        raise ValueError(f"{provider} does not support inbound lead approval policies")

    now = datetime.now(UTC)
    row = session.get(WorkspaceProviderLeadApprovalPolicyORM, (workspace_id, provider))
    old_enabled = bool(row.auto_approve_leads) if row is not None else False
    old_source = row.source if row is not None else "default"
    if row is None:
        row = WorkspaceProviderLeadApprovalPolicyORM(
            workspace_id=workspace_id,
            provider=provider,
            auto_approve_leads=auto_approve_leads,
            source="user",
            updated_by=actor_id,
            updated_at=now,
        )
        session.add(row)
    else:
        row.auto_approve_leads = auto_approve_leads
        row.source = "user"
        row.updated_by = actor_id
        row.updated_at = now

    if old_enabled != auto_approve_leads or old_source != "user":
        try:
            audit_actor_id = UUID(actor_id)
        except (TypeError, ValueError):
            audit_actor_id = None
        session.add(
            WorkspaceAdminAuditEventORM(
                workspace_id=workspace_id,
                actor_id=audit_actor_id,
                operation="integration_lead_approval_policy_updated",
                before={
                    "provider": provider,
                    "auto_approve_leads": old_enabled,
                    "source": old_source,
                },
                after={
                    "provider": provider,
                    "auto_approve_leads": auto_approve_leads,
                    "source": "user",
                },
                created_at=now,
            )
        )
    session.flush()
    return row
