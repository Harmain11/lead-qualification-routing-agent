from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.domain.routing import NotificationMessage
from lead_agent.infrastructure.db.models import (
    AssignmentNotificationORM,
    LeadORM,
    UserORM,
    WorkspaceMembershipORM,
)

_LOGGER = logging.getLogger("lead_agent.assignment_notifications")


def notify_assigned_member(
    session: Session,
    *,
    workspace_id: UUID,
    lead_id: UUID,
    recipient_user_id: UUID,
    assigned_by_user_id: UUID | None = None,
) -> AssignmentNotificationORM | None:
    notification = notify_lead_action(
        session,
        workspace_id=workspace_id,
        lead_id=lead_id,
        recipient_user_id=recipient_user_id,
        kind="assignment",
        message=None,
        assigned_by_user_id=assigned_by_user_id,
    )
    if notification is not None:
        _notify_slack_assignee(
            session,
            workspace_id=workspace_id,
            lead_id=lead_id,
            recipient_user_id=recipient_user_id,
            assigned_by_user_id=assigned_by_user_id,
            assignment_notification_id=notification.id,
        )
    return notification


def _notify_slack_assignee(
    session: Session,
    *,
    workspace_id: UUID,
    lead_id: UUID,
    recipient_user_id: UUID,
    assigned_by_user_id: UUID | None,
    assignment_notification_id: UUID,
) -> None:
    """Best-effort DM to the member linked to the assignment.

    Assignment and CRM writes must never fail just because Slack is not
    connected, the member is not linked, or Slack rejects a message. The
    in-app assignment notification remains the durable source of truth.
    """
    membership = session.scalar(
        select(WorkspaceMembershipORM).where(
            WorkspaceMembershipORM.workspace_id == workspace_id,
            WorkspaceMembershipORM.user_id == recipient_user_id,
            WorkspaceMembershipORM.status == "active",
        )
    )
    if membership is None or not membership.slack_member_id:
        return

    try:
        from lead_agent.infrastructure.slack import get_workspace_slack_notification_resolver

        provider = get_workspace_slack_notification_resolver().resolve_for_direct_message(
            session, workspace_id, slack_team_id=membership.slack_team_id
        )
        if provider is None:
            return
        lead = session.get(LeadORM, lead_id)
        if lead is None:
            return
        assigned_by = session.get(UserORM, assigned_by_user_id) if assigned_by_user_id else None
        actor = assigned_by.display_name if assigned_by else "Corral routing"
        lead_label = lead.company_name or lead.full_name or lead.email
        message = NotificationMessage(
            deduplication_key=f"assignment:{assignment_notification_id}",
            priority="normal",
            text=(
                f"You were assigned a lead in Corral.\n\n"
                f"Lead: {lead_label}\n"
                f"Contact: {lead.full_name} · {lead.email}\n"
                f"Assigned by: {actor}\n\n"
                "Open Corral to review the lead and take the next action."
            ),
        )
        delivery = provider.send_direct(membership.slack_member_id, message)
        if delivery.state not in {"sent", "retryable"}:
            _LOGGER.warning("Slack assignment DM failed for %s: %s", recipient_user_id, delivery.error_message)
    except Exception:
        _LOGGER.exception("Slack assignment DM could not be delivered for %s", recipient_user_id)


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
