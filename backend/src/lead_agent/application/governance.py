"""Consent and deployment constraints apply at every outreach boundary."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.domain.operations import OperationsPolicy, email_key
from lead_agent.infrastructure.db.models import WorkspaceAdminAuditEventORM
from lead_agent.infrastructure.db.operations import ConsentEventORM, OperationsPolicyORM


class OutreachBlocked(ValueError):
    pass


def operations_policy(session: Session, workspace_id: UUID) -> OperationsPolicy:
    row = session.get(OperationsPolicyORM, workspace_id)
    return OperationsPolicy.model_validate(row.configuration if row else {})


def audit_operation(
    session: Session, workspace_id: UUID, actor_id: str, operation: str, details: dict[str, object]
) -> None:
    try:
        actor = UUID(actor_id)
    except (ValueError, TypeError):
        actor = None
    session.add(
        WorkspaceAdminAuditEventORM(
            id=uuid4(),
            workspace_id=workspace_id,
            actor_id=actor,
            target_membership_id=None,
            operation=operation,
            before={},
            after=details,
            created_at=datetime.now(UTC),
        )
    )


def hosting_region() -> str:
    return os.environ.get("CORRAL_HOSTING_REGION", "local").lower()


def assert_residency(session: Session, workspace_id: UUID) -> None:
    required = operations_policy(session, workspace_id).required_region
    if required not in {"any", hosting_region()}:
        raise OutreachBlocked("Workspace residency policy does not permit this hosting region.")


def outreach_allowed(
    session: Session, workspace_id: UUID, email: str, channel: str = "email"
) -> tuple[bool, str]:
    try:
        key = email_key(email)
    except ValueError:
        return False, "A valid contact email is required."
    assert_residency(session, workspace_id)
    events = list(
        session.scalars(
            select(ConsentEventORM)
            .where(
                ConsentEventORM.workspace_id == workspace_id,
                ConsentEventORM.email == key,
                ConsentEventORM.channel.in_(("all", channel)),
            )
            .order_by(ConsentEventORM.created_at.desc(), ConsentEventORM.id.desc())
        )
    )
    latest: dict[str, ConsentEventORM] = {}
    for event in events:
        latest.setdefault(event.channel, event)
    for event in latest.values():
        if event.status in {"denied", "withdrawn"}:
            return False, f"Do not contact: {event.channel} permission is {event.status}."
    if operations_policy(session, workspace_id).require_recorded_consent and not any(
        event.status == "granted" for event in latest.values()
    ):
        return False, f"Record permission for {channel} before outreach."
    return True, "No contact restriction."


def assert_outreach_allowed(
    session: Session, workspace_id: UUID, email: str, channel: str = "email"
) -> None:
    allowed, reason = outreach_allowed(session, workspace_id, email, channel)
    if not allowed:
        raise OutreachBlocked(reason)
