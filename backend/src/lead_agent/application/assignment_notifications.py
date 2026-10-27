from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.infrastructure.db.models import (
    AssignmentNotificationORM,
    UserORM,
    WorkspaceMembershipORM,
)


def notify_assigned_member(
    session: Session,
    *,
    workspace_id: UUID,
    lead_id: UUID,
    recipient_user_id: UUID,
    assigned_by_user_id: UUID | None = None,
) -> AssignmentNotificationORM | None:
    return notify_lead_action(
        session,
        workspace_id=workspace_id,
        lead_id=lead_id,
        recipient_user_id=recipient_user_id,
        kind="assignment",
        message=None,
        assigned_by_user_id=assigned_by_user_id,
    )


def notify_lead_action(
    session: Session,
    *,
    workspace_id: UUID,
    lead_id: UUID,
    recipient_user_id: UUID,
    kind: str,
    message: str | None,
    assigned_by_user_id: UUID | None = None,
) -> AssignmentNotificationORM | None:
    active_member = session.scalar(
        select(WorkspaceMembershipORM.id)
        .join(UserORM, UserORM.id == WorkspaceMembershipORM.user_id)
        .where(
            WorkspaceMembershipORM.workspace_id == workspace_id,
            WorkspaceMembershipORM.user_id == recipient_user_id,
            WorkspaceMembershipORM.status == "active",
            UserORM.status == "active",
        )
    )
    if active_member is None:
        return None

    notification = AssignmentNotificationORM(
        id=uuid4(),
        workspace_id=workspace_id,
        recipient_user_id=recipient_user_id,
        lead_id=lead_id,
        assigned_by_user_id=assigned_by_user_id,
        kind=kind,
        message=message[:500] if message else None,
        created_at=datetime.now(UTC),
    )
    session.add(notification)
    return notification
