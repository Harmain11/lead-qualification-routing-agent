"""Record commercial facts separately from AI scoring and external CRM writes."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.domain.routing import NotificationMessage
from lead_agent.infrastructure.db.models import (
    LeadORM,
    LeadOutcomeORM,
    LeadWinNotificationORM,
    ProviderConnectionORM,
    UserORM,
    WorkspaceMembershipORM,
)
from lead_agent.infrastructure.repositories import get_current_routing
from lead_agent.infrastructure.slack import WorkspaceSlackNotificationResolver


def record_outcome(
    session: Session, lead: LeadORM, *, stage: str,
    conversion_type: str | None, revenue: Decimal | None, credited_user_id: UUID | None,
) -> LeadOutcomeORM:
    if credited_user_id is None and lead.outcome:
        credited_user_id = lead.outcome.credited_user_id
    route = get_current_routing(session, lead.id, lead.workspace_id)
    if credited_user_id is None and route and route.assigned_rep_id:
        try:
            candidate = UUID(route.assigned_rep_id)
        except ValueError:
            candidate = None
        if candidate and session.scalar(select(WorkspaceMembershipORM.id).where(
            WorkspaceMembershipORM.workspace_id == lead.workspace_id,
            WorkspaceMembershipORM.user_id == candidate,
            WorkspaceMembershipORM.status == "active",
        )):
            credited_user_id = candidate
    user = None
    if credited_user_id:
        user = session.scalar(select(UserORM).join(
            WorkspaceMembershipORM, WorkspaceMembershipORM.user_id == UserORM.id
        ).where(
            WorkspaceMembershipORM.workspace_id == lead.workspace_id,
            WorkspaceMembershipORM.user_id == credited_user_id,
            WorkspaceMembershipORM.status == "active",
            UserORM.status == "active",
        ))
        if user is None:
            raise ValueError("Credit must belong to an active member of this workspace.")
    now = datetime.now(UTC)
    outcome = lead.outcome
    target = "closed_won" if stage == "converted" else "closed_lost"
    if outcome is None:
        outcome = LeadOutcomeORM(
            id=uuid4(), workspace_id=lead.workspace_id, lead_id=lead.id,
            pipeline_stage=target, currency="USD", data_source="manual",
            created_at=now, updated_at=now, opened_at=lead.received_at,
        )
        lead.outcome = outcome
        session.add(outcome)
    if outcome.pipeline_stage != target or outcome.closed_at is None:
        outcome.closed_at = now
    outcome.pipeline_stage = target
    outcome.conversion_type = conversion_type if stage == "converted" else None
    outcome.closed_revenue = revenue if stage == "converted" else Decimal("0")
    outcome.credited_user = user
    outcome.data_source = "manual"
    outcome.currency = "USD"
    outcome.updated_at = now
    lead.updated_at = now
    session.flush()
    return outcome


def _plain(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace("\n", " ")[:220]
    )


def send_win_notifications(
    session: Session, lead: LeadORM, resolver: WorkspaceSlackNotificationResolver,
    actor_id: str, *, retry: bool = False,
) -> None:
    outcome = lead.outcome
    if outcome is None or outcome.pipeline_stage != "closed_won":
        return
    provider = None
    resolution_error = None
    try:
        provider = resolver.resolve_for_workspace(session, lead.workspace_id, actor_id)
    except (LookupError, ValueError):
        resolution_error = (
            "Slack is unavailable. Verify the workspace connection and selected team channel."
        )
    except Exception:
        resolution_error = "Slack could not be resolved. Check the integration before retrying."
    connection = session.get(ProviderConnectionORM, provider.connection_id) if provider else None
    membership = session.scalar(select(WorkspaceMembershipORM).where(
        WorkspaceMembershipORM.workspace_id == lead.workspace_id,
        WorkspaceMembershipORM.user_id == outcome.credited_user_id,
        WorkspaceMembershipORM.status == "active",
    )) if outcome.credited_user_id else None
    name = outcome.credited_user.display_name if outcome.credited_user else "the sales team"
    amount = (
        f"USD {outcome.closed_revenue:,.2f}"
        if outcome.closed_revenue is not None else "Value not recorded"
    )
    text = (
        f"Congratulations, {_plain(name)}! {_plain(lead.company_name)} converted to a "
        f"{(outcome.conversion_type or 'sale').title()}.\n"
        f"{amount}. Lead: {_plain(lead.full_name)}. Service: {_plain(lead.product_or_service)}."
    )
    for destination in ("channel", "direct_message"):
        row = session.scalar(select(LeadWinNotificationORM).where(
            LeadWinNotificationORM.outcome_id == outcome.id,
            LeadWinNotificationORM.destination == destination,
        ))
        if row is None:
            try:
                with session.begin_nested():
                    row = LeadWinNotificationORM(
                        id=uuid4(), workspace_id=lead.workspace_id, outcome_id=outcome.id,
                        destination=destination, status="queued", attempts=0,
                        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
                    )
                    session.add(row)
                    session.flush()
            except IntegrityError:
                row = session.scalar(select(LeadWinNotificationORM).where(
                    LeadWinNotificationORM.outcome_id == outcome.id,
                    LeadWinNotificationORM.destination == destination,
                ))
        if row is None:
            continue
        claimable = ["queued"] + (["failed", "retryable", "not_configured"] if retry else [])
        claimed = session.execute(update(LeadWinNotificationORM).where(
            LeadWinNotificationORM.id == row.id,
            LeadWinNotificationORM.status.in_(claimable),
        ).values(status="pending", attempts=LeadWinNotificationORM.attempts + 1,
                 updated_at=datetime.now(UTC)))
        session.commit()
        if not claimed.rowcount:
            continue
        session.refresh(row)
        error = resolution_error
        if provider is None:
            error = error or (
                "Connect and verify Slack, then select the team notification "
                "channel in Integrations."
            )
        elif destination == "direct_message":
            if membership is None or not membership.slack_member_id:
                error = "Link the credited member's Slack member ID in Users & permissions."
            elif (
                connection is None
                or membership.slack_team_id != connection.connection_metadata.get("slack_team_id")
            ):
                error = (
                    "The member's Slack link belongs to a different Slack workspace. "
                    "Relink it in Users & permissions."
                )
        if error:
            row.status, row.error_message = "not_configured", error
        else:
            message = NotificationMessage(
                deduplication_key=f"win:{outcome.id}:{destination}",
                priority="normal", text=text,
            )
            row.recipient_id = (
                provider.channel_id if destination == "channel" else membership.slack_member_id
            )
            try:
                delivery = (
                    provider.send(message) if destination == "channel"
                    else provider.send_direct(membership.slack_member_id, message)
                )
                row.status = delivery.state
                row.external_message_id = delivery.external_id
                row.error_message = delivery.error_message
            except Exception:
                # Never turn a recorded win into an API failure or claim delivery
                # succeeded when an external call's outcome is uncertain.
                row.status = "delivery_uncertain"
                row.error_message = (
                    "Slack delivery could not be confirmed. "
                    "Check Slack before sending another message."
                )
        row.updated_at = datetime.now(UTC)
        session.commit()
    session.expire(outcome, ["win_notifications"])
