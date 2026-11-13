from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.application.assignment_notifications import notify_lead_action
from lead_agent.application.email_templates import (
    lead_signoff,
    apply_workspace_email_signature,
    get_email_template,
)
from lead_agent.application.governance import (
    OutreachBlocked,
    assert_outreach_allowed,
    operations_policy,
)
from lead_agent.application.meetings import (
    MeetingIntentUnavailable,
    _plain_excerpt,
    find_meeting_request,
    lead_time_zone,
    match_confirmed_meeting_option,
    meeting_reply_declines_offer,
    meeting_reply_has_uncertain_language,
    meeting_reply_requests_other_times,
    recommend_calendly_slots,
    recommend_google_calendar_slots,
    requested_meeting_date,
    requested_meeting_datetime,
    requested_meeting_time,
)
from lead_agent.domain.follow_up import FollowUpEmailContent
from lead_agent.domain.meeting_reply import MeetingReplyClassification
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.calendar_providers import (
    CalendlyClient,
    GoogleCalendarClient,
)
from lead_agent.infrastructure.db.models import (
    CrmProviderRecordLinkORM,
    LeadFollowUpEmailORM,
    LeadORM,
    PipelineEventORM,
    ProviderConnectionORM,
    RoutingDecisionORM,
    WorkspaceEmailAutomationSettingORM,
    WorkspaceORM,
)
from lead_agent.infrastructure.db.operations import LeadNoteORM
from lead_agent.infrastructure.gmail import (
    GMAIL_SEND_SCOPE,
    GmailInboundProvider,
    contact_phone_from_gmail_messages,
)
from lead_agent.infrastructure.oauth import get_oauth_broker
from lead_agent.infrastructure.provider_lifecycle import WorkspaceAuthorizedConnectionResolver
from lead_agent.infrastructure.reasoning.factory import resolve_workspace_reasoning_provider
from lead_agent.infrastructure.secrets import get_secret_store

_LOGGER = logging.getLogger("lead_agent.meeting_email")
_OFFER_STAGE = "meeting_email_offer"
_OFFER_PENDING = "awaiting_confirmation"
_OFFER_AWAITING_SEND = "awaiting_send"


def _find_meeting_request_for_draft(*args: Any, **kwargs: Any) -> dict[str, str] | None:
    try:
        return find_meeting_request(*args, **kwargs)
    except MeetingIntentUnavailable:
        raise HTTPException(
            status_code=503,
            detail=(
                "AI could not check the Gmail thread. Check the workspace reasoning "
                "provider, then retry."
            ),
        ) from None


def _find_meeting_request_in_background(*args: Any, **kwargs: Any) -> dict[str, str] | None:
    try:
        return find_meeting_request(*args, **kwargs)
    except MeetingIntentUnavailable:
        _LOGGER.warning("AI could not check a Gmail thread during meeting processing")
        return None


_MAX_REQUEST_AGE = timedelta(days=7)
_MAX_PENDING_OFFERS_PER_TICK = 100


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _authorized_credentials(
    session: Session,
    workspace_id: UUID,
    connection: ProviderConnectionORM,
    actor_id: str,
) -> dict[str, str]:
    resolved = WorkspaceAuthorizedConnectionResolver(
        get_secret_store(), get_oauth_broker()
    ).resolve_connection(
        session,
        workspace_id,
        connection.id,
        actor_id=actor_id,
    )
    if resolved is None or not resolved.credentials.get("access_token"):
        raise ProviderOperationError(
            f"The connected {connection.provider_type.replace('_', ' ')} account "
            "has no usable access token.",
            provider=connection.provider_type,
            code=f"{connection.provider_type}_access_token_unavailable",
            category="invalid_credentials",
        )
    return resolved.credentials


def _safe_booking_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    url = value.strip()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return url


def meeting_booking_link(
    session: Session,
    workspace_id: UUID,
    connection: ProviderConnectionORM,
    actor_id: str,
) -> str:
    """Resolve the public booking page for the preferred connected calendar."""
    if connection.provider_type == "google_calendar":
        setting = session.get(WorkspaceEmailAutomationSettingORM, workspace_id)
        url = _safe_booking_url(setting.google_calendar_booking_url if setting else "")
        if not url:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Add a Google Calendar appointment-schedule link in "
                    "Settings → Company → Email templates before creating this draft."
                ),
            )
        parsed = urlsplit(url)
        google_calendar_url = (
            parsed.hostname == "calendar.app.google" and parsed.path.strip("/") != ""
        ) or (
            parsed.hostname == "calendar.google.com" and "/appointments/schedules/" in parsed.path
        )
        if not google_calendar_url:
            raise HTTPException(
                status_code=409,
                detail="The saved Google Calendar booking link is not a valid appointment link.",
            )
        return url

    if connection.provider_type != "calendly":
        raise HTTPException(status_code=409, detail="No supported booking link is connected.")
    credentials = _authorized_credentials(session, workspace_id, connection, actor_id)
    client = CalendlyClient(credentials["access_token"])
    user = client.current_user()
    user_uri = str(user.get("uri") or "")
    event_type_url = ""
    if user_uri:
        for event_type in client.event_types(user_uri):
            if event_type.get("active") is False:
                continue
            event_type_url = _safe_booking_url(event_type.get("scheduling_url"))
            if event_type_url:
                break
    url = _safe_booking_url(user.get("scheduling_url")) or event_type_url
    if not url:
        raise HTTPException(
            status_code=409,
            detail=(
                "Calendly is connected but has no public scheduling link. "
                "Set up an active Calendly event type, then try again."
            ),
        )
    calendly_host = urlsplit(url).hostname or ""
    if not (calendly_host == "calendly.com" or calendly_host.endswith(".calendly.com")):
        raise HTTPException(
            status_code=409,
            detail="The connected Calendly account did not return a Calendly booking page.",
        )
    return url


def _active_connection(
    session: Session, workspace_id: UUID, provider: str
) -> ProviderConnectionORM | None:
    return session.scalar(
        select(ProviderConnectionORM)
        .where(
            ProviderConnectionORM.workspace_id == workspace_id,
            ProviderConnectionORM.provider_type == provider,
            ProviderConnectionORM.is_active.is_(True),
            ProviderConnectionORM.status == "verified",
        )
        .order_by(ProviderConnectionORM.updated_at.desc())
    )


def _connection_gmail_email(connection: ProviderConnectionORM) -> str:
    metadata = (
        connection.connection_metadata if isinstance(connection.connection_metadata, dict) else {}
    )
    for key in ("gmail_email", "email", "account_email"):
        value = metadata.get(key)
... (+2467 lines) [see remaining: rtk proxy git show HEAD:backend/src/lead_agent/application/meeting_email.py | tail -n +238]
