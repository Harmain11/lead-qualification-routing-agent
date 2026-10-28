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
        if isinstance(value, str) and "@" in value:
            return value.strip().lower()
    account_name = str(connection.account_name or "").strip().lower()
    return account_name if "@" in account_name else ""


def _active_gmail_connection_for_source(
    session: Session,
    workspace_id: UUID,
    source_connection_id: str,
) -> ProviderConnectionORM | None:
    try:
        source_id = UUID(source_connection_id)
    except (TypeError, ValueError):
        return None
    source_connection = session.scalar(
        select(ProviderConnectionORM).where(
            ProviderConnectionORM.id == source_id,
            ProviderConnectionORM.workspace_id == workspace_id,
            ProviderConnectionORM.provider_type == "gmail",
        )
    )
    if source_connection is None:
        return None
    if source_connection.is_active and source_connection.status == "verified":
        return source_connection

    source_email = _connection_gmail_email(source_connection)
    active_connections = session.scalars(
        select(ProviderConnectionORM)
        .where(
            ProviderConnectionORM.workspace_id == workspace_id,
            ProviderConnectionORM.provider_type == "gmail",
            ProviderConnectionORM.is_active.is_(True),
            ProviderConnectionORM.status == "verified",
        )
        .order_by(ProviderConnectionORM.updated_at.desc())
    )
    for connection in active_connections:
        same_external_account = bool(
            source_connection.external_account_id
            and connection.external_account_id
            and source_connection.external_account_id == connection.external_account_id
        )
        same_mailbox = bool(source_email and source_email == _connection_gmail_email(connection))
        if same_external_account or same_mailbox:
            return connection
    return None


def _thread_offer(
    session: Session,
    workspace_id: UUID,
    lead_id: UUID,
    thread_id: str,
    gmail_connection_id: str | None = None,
) -> PipelineEventORM | None:
    events = session.scalars(
        select(PipelineEventORM)
        .where(
            PipelineEventORM.workspace_id == workspace_id,
            PipelineEventORM.lead_id == lead_id,
            PipelineEventORM.stage == _OFFER_STAGE,
        )
        .order_by(PipelineEventORM.created_at.desc())
        .limit(50)
    )
    for event in events:
        details = event.details if isinstance(event.details, dict) else {}
        if details.get("thread_id") != thread_id:
            continue
        stored_connection_id = str(details.get("gmail_connection_id") or "")
        if gmail_connection_id is None or stored_connection_id == gmail_connection_id:
            return event
        reconnected_connection = _active_gmail_connection_for_source(
            session, workspace_id, stored_connection_id
        )
        if (
            reconnected_connection is not None
            and str(reconnected_connection.id) == gmail_connection_id
        ):
            return event
    return None


def can_replace_unbooked_offer(offer: PipelineEventORM | None) -> bool:
    """Whether a deliberate new slot can safely supersede the current offer."""
    if offer is None:
        return False
    if offer.status == _OFFER_PENDING:
        return True
    if offer.status != "needs_attention":
        return False
    details = offer.details if isinstance(offer.details, dict) else {}
    return (
        not details.get("booking_error")
        and details.get("selected_option") is None
        and details.get("error_code") != "gmail_meeting_offer_delivery_uncertain"
    )


def record_gmail_thread_messages(
    session: Session,
    *,
    workspace_id: UUID,
    lead_id: UUID,
    gmail_connection_id: UUID,
    messages: list[dict[str, str]],
) -> int:
    from lead_agent.application.gmail_history import append_gmail_thread_messages

    return append_gmail_thread_messages(
        session,
        workspace_id=workspace_id,
        lead_id=lead_id,
        connection_id=gmail_connection_id,
        messages=messages,
    )


def _format_slot(index: int, slot: dict[str, Any]) -> str:
    start_at = _parse_datetime(slot.get("start_at"))
    if start_at is None:
        raise ValueError("A proposed meeting slot has an invalid start time.")
    time_zone = str(slot.get("time_zone") or "UTC")
    local_time = start_at.astimezone(ZoneInfo(time_zone))
    date_label = local_time.strftime("%A, %B ")
    time_label = local_time.strftime("%I:%M %p").lstrip("0")
    label = f"{date_label}{local_time.day}, {local_time.year} at {time_label}"
    event_type_name = str(slot.get("event_type_name") or "").strip()
    suffix = f" — {event_type_name}" if event_type_name else ""
    return f"{index}. {label} ({time_zone}){suffix}"


def _offer_email(
    lead: LeadORM,
    source: dict[str, str],
    provider: str,
    slots: list[dict[str, Any]],
) -> tuple[str, str]:
    source_subject = source.get("subject", "").strip()
    subject = source_subject or "Meeting times"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    first_name = (lead.full_name.strip().split() or ["there"])[0]
    provider_name = "Google Calendar" if provider == "google_calendar" else "Calendly"
    booking_url = next(
        (
            _safe_booking_url(slot.get("booking_url"))
            for slot in slots
            if isinstance(slot, dict) and _safe_booking_url(slot.get("booking_url"))
        ),
        "",
    )
    if booking_url:
        context = _personalized_meeting_context(lead, source)
        time_note = ""
        selected = next(
            (slot for slot in slots if isinstance(slot, dict) and slot.get("start_at")),
            None,
        )
        if selected is not None:
            time_note = (
                f"\n\nOne time that may work is "
                f"{_format_slot(1, selected).removeprefix('1. ')}. "
                "The booking page shows the current available times."
            )
        body = (
            f"Hi {first_name},\n\n"
            f"{context} I’d be glad to talk this through with you.\n\n"
            f"Choose a time that works for you using my {provider_name} booking link:\n"
            f"{booking_url}{time_note}\n\n"
            "Looking forward to speaking with you.\n\n"
            "Best,\nThe team"
        )
        return subject, body

    options = "\n".join(_format_slot(index, slot) for index, slot in enumerate(slots, 1))
    requested_text = source.get("excerpt", "")
    reference = _parse_datetime(source.get("sent_at"))
    time_zone = str(slots[0].get("time_zone") or "UTC") if slots else "UTC"
    contact_zone = lead_time_zone(lead)
    requested_date = requested_meeting_date(
        requested_text, reference=reference, time_zone=time_zone
    )
    if requested_date is not None and requested_meeting_time(requested_text) is not None:
        introduction = (
            "I couldn't safely confirm the exact time because your time zone isn't clear. "
            "Here are the available options:"
            if contact_zone is None
            else "I couldn't confirm the exact time you requested. Here are the available options:"
        )
    elif requested_date is not None:
        offered_dates = set()
        for slot in slots:
            start_at = _parse_datetime(slot.get("start_at"))
            if start_at is None:
                continue
            try:
                slot_zone = ZoneInfo(str(slot.get("time_zone") or "UTC"))
            except (ZoneInfoNotFoundError, ValueError):
                slot_zone = ZoneInfo("UTC")
            offered_dates.add(start_at.astimezone(slot_zone).date())
        date_label = f"{requested_date.strftime('%B')} {requested_date.day}"
        if requested_date in offered_dates:
            introduction = f"Here are the available times for {date_label}:"
        else:
            introduction = (
                f"There are no open times on {date_label}. Here are the next available options:"
            )
    else:
        introduction = "Thanks for asking to meet. Here are some available times:"
    timezone_note = (
        f"\n\nI couldn't reliably identify your time zone. These options are shown in "
        f"{time_zone}. Please confirm that time zone is correct before choosing a slot."
        if contact_zone is None
        else ""
    )
    reply_instruction = (
        "Reply with 1 to confirm. "
        if len(slots) == 1
        else "Reply with the option number (for example, 2) to confirm. "
    )
    body = (
        f"Hi {first_name},\n\n"
        f"{introduction}{timezone_note}\n\n"
        f"{options}\n\n"
        f"{reply_instruction}"
        f"We’ll add the meeting to {provider_name} and send you a calendar invitation. "
        "If none of these times work, reply “none” and Corral will flag it for your team.\n\n"
        "Best,\nThe team"
    )
    return subject, body


def _ai_booking_email(
    session: Session,
    lead: LeadORM,
    source: dict[str, str],
    provider_name: str,
    booking_url: str,
) -> tuple[str, str, str]:
    """Ground a booking reply in the actual lead and its selected Gmail thread."""
    thread_rows = list(
        session.scalars(
            select(PipelineEventORM)
            .where(
                PipelineEventORM.workspace_id == lead.workspace_id,
                PipelineEventORM.lead_id == lead.id,
                PipelineEventORM.stage == "gmail_message",
            )
            .order_by(PipelineEventORM.created_at.desc())
            .limit(100)
        )
    )
    conversation: list[dict[str, str]] = []
    for row in reversed(thread_rows):
        details = row.details if isinstance(row.details, dict) else {}
        if str(details.get("thread_id") or "") != str(source["thread_id"]) or str(
            details.get("gmail_connection_id") or ""
        ) != str(source["gmail_connection_id"]):
            continue
        conversation.append(
            {
                "role": "workspace" if details.get("direction") == "outbound" else "lead",
                "sent_at": str(details.get("sent_at") or ""),
                "subject": str(details.get("subject") or "")[:200],
                "body": _plain_excerpt(
                    str(details.get("body") or details.get("snippet") or ""), limit=1500
                ),
            }
        )
    notes = list(
        session.scalars(
            select(LeadNoteORM)
            .where(
                LeadNoteORM.workspace_id == lead.workspace_id,
                LeadNoteORM.lead_id == lead.id,
            )
            .order_by(LeadNoteORM.updated_at.desc())
            .limit(5)
        )
    )
    profile = operations_policy(session, lead.workspace_id).business_profile
    workspace = session.get(WorkspaceORM, lead.workspace_id)
    template = get_email_template(session, lead.workspace_id, "follow_up")
    facts = {
        "email_purpose": "meeting_booking",
        "lead": {
            "name": lead.full_name,
            "company": lead.company_name,
            "industry": lead.industry,
            "job_title": lead.job_title,
            "service_interest": lead.product_or_service,
            "original_inquiry": lead.message[:2000],
        },
        "meeting_request": {
            "subject": source.get("subject", ""),
            "body": source.get("excerpt", ""),
            "sent_at": source.get("sent_at", ""),
        },
        "gmail_conversation": conversation[-12:],
        "newest_team_notes": [
            {"at": note.updated_at.isoformat(), "body": note.body[:1000]} for note in notes
        ],
        "booking_url": booking_url,
        "calendar_provider": provider_name,
        "workspace_business_profile": profile.model_dump(mode="json"),
        "workspace_email_template": {"subject": template["subject"], "body": template["body"]},
    }
    try:
        provider = resolve_workspace_reasoning_provider(session, lead.workspace_id)
        draft = FollowUpEmailContent.model_validate(provider.draft_follow_up(facts))
        body = draft.body.strip()
        if not body or len(body) > 10000:
            raise ValueError("Invalid AI booking draft")
        if booking_url not in body:
            body = f"{body}\n\nBook a time here: {booking_url}"
        body = apply_workspace_email_signature(
            body, profile, fallback_business_name=workspace.name if workspace else ""
        )
    except Exception as error:
        _LOGGER.warning("AI appointment draft unavailable: %s", type(error).__name__)
        raise HTTPException(
            status_code=503,
            detail=(
                "AI could not prepare the personalized appointment draft. "
                "Check the workspace AI connection and retry."
            ),
        ) from error
    source_subject = source.get("subject", "").strip() or "Meeting times"
    subject = (
        source_subject if source_subject.lower().startswith("re:") else f"Re: {source_subject}"
    )
    return subject, body, draft.rationale


def _personalized_meeting_context(lead: LeadORM, source: dict[str, str]) -> str:
    subject = re.sub(
        r"^(?:(?:re|fw|fwd)\s*:\s*)+",
        "",
        str(source.get("subject") or "").strip(),
        flags=re.IGNORECASE,
    ).strip()
    generic_subjects = {
        "meeting",
        "meeting request",
        "schedule a meeting",
        "meeting times",
        "call",
    }
    if subject and subject.casefold() not in generic_subjects:
        return f"Thanks for reaching out about {subject[:120]}."
    excerpt = _plain_excerpt(str(source.get("excerpt") or ""), limit=240)
    excerpt = re.sub(
        r"^(?:hi|hello|dear)\s+[^,!.]{1,60}[,!.:\s-]+",
        "",
        excerpt,
        flags=re.IGNORECASE,
    ).strip(" \t\r\n.,!?;:")
    if excerpt:
        sentence = re.split(r"(?<=[.!?])\s+", excerpt, maxsplit=1)[0]
        if len(sentence) > 160:
            sentence = sentence[:157].rsplit(" ", 1)[0] + "…"
        return f"Thanks for your note. I read your message about “{sentence}”."
    company = str(getattr(lead, "company_name", "") or "").strip()
    if company:
        return f"Thanks for reaching out about what {company} is looking for."
    return "Thanks for reaching out."


def manual_selected_slot_body_is_intact(details: dict[str, Any], body: str) -> bool:
    """Keep the booking URL intact, or preserve legacy numbered offers."""
    expected_url = _safe_booking_url(details.get("booking_link_url"))
    if expected_url:
        return expected_url in body
    if not details.get("manual_selected_slot"):
        return True
    options = details.get("options")
    if not isinstance(options, list) or len(options) != 1 or not isinstance(options[0], dict):
        return False
    expected = _format_slot(1, options[0]).casefold()
    numbered_lines = [
        line.strip().casefold() for line in body.splitlines() if re.match(r"^\s*\d+[.)]\s+", line)
    ]
    return numbered_lines == [expected]


def _calendar_options(
    session: Session,
    workspace_id: UUID,
    source: dict[str, str],
    actor_id: str,
    *,
    lead: LeadORM | None = None,
) -> tuple[ProviderConnectionORM, list[dict[str, Any]]] | None:
    del lead
    connection = _active_connection(session, workspace_id, "calendly")
    if connection is None:
        connection = _active_connection(session, workspace_id, "google_calendar")
    if connection is None:
        return None
    try:
        booking_url = meeting_booking_link(session, workspace_id, connection, actor_id)
    except (HTTPException, ProviderOperationError) as error:
        error_code = str(error.detail) if isinstance(error, HTTPException) else error.code
        _LOGGER.warning(
            "meeting_email.booking_link_unavailable",
            extra={
                "fields": {
                    "workspace_id": str(workspace_id),
                    "provider": connection.provider_type,
                    "error": error_code,
                }
            },
        )
        return None
    return connection, [
        {
            "provider": connection.provider_type,
            "connection_id": str(connection.id),
            "account_name": connection.account_name,
            "event_type_uri": "",
            "event_type_name": "",
            "source_message_id": source.get("message_id", ""),
            "source_gmail_connection_id": source.get("gmail_connection_id", ""),
            "booking_url": booking_url,
            "booking_link_mode": True,
        }
    ]


def _attempt_requested_time_booking(
    session: Session,
    lead: LeadORM,
    workspace_id: UUID,
    actor_id: str,
    source_message_id: str,
    source_gmail_connection_id: str | None,
    request_text: str,
    reference: datetime | None,
) -> bool:
    """Book a clearly stated future date/time only if a connected calendar accepts it."""
    if requested_meeting_time(request_text) is None:
        return False
    contact_zone = lead_time_zone(lead)
    if contact_zone is None:
        return False
    from lead_agent.api.routes.meetings import ConfirmMeetingRequest, confirm_meeting

    workspace = WorkspaceContext(
        workspace_id=workspace_id,
        actor_id=actor_id,
        role="owner",
    )
    for provider_name in ("google_calendar", "calendly"):
        connection = _active_connection(session, workspace_id, provider_name)
        if connection is None:
            continue
        try:
            credentials = _authorized_credentials(session, workspace_id, connection, actor_id)
            event_type_uri: str | None = None
            event_type_name: str | None = None
            if provider_name == "google_calendar":
                client = GoogleCalendarClient(credentials["access_token"])
                client.primary_calendar()
                time_zone = contact_zone
                if (
                    requested_meeting_date(request_text, reference=reference, time_zone=time_zone)
                    is None
                ):
                    continue
                start_at = requested_meeting_datetime(
                    request_text, time_zone=time_zone, reference=reference
                )
                if start_at is None or start_at <= datetime.now(UTC) + timedelta(minutes=5):
                    continue
                end_at = start_at + timedelta(minutes=30)
            else:
                client = CalendlyClient(credentials["access_token"])
                user = client.current_user()
                user_uri = str(user.get("uri") or "")
                time_zone = contact_zone
                if (
                    requested_meeting_date(request_text, reference=reference, time_zone=time_zone)
                    is None
                ):
                    continue
                start_at = requested_meeting_datetime(
                    request_text, time_zone=time_zone, reference=reference
                )
                if start_at is None or start_at <= datetime.now(UTC) + timedelta(minutes=5):
                    continue
                event_types = client.event_types(user_uri) if user_uri else []
                available_event_type = None
                for event_type in event_types:
                    uri = str(event_type.get("uri") or "")
                    if not uri or event_type.get("active") is False:
                        continue
                    try:
                        duration = int(event_type.get("duration") or 0)
                    except (TypeError, ValueError):
                        continue
                    if duration < 5 or duration > 180:
                        continue
                    try:
                        available = client.available_times(
                            uri,
                            max(
                                datetime.now(UTC) + timedelta(minutes=5),
                                start_at - timedelta(minutes=1),
                            ),
                            start_at + timedelta(minutes=duration + 1),
                        )
                    except ProviderOperationError as error:
                        if error.code != "calendly_external_calendar_unavailable":
                            raise
                        continue
                    matching = any(
                        (slot_start := _parse_datetime(slot.get("start_time"))) is not None
                        and slot_start.astimezone(UTC) == start_at.astimezone(UTC)
                        for slot in available
                    )
                    if matching:
                        available_event_type = (event_type, duration)
                        break
                if available_event_type is None:
                    continue
                event_type, duration = available_event_type
                event_type_uri = str(event_type["uri"])
                event_type_name = str(event_type.get("name") or "Calendly meeting")
                end_at = start_at + timedelta(minutes=duration)

            booking_request = ConfirmMeetingRequest(
                provider=provider_name,
                connection_id=connection.id,
                source_message_id=source_message_id,
                source_gmail_connection_id=source_gmail_connection_id,
                start_at=start_at,
                end_at=end_at,
                time_zone=time_zone,
                event_type_uri=event_type_uri,
                event_type_name=event_type_name,
            )
            confirm_meeting(
                lead.id,
                booking_request,
                session=session,
                workspace=workspace,
            )
            return True
        except HTTPException as error:
            if error.status_code != 409:
                _LOGGER.warning(
                    "meeting_email.exact_time_booking_failed",
                    extra={
                        "fields": {
                            "workspace_id": str(workspace_id),
                            "lead_id": str(lead.id),
                            "provider": provider_name,
                            "status_code": error.status_code,
                        }
                    },
                )
        except ProviderOperationError as error:
            _LOGGER.warning(
                "meeting_email.exact_time_calendar_unavailable",
                extra={
                    "fields": {
                        "workspace_id": str(workspace_id),
                        "lead_id": str(lead.id),
                        "provider": provider_name,
                        "error_code": error.code,
                    }
                },
            )
    return False


def _notify_meeting_draft(
    session: Session,
    *,
    workspace_id: UUID,
    lead: LeadORM,
) -> None:
    decision = session.scalar(
        select(RoutingDecisionORM)
        .where(
            RoutingDecisionORM.lead_id == lead.id,
            RoutingDecisionORM.workspace_id == workspace_id,
            RoutingDecisionORM.is_current.is_(True),
        )
        .order_by(RoutingDecisionORM.created_at.desc())
    )
    if decision is None or not decision.assigned_rep_id:
        return
    try:
        recipient_user_id = UUID(decision.assigned_rep_id)
    except ValueError:
        return
    notify_lead_action(
        session,
        workspace_id=workspace_id,
        lead_id=lead.id,
        recipient_user_id=recipient_user_id,
        kind="meeting_reply_draft",
        message=(
            f"{lead.full_name} asked to meet. Review the personalized booking-link "
            "draft in this lead’s Emails tab and send it when ready."
        ),
    )


def _send_options(
    session: Session,
    lead: LeadORM,
    workspace_id: UUID,
    gmail_connection: ProviderConnectionORM,
    messages: list[dict[str, str]],
    source: dict[str, str],
    actor_id: str,
) -> str:
    thread_id = source.get("thread_id", "").strip()
    if not thread_id:
        return "no_thread"
    existing_offer = _thread_offer(
        session, workspace_id, lead.id, thread_id, str(gmail_connection.id)
    )
    existing_details = existing_offer.details if existing_offer is not None else {}
    if (
        existing_offer is not None
        and isinstance(existing_details, dict)
        and existing_details.get("source_message_id") == source["message_id"]
        and existing_offer.status
        in {"sending", _OFFER_AWAITING_SEND, _OFFER_PENDING, "booking", "booked"}
    ):
        return "already_offered"
    sent_at = _parse_datetime(source.get("sent_at"))
    now = datetime.now(UTC)
    if sent_at is None or sent_at < now - _MAX_REQUEST_AGE or sent_at > now + timedelta(minutes=5):
        return "stale_request"

    selected_calendar = _calendar_options(session, workspace_id, source, actor_id, lead=lead)
    if selected_calendar is None:
        return "no_calendar_availability"
    calendar_connection, slots = selected_calendar
    provider = calendar_connection.provider_type

    offer = PipelineEventORM(
        id=uuid4(),
        workspace_id=workspace_id,
        lead_id=lead.id,
        stage=_OFFER_STAGE,
        status=_OFFER_AWAITING_SEND,
        details={
            "thread_id": thread_id,
            "source_message_id": source["message_id"],
            "source_subject": source.get("subject", ""),
            "source_rfc_message_id": source.get("rfc_message_id", ""),
            "gmail_connection_id": str(gmail_connection.id),
            "calendar_provider": provider,
            "calendar_connection_id": str(calendar_connection.id),
            "options": [],
            "booking_link_mode": True,
            "booking_link_url": str(slots[0].get("booking_url") or ""),
            "created_at": now.isoformat(),
        },
        created_at=now,
    )
    subject, body = _offer_email(lead, source, provider, slots)
    try:
        assert_outreach_allowed(session, workspace_id, lead.email)
    except OutreachBlocked as error:
        offer.status = "needs_attention"
        offer.details = {
            **offer.details,
            "error_code": "outreach_blocked",
            "error_message": str(error)[:500],
        }
        session.add(offer)
        session.commit()
        return "needs_attention"

    draft = LeadFollowUpEmailORM(
        id=uuid4(),
        workspace_id=workspace_id,
        lead_id=lead.id,
        provider_connection_id=gmail_connection.id,
        kind=f"meeting_options:{offer.id}",
        recipient=lead.email,
        subject=subject,
        body=body,
        rationale="Personalized meeting booking link prepared for human review before sending.",
        generation_method="meeting_request",
        generation_note="Meeting booking link",
        status="draft",
        attempts=0,
        created_at=now,
        updated_at=now,
    )
    offer.details = {
        **offer.details,
        "email_draft_id": str(draft.id),
    }
    session.add(offer)
    session.add(draft)

    _notify_meeting_draft(session, workspace_id=workspace_id, lead=lead)
    session.commit()
    return "draft_created"


def _slot_identity(slot: dict[str, Any]) -> tuple[str, ...]:
    start_at = _parse_datetime(slot.get("start_at"))
    end_at = _parse_datetime(slot.get("end_at"))
    return (
        str(slot.get("provider") or ""),
        str(slot.get("connection_id") or ""),
        str(slot.get("source_message_id") or ""),
        str(slot.get("source_gmail_connection_id") or ""),
        start_at.astimezone(UTC).isoformat() if start_at else "",
        end_at.astimezone(UTC).isoformat() if end_at else "",
        str(slot.get("time_zone") or ""),
        str(slot.get("event_type_uri") or ""),
    )


def _cached_reply_interpretation(details: dict[str, Any], message_id: str) -> dict[str, Any] | None:
    cache = details.get("reply_classifications")
    if not isinstance(cache, dict):
        return None
    result = cache.get(message_id)
    if not isinstance(result, dict):
        return None
    intent = result.get("intent")
    option_number = result.get("option_number")
    if (
        intent not in {"confirm", "decline", "uncertain"}
        or not isinstance(option_number, int)
        or isinstance(option_number, bool)
    ):
        return None
    return result


def _has_newer_inbound_reply(
    messages: list[dict[str, str]],
    *,
    current_message_id: str,
    lead_email: str,
    current_sent_at: datetime,
) -> bool:
    for message in messages:
        if (
            str(message.get("message_id") or "") == current_message_id
            or message.get("direction") != "inbound"
            or message.get("sender", "").strip().lower() != lead_email.strip().lower()
        ):
            continue
        sent_at = _parse_datetime(message.get("sent_at"))
        if sent_at is not None and sent_at > current_sent_at:
            return True
    return False


def _store_reply_interpretation(
    details: dict[str, Any],
    message_id: str,
    *,
    intent: str,
    option_number: int,
    confidence: float,
    source: str,
) -> dict[str, Any]:
    updated = dict(details)
    raw_cache = details.get("reply_classifications")
    cache = dict(raw_cache) if isinstance(raw_cache, dict) else {}
    cache[message_id] = {
        "intent": intent,
        "option_number": option_number,
        "confidence": confidence,
        "source": source,
    }
    while len(cache) > 20:
        cache.pop(next(iter(cache)))
    updated["reply_classifications"] = cache
    return updated


def _normalized_reply_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _quoted_evidence_is_present(reply: str, evidence: str) -> bool:
    normalized_evidence = _normalized_reply_text(evidence)
    return bool(normalized_evidence) and normalized_evidence in _normalized_reply_text(reply)


def _option_reference_from_evidence(evidence: str, options: list[dict[str, Any]]) -> int | None:
    text = evidence.casefold()
    ordinal_values = {
        "first": 1,
        "1st": 1,
        "second": 2,
        "2nd": 2,
        "third": 3,
        "3rd": 3,
        "fourth": 4,
        "4th": 4,
        "fifth": 5,
        "5th": 5,
        "sixth": 6,
        "6th": 6,
        "seventh": 7,
        "7th": 7,
        "eighth": 8,
        "8th": 8,
        "ninth": 9,
        "9th": 9,
        "tenth": 10,
        "10th": 10,
    }
    selector = (
        r"\b(?:the\s+)?(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th|"
        r"sixth|6th|seventh|7th|eighth|8th|ninth|9th|tenth|10th)\s+"
        r"(?:one|option|slot|time)\b"
    )
    ordinal_matches = {ordinal_values[match.group(1)] for match in re.finditer(selector, text)}
    if re.search(r"\b(?:the\s+)?last\s+(?:one|option|slot|time)\b", text):
        ordinal_matches.add(len(options))
    if len(ordinal_matches) == 1:
        return next(iter(ordinal_matches))
    if ordinal_matches:
        return None

    evidence_references: set[int] = set()
    normalized_evidence = _normalized_reply_text(evidence)
    option_variants: list[set[str]] = []
    for slot in options:
        variants: set[str] = set()
        start_at = _parse_datetime(slot.get("start_at"))
        if start_at is not None:
            try:
                local = start_at.astimezone(ZoneInfo(str(slot.get("time_zone") or "UTC")))
            except (ZoneInfoNotFoundError, ValueError):
                local = start_at.astimezone(UTC)
            variants.update(
                {
                    local.strftime("%A").casefold(),
                    f"{local.strftime('%B')} {local.day}".casefold(),
                    f"{local.strftime('%B')} {local.day}, {local.year}".casefold(),
                    local.strftime("%I:%M %p").lstrip("0").casefold(),
                    local.strftime("%I %p").lstrip("0").casefold(),
                }
            )
        option_variants.append({item for item in variants if item})

    for index, variants in enumerate(option_variants, start=1):
        for variant in variants:
            if not re.search(
                rf"(?<!\w){re.escape(variant)}(?!\w)",
                normalized_evidence,
                flags=re.IGNORECASE,
            ):
                continue
            matching = [
                option_index
                for option_index, other_variants in enumerate(option_variants, start=1)
                if variant in other_variants
            ]
            if len(matching) == 1:
                evidence_references.add(index)
    return next(iter(evidence_references)) if len(evidence_references) == 1 else None


def _classify_meeting_reply_with_ai(
    session: Session,
    workspace_id: UUID,
    body: str,
    options: list[dict[str, Any]],
) -> tuple[MeetingReplyClassification | None, str]:
    try:
        provider = resolve_workspace_reasoning_provider(session, workspace_id)
        classify = getattr(provider, "classify_meeting_reply", None)
        if not callable(classify):
            return None, "unavailable"
        clean_reply = _plain_excerpt(body, limit=2000)
        safe_options = [option for option in options if isinstance(option, dict)]
        if len(safe_options) != len(options):
            return None, "unavailable"
        payload = {
            "reply": clean_reply,
            "options": [
                {
                    "number": index,
                    "label": _format_slot(index, option),
                    "start_at": option.get("start_at"),
                    "end_at": option.get("end_at"),
                    "time_zone": option.get("time_zone"),
                }
                for index, option in enumerate(safe_options, start=1)
            ],
        }
        result = MeetingReplyClassification.model_validate(classify(payload))
    except Exception as error:
        _LOGGER.warning(
            "meeting_email.reply_interpretation_unavailable",
            extra={
                "fields": {
                    "workspace_id": str(workspace_id),
                    "provider": str(getattr(locals().get("provider"), "provider_name", "")),
                    "error_type": type(error).__name__,
                }
            },
        )
        return None, "unavailable"

    if result.intent == "confirm":
        valid = (
            result.confidence >= 0.9
            and 1 <= result.option_number <= len(options)
            and _quoted_evidence_is_present(clean_reply, result.evidence)
            and not meeting_reply_requests_other_times(clean_reply)
            and not meeting_reply_declines_offer(clean_reply)
            and not meeting_reply_has_uncertain_language(clean_reply)
        )
        if len(options) > 1:
            valid = (
                valid
                and _option_reference_from_evidence(result.evidence, options)
                == result.option_number
            )
        if not valid:
            result = MeetingReplyClassification(
                intent="uncertain",
                option_number=0,
                confidence=result.confidence,
                evidence=result.evidence,
            )
    elif result.intent == "decline" and result.confidence < 0.8:
        result = MeetingReplyClassification(
            intent="uncertain",
            option_number=0,
            confidence=result.confidence,
            evidence=result.evidence,
        )
    return result, "ai"


def create_selected_slot_offer_draft(
    session: Session,
    *,
    lead: LeadORM,
    workspace_id: UUID,
    slot: dict[str, Any],
) -> LeadFollowUpEmailORM:
    """Save one freshly selected calendar slot as a human-reviewed email offer."""
    if not lead.email or not lead.email.strip():
        raise HTTPException(
            status_code=409,
            detail="Add an email address to this lead before preparing meeting options.",
        )
    source_gmail_connection_id = str(slot.get("source_gmail_connection_id") or "")
    if not source_gmail_connection_id:
        raise HTTPException(
            status_code=409,
            detail="Reconnect the Gmail mailbox used for this meeting conversation.",
        )
    try:
        UUID(source_gmail_connection_id)
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail="Reconnect the Gmail mailbox used for this meeting conversation.",
        ) from error

    source = _find_meeting_request_for_draft(
        session,
        lead,
        workspace_id,
        gmail_connection_id=source_gmail_connection_id,
    )
    if (
        source is None
        or source.get("message_id") != str(slot.get("source_message_id") or "")
        or source.get("gmail_connection_id") != source_gmail_connection_id
        or not source.get("thread_id")
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "The conversation changed. Refresh meeting recommendations "
                "before creating a draft."
            ),
        )
    gmail_connection = _active_gmail_connection_for_source(
        session, workspace_id, source_gmail_connection_id
    )
    if gmail_connection is None:
        raise HTTPException(
            status_code=409,
            detail="Reconnect the Gmail mailbox used for this meeting conversation.",
        )
    gmail_connection_id = str(gmail_connection.id)
    sent_at = _parse_datetime(source.get("sent_at"))
    now = datetime.now(UTC)
    if sent_at is None or sent_at < now - _MAX_REQUEST_AGE or sent_at > now + timedelta(minutes=5):
        raise HTTPException(
            status_code=409,
            detail="This meeting request is no longer current. Refresh the conversation first.",
        )
    try:
        assert_outreach_allowed(session, workspace_id, lead.email)
    except OutreachBlocked as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    provider = str(slot.get("provider") or "")
    if provider not in {"google_calendar", "calendly"}:
        raise HTTPException(status_code=422, detail="Select a supported calendar provider.")
    calendar_connection_id = str(slot.get("connection_id") or "")
    if not calendar_connection_id:
        raise HTTPException(status_code=422, detail="The selected calendar is unavailable.")
    booking_link_mode = bool(slot.get("booking_link_mode"))
    booking_url = _safe_booking_url(slot.get("booking_url"))
    if booking_link_mode and not booking_url:
        raise HTTPException(
            status_code=409,
            detail="The selected calendar does not have a usable booking link.",
        )
    options = [] if booking_link_mode else [slot]
    existing = _thread_offer(
        session,
        workspace_id,
        lead.id,
        str(source["thread_id"]),
        gmail_connection_id,
    )
    if existing is not None:
        details = existing.details if isinstance(existing.details, dict) else {}
        same_source = details.get("source_message_id") == source["message_id"]
        if same_source and existing.status in {_OFFER_AWAITING_SEND, "needs_attention"}:
            try:
                draft_id = UUID(str(details.get("email_draft_id") or ""))
            except ValueError:
                draft_id = None
            draft = (
                session.scalar(
                    select(LeadFollowUpEmailORM).where(
                        LeadFollowUpEmailORM.id == draft_id,
                        LeadFollowUpEmailORM.workspace_id == workspace_id,
                        LeadFollowUpEmailORM.lead_id == lead.id,
                    )
                )
                if draft_id is not None
                else None
            )
            existing_options = details.get("options")
            if (
                booking_link_mode
                and details.get("booking_link_mode")
                and draft is not None
                and draft.status == "draft"
            ):
                if details.get("booking_link_url") == booking_url:
                    if draft.generation_method != "openai":
                        baseline_subject, baseline_body = _offer_email(
                            lead,
                            source,
                            str(details.get("calendar_provider") or ""),
                            [{"booking_url": booking_url}],
                        )
                        if draft.subject == baseline_subject and draft.body == baseline_body:
                            subject, body, rationale = _ai_booking_email(
                                session, lead, source, provider, booking_url
                            )
                            draft.subject = subject
                            draft.body = body
                            draft.rationale = rationale
                            draft.generation_method = "openai"
                            draft.updated_at = now
                            existing.details = {
                                **details,
                                "generated_subject": subject,
                                "generated_body": body,
                            }
                            session.add(existing)
                            session.add(draft)
                            session.commit()
                    return draft
                old_subject, old_body = (
                    (str(details["generated_subject"]), str(details["generated_body"]))
                    if details.get("generated_subject") and details.get("generated_body")
                    else _offer_email(
                        lead,
                        source,
                        str(details.get("calendar_provider") or ""),
                        [{"booking_url": details.get("booking_link_url")}],
                    )
                )
                if draft.subject != old_subject or draft.body != old_body:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "The existing meeting email has been edited. Open it in the "
                            "Emails tab; no draft changes were made."
                        ),
                    )
                subject, body, rationale = _ai_booking_email(
                    session, lead, source, provider, booking_url
                )
                draft.subject = subject
                draft.body = body
                draft.rationale = rationale
                draft.generation_method = "openai"
                draft.updated_at = now
                existing.status = _OFFER_AWAITING_SEND
                existing.details = {
                    **details,
                    "calendar_provider": provider,
                    "calendar_connection_id": calendar_connection_id,
                    "options": [],
                    "booking_link_mode": True,
                    "booking_link_url": booking_url,
                    "generated_subject": subject,
                    "generated_body": body,
                    "error_code": "",
                    "error_message": "",
                }
                session.add(existing)
                session.add(draft)
                _notify_meeting_draft(session, workspace_id=workspace_id, lead=lead)
                session.commit()
                return draft
            if (
                draft is not None
                and draft.status == "draft"
                and isinstance(existing_options, list)
                and len(existing_options) == 1
                and isinstance(existing_options[0], dict)
                and _slot_identity(existing_options[0]) == _slot_identity(slot)
            ):
                return draft
            old_provider = str(details.get("calendar_provider") or "")
            if draft is not None and draft.status == "draft" and isinstance(existing_options, list):
                old_subject, old_body = _offer_email(
                    lead,
                    source,
                    old_provider,
                    [option for option in existing_options if isinstance(option, dict)],
                )
                if draft.subject != old_subject or draft.body != old_body:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "The existing meeting email has been edited. Open it in the "
                            "Emails tab; no draft changes were made."
                        ),
                    )
                subject, body = _offer_email(lead, source, provider, [slot])
                draft.subject = subject
                draft.body = body
                draft.updated_at = now
                existing.status = _OFFER_AWAITING_SEND
                existing.details = {
                    **details,
                    "calendar_provider": provider,
                    "calendar_connection_id": calendar_connection_id,
                    "options": options,
                    "manual_selected_slot": True,
                    "error_code": "",
                    "error_message": "",
                }
                session.add(existing)
                session.add(draft)
                _notify_meeting_draft(session, workspace_id=workspace_id, lead=lead)
                session.commit()
                return draft
        if same_source and can_replace_unbooked_offer(existing):
            existing.status = "superseded"
            existing.details = {
                **details,
                "closed_reason": "replaced_by_manual_time_selection",
                "superseded_at": now.isoformat(),
            }
            session.add(existing)
        elif same_source and existing.status != "superseded":
            if existing.status == "booked":
                message = (
                    "A meeting is already booked for this request. A new offer requires "
                    "a new meeting request from the lead."
                )
            elif existing.status in {"sending", "booking"}:
                message = (
                    "A meeting offer or booking is still in progress. Refresh the "
                    "recommendations before trying again."
                )
            elif (
                existing.status == "needs_attention"
                and details.get("error_code") == "gmail_meeting_offer_delivery_uncertain"
            ):
                message = (
                    "Gmail delivery is uncertain for the previous offer. Check Gmail "
                    "Sent before replacing it."
                )
            else:
                message = (
                    "The previous offer may have a confirmed or uncertain booking. "
                    "Check the calendar before creating another draft."
                )
            raise HTTPException(
                status_code=409,
                detail=message,
            )
        if not same_source and existing.status not in {"superseded", "booked"}:
            existing.status = "superseded"
            existing.details = {**details, "closed_reason": "newer_meeting_request"}

    if booking_link_mode:
        subject, body, rationale = _ai_booking_email(session, lead, source, provider, booking_url)
    else:
        subject, body = _offer_email(lead, source, provider, [slot])
        rationale = "Selected meeting time prepared for human review before sending."
    offer_details: dict[str, Any] = {
        "thread_id": source["thread_id"],
        "source_message_id": source["message_id"],
        "source_gmail_connection_id": source_gmail_connection_id,
        "source_subject": source.get("subject", ""),
        "source_rfc_message_id": source.get("rfc_message_id", ""),
        "gmail_connection_id": gmail_connection_id,
        "calendar_provider": provider,
        "calendar_connection_id": calendar_connection_id,
        "options": options,
        "manual_selected_slot": not booking_link_mode,
        "booking_link_mode": booking_link_mode,
        "booking_link_url": booking_url,
        "created_at": now.isoformat(),
        "generated_subject": subject,
        "generated_body": body,
    }
    if existing is not None and existing.status == "superseded":
        details = existing.details if isinstance(existing.details, dict) else {}
        if details.get("closed_reason") == "replaced_by_manual_time_selection":
            offer_details["replaces_offer_id"] = str(existing.id)

    offer = PipelineEventORM(
        id=uuid4(),
        workspace_id=workspace_id,
        lead_id=lead.id,
        stage=_OFFER_STAGE,
        status=_OFFER_AWAITING_SEND,
        details=offer_details,
        created_at=now,
    )
    draft = LeadFollowUpEmailORM(
        id=uuid4(),
        workspace_id=workspace_id,
        lead_id=lead.id,
        provider_connection_id=gmail_connection.id,
        kind=f"meeting_options:{offer.id}",
        recipient=lead.email.strip(),
        subject=subject,
        body=body,
        rationale=rationale,
        generation_method="openai" if booking_link_mode else "meeting_request",
        generation_note=(
            "Meeting booking link" if booking_link_mode else "Manually selected calendar slot."
        ),
        status="draft",
        attempts=0,
        created_at=now,
        updated_at=now,
    )
    offer.details = {**offer.details, "email_draft_id": str(draft.id)}
    session.add(offer)
    session.add(draft)
    _notify_meeting_draft(session, workspace_id=workspace_id, lead=lead)
    session.commit()
    return draft


def create_booking_link_offer_draft(
    session: Session,
    *,
    lead: LeadORM,
    workspace_id: UUID,
    actor_id: str,
) -> LeadFollowUpEmailORM:
    """Create an in-thread booking-link draft without requiring a selected slot."""
    source = _find_meeting_request_for_draft(session, lead, workspace_id)
    if source is None or not source.get("thread_id"):
        raise HTTPException(
            status_code=409,
            detail="Refresh the lead’s Gmail conversation before creating a meeting draft.",
        )
    existing_offer = _thread_offer(
        session,
        workspace_id,
        lead.id,
        str(source["thread_id"]),
        str(source.get("gmail_connection_id") or ""),
    )
    if existing_offer is not None and existing_offer.status in {
        "sending",
        "awaiting_confirmation",
        "booking",
        "booked",
        "needs_attention",
    }:
        details = existing_offer.details if isinstance(existing_offer.details, dict) else {}
        offered_at = _parse_datetime(details.get("offered_at") or details.get("created_at"))
        request_at = _parse_datetime(source.get("sent_at"))
        if (
            details.get("source_message_id") == source.get("message_id")
            or request_at is None
            or offered_at is None
            or request_at <= offered_at
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "The appointment link for this request was already sent or resolved. "
                    "Refresh the booking status before preparing another draft."
                ),
            )
    connection = _active_connection(session, workspace_id, "calendly")
    if connection is None:
        connection = _active_connection(session, workspace_id, "google_calendar")
    if connection is None:
        raise HTTPException(
            status_code=409,
            detail="Connect Calendly or Google Calendar before creating a meeting draft.",
        )
    try:
        booking_url = meeting_booking_link(session, workspace_id, connection, actor_id)
    except ProviderOperationError as error:
        raise HTTPException(
            status_code=503 if error.retryable else 409,
            detail=str(error),
        ) from error
    return create_selected_slot_offer_draft(
        session,
        lead=lead,
        workspace_id=workspace_id,
        slot={
            "provider": connection.provider_type,
            "connection_id": str(connection.id),
            "account_name": connection.account_name,
            "event_type_uri": "",
            "event_type_name": "",
            "source_message_id": source["message_id"],
            "source_gmail_connection_id": source["gmail_connection_id"],
            "booking_url": booking_url,
            "booking_link_mode": True,
        },
    )


def _book_selected_option(
    session: Session,
    lead: LeadORM,
    workspace_id: UUID,
    actor_id: str,
    offer: PipelineEventORM,
    reply_message_id: str,
    option_number: int,
) -> str:
    details = dict(offer.details)
    options = details.get("options", [])
    if not isinstance(options, list) or not 1 <= option_number <= len(options):
        return "invalid_offer"
    selected = options[option_number - 1]
    if not isinstance(selected, dict):
        return "invalid_offer"
    details["selected_option"] = option_number
    details["reply_message_id"] = reply_message_id
    offer.status = "booking"
    offer.details = details
    session.commit()

    try:
        from lead_agent.api.routes.meetings import ConfirmMeetingRequest, confirm_meeting

        booking_request = ConfirmMeetingRequest(
            provider=str(selected["provider"]),
            connection_id=UUID(str(selected["connection_id"])),
            source_message_id=str(details["source_message_id"]),
            source_gmail_connection_id=selected.get("source_gmail_connection_id")
            or details.get("gmail_connection_id"),
            start_at=datetime.fromisoformat(str(selected["start_at"])),
            end_at=datetime.fromisoformat(str(selected["end_at"])),
            time_zone=str(selected.get("time_zone") or "UTC"),
            event_type_uri=selected.get("event_type_uri"),
            event_type_name=selected.get("event_type_name"),
        )
        workspace = WorkspaceContext(
            workspace_id=workspace_id,
            actor_id=actor_id,
            role="owner",
        )
        result = confirm_meeting(
            lead.id,
            booking_request,
            session=session,
            workspace=workspace,
        )
    except HTTPException as error:
        details["booking_error"] = str(error.detail)[:500]
        offer.status = _OFFER_PENDING if error.status_code >= 500 else "needs_attention"
        offer.details = details
        session.commit()
        _LOGGER.warning(
            "meeting_email.booking_failed",
            extra={
                "fields": {
                    "workspace_id": str(workspace_id),
                    "lead_id": str(lead.id),
                    "status_code": error.status_code,
                }
            },
        )
        return offer.status
    except Exception as error:
        details["booking_error"] = type(error).__name__
        offer.status = _OFFER_PENDING
        offer.details = details
        session.commit()
        _LOGGER.exception(
            "meeting_email.booking_retryable",
            extra={
                "fields": {
                    "workspace_id": str(workspace_id),
                    "lead_id": str(lead.id),
                }
            },
        )
        return offer.status

    details["provider_event_id"] = result.provider_event_id
    details["event_url"] = result.event_url or ""
    details.pop("booking_error", None)
    offer.status = "booked"
    offer.details = details
    session.commit()
    return "booked"


def _confirmed_booking_retry(
    details: dict[str, Any],
    messages: list[dict[str, str]],
    lead_email: str,
) -> tuple[str, int] | None:
    if details.get("lead_requested_other_times") or details.get("lead_declined_offer"):
        return None
    options = details.get("options")
    selected_option = details.get("selected_option")
    reply_message_id = str(details.get("reply_message_id") or "")
    if (
        not isinstance(options, list)
        or not isinstance(selected_option, int)
        or isinstance(selected_option, bool)
        or not 1 <= selected_option <= len(options)
        or not reply_message_id
    ):
        return None

    reply = next(
        (
            message
            for message in messages
            if str(message.get("message_id") or "") == reply_message_id
            and message.get("direction") == "inbound"
            and message.get("sender", "").strip().lower() == lead_email.strip().lower()
        ),
        None,
    )
    if reply is None:
        return None
    reply_body = str(reply.get("body") or "")
    if meeting_reply_requests_other_times(reply_body) or meeting_reply_declines_offer(reply_body):
        return None
    matched_option = match_confirmed_meeting_option(reply_body, len(options))
    if matched_option is None:
        cached = _cached_reply_interpretation(details, reply_message_id)
        if cached is not None and cached.get("intent") == "confirm":
            cached_option = cached.get("option_number")
            if isinstance(cached_option, int) and not isinstance(cached_option, bool):
                matched_option = cached_option
    if matched_option != selected_option:
        return None
    reply_time = _parse_datetime(reply.get("sent_at"))
    if reply_time is None:
        return None
    for message in messages:
        if (
            str(message.get("message_id") or "") == reply_message_id
            or message.get("direction") != "inbound"
            or message.get("sender", "").strip().lower() != lead_email.strip().lower()
        ):
            continue
        sent_at = _parse_datetime(message.get("sent_at"))
        if sent_at is not None and sent_at > reply_time:
            return None
    return reply_message_id, selected_option


def process_gmail_meeting_thread(
    session: Session,
    *,
    workspace_id: UUID,
    lead: LeadORM,
    gmail_connection: ProviderConnectionORM,
    messages: list[dict[str, str]],
    actor_id: str,
) -> str:
    """Prepare a booking link and reconcile provider bookings for a Gmail thread."""
    locked_lead_id = session.scalar(
        select(LeadORM.id)
        .where(
            LeadORM.id == lead.id,
            LeadORM.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if locked_lead_id is None:
        return "lead_missing"
    record_gmail_thread_messages(
        session,
        workspace_id=workspace_id,
        lead_id=lead.id,
        gmail_connection_id=gmail_connection.id,
        messages=messages,
    )
    if not getattr(lead, "phone", None):
        contact_phone = contact_phone_from_gmail_messages(
            messages, str(getattr(lead, "email", "") or "")
        )
        if contact_phone is not None:
            lead.phone = str(contact_phone["phone"])
            if not getattr(lead, "geography", None) and contact_phone.get("country"):
                lead.geography = str(contact_phone["country"])
            session.commit()
    if messages:
        session.flush()
    thread_ids = {
        str(message.get("thread_id") or "") for message in messages if message.get("thread_id")
    }
    if len(thread_ids) != 1:
        return "no_single_thread"
    thread_id = next(iter(thread_ids))

    offer = _thread_offer(session, workspace_id, lead.id, thread_id, str(gmail_connection.id))
    if offer is not None and offer.status == "needs_attention":
        details = offer.details if isinstance(offer.details, dict) else {}
        retry = _confirmed_booking_retry(details, messages, lead.email)
        if retry is not None:
            return _book_selected_option(
                session,
                lead,
                workspace_id,
                actor_id,
                offer,
                retry[0],
                retry[1],
            )
    if offer is not None and offer.status == "sending":
        details = offer.details if isinstance(offer.details, dict) else {}
        metadata = (
            gmail_connection.connection_metadata
            if isinstance(gmail_connection.connection_metadata, dict)
            else {}
        )
        mailbox = str(metadata.get("gmail_email") or gmail_connection.account_name).strip().lower()
        created_at = _parse_datetime(details.get("created_at"))
        sent_offer = None
        if created_at is not None:
            sent_marker = (
                str(details.get("booking_link_url") or "").lower()
                if details.get("booking_link_mode")
                else "reply with the option number"
            )
            for message in messages:
                sent_at = _parse_datetime(message.get("sent_at"))
                if (
                    message.get("direction") == "outbound"
                    and message.get("sender", "").strip().lower() == mailbox
                    and sent_at is not None
                    and sent_at > created_at
                    and sent_marker
                    and sent_marker in message.get("body", "").lower()
                ):
                    sent_offer = message
                    break
        if sent_offer is None:
            attempts = int(details.get("delivery_check_attempts") or 0) + 1
            offer.status = "needs_attention" if attempts >= 3 else "sending"
            offer.details = {
                **details,
                "delivery_check_attempts": attempts,
                **(
                    {"error_code": "gmail_meeting_offer_delivery_uncertain"}
                    if attempts >= 3
                    else {}
                ),
            }
            session.commit()
            return offer.status
        details["outbound_message_id"] = str(sent_offer.get("message_id") or "")
        details["offered_at"] = sent_offer.get("sent_at") or datetime.now(UTC).isoformat()
        offer.status = _OFFER_PENDING
        offer.details = details
        session.commit()

    reply_attention = False
    if offer is not None:
        offer_details = offer.details if isinstance(offer.details, dict) else {}
        reply_attention = offer.status == "needs_attention" and isinstance(
            offer_details.get("reply_classifications"), dict
        )
    if offer is not None and (offer.status in {_OFFER_PENDING, "booking"} or reply_attention):
        details = offer.details if isinstance(offer.details, dict) else {}
        offered_at = _parse_datetime(details.get("offered_at") or details.get("created_at"))
        source = _find_meeting_request_in_background(
            session,
            lead,
            workspace_id,
            thread_id=thread_id,
            gmail_connection_id=str(gmail_connection.id),
        )
        for message in messages:
            if (
                message.get("direction") != "inbound"
                or message.get("sender", "").strip().lower() != lead.email.strip().lower()
                or message.get("message_id")
                in {
                    str(details.get("source_message_id") or ""),
                    str(details.get("outbound_message_id") or ""),
                }
            ):
                continue
            sent_at = _parse_datetime(message.get("sent_at"))
            if offered_at is None or sent_at is None or sent_at <= offered_at:
                continue
            message_id = str(message.get("message_id") or "")
            if not message_id or _has_newer_inbound_reply(
                messages,
                current_message_id=message_id,
                lead_email=lead.email,
                current_sent_at=sent_at,
            ):
                continue
            options = details.get("options", [])
            if not isinstance(options, list) or not options:
                continue
            reply_body = str(message.get("body") or "")
            option_number = match_confirmed_meeting_option(reply_body, len(options))
            intent = "confirm" if option_number is not None else "uncertain"
            interpretation_source = "explicit"

            cached = _cached_reply_interpretation(details, message_id)
            if option_number is None and cached is not None:
                intent = str(cached["intent"])
                cached_option = cached.get("option_number")
                option_number = (
                    cached_option
                    if isinstance(cached_option, int) and not isinstance(cached_option, bool)
                    else None
                )
                interpretation_source = str(cached.get("source") or "ai")
            elif option_number is None:
                classification, interpretation_source = _classify_meeting_reply_with_ai(
                    session,
                    workspace_id,
                    reply_body,
                    [item for item in options if isinstance(item, dict)],
                )
                if classification is None:
                    intent = "uncertain"
                    option_number = None
                    confidence = 0.0
                else:
                    intent = classification.intent
                    option_number = (
                        classification.option_number if classification.intent == "confirm" else None
                    )
                    confidence = classification.confidence
                details = _store_reply_interpretation(
                    details,
                    message_id,
                    intent=intent,
                    option_number=option_number or 0,
                    confidence=confidence,
                    source=interpretation_source,
                )
                offer.details = details
                session.commit()

            if (
                intent == "confirm"
                and isinstance(option_number, int)
                and not isinstance(option_number, bool)
                and 1 <= option_number <= len(options)
                and not meeting_reply_requests_other_times(reply_body)
                and not meeting_reply_declines_offer(reply_body)
            ):
                details.pop("lead_declined_offer", None)
                details.pop("lead_requested_other_times", None)
                details.pop("error_code", None)
                details.pop("error_message", None)
                offer.details = details
                return _book_selected_option(
                    session,
                    lead,
                    workspace_id,
                    actor_id,
                    offer,
                    message_id,
                    option_number,
                )

            reply_excerpt = _plain_excerpt(reply_body, limit=2000)
            contact_zone = lead_time_zone(lead, "UTC") or "UTC"
            if (
                intent == "decline"
                and source is not None
                and requested_meeting_date(reply_excerpt, reference=sent_at, time_zone=contact_zone)
                is not None
                and requested_meeting_time(reply_excerpt) is not None
            ):
                offer.status = "superseded"
                offer.details = {
                    **details,
                    "reply_message_id": str(message.get("message_id") or ""),
                    "closed_reason": "lead_requested_new_time",
                }
                session.commit()
                updated_source = {
                    **source,
                    "excerpt": reply_excerpt[:320],
                    "sent_at": str(message.get("sent_at") or ""),
                    "rfc_message_id": str(
                        message.get("rfc_message_id") or source.get("rfc_message_id") or ""
                    ),
                }
                return _send_options(
                    session,
                    lead,
                    workspace_id,
                    gmail_connection,
                    messages,
                    updated_source,
                    actor_id,
                )

            if meeting_reply_requests_other_times(message.get("body", "")):
                offer.status = "needs_attention"
                offer.details = {
                    **details,
                    "reply_message_id": message_id,
                    "lead_requested_other_times": True,
                }
                session.commit()
                continue
            if intent == "decline":
                offer.status = "needs_attention"
                offer.details = {
                    **details,
                    "reply_message_id": message_id,
                    "lead_declined_offer": True,
                    "error_code": "lead_declined_meeting_offer",
                }
                session.commit()
                continue
            if intent == "uncertain":
                offer.status = "needs_attention"
                offer.details = {
                    **details,
                    "reply_message_id": message_id,
                    "error_code": (
                        "meeting_reply_interpretation_unavailable"
                        if interpretation_source == "unavailable"
                        else "meeting_reply_unclear"
                    ),
                    "error_message": (
                        "The reply could not be safely interpreted. Review it before booking."
                    ),
                }
                session.commit()
                continue

        if source is None or details.get("source_message_id") == source["message_id"]:
            return offer.status
        offer.status = "superseded"
        offer.details = {**details, "closed_reason": "newer_meeting_request"}
        session.commit()
        offer = None
    else:
        source = _find_meeting_request_in_background(
            session,
            lead,
            workspace_id,
            thread_id=thread_id,
            gmail_connection_id=str(gmail_connection.id),
        )

    if offer is not None and source is not None:
        details = offer.details if isinstance(offer.details, dict) else {}
        if source["message_id"] in {
            str(details.get("source_message_id") or ""),
            str(details.get("reply_message_id") or ""),
        }:
            retry_after_gmail_reconnect = (
                offer.status == "needs_attention"
                and details.get("error_code")
                in {
                    "gmail_send_scope_missing",
                    "gmail_insufficient_scope",
                    "gmail_send_forbidden",
                }
                and GMAIL_SEND_SCOPE in set(gmail_connection.scopes or [])
            )
            if not retry_after_gmail_reconnect:
                return offer.status
    if source is None:
        return "no_meeting_request"
    gmail_connection_id = source.get("gmail_connection_id")
    if gmail_connection_id and gmail_connection_id != str(gmail_connection.id):
        return "wrong_gmail_connection"
    return _send_options(
        session,
        lead,
        workspace_id,
        gmail_connection,
        messages,
        source,
        actor_id,
    )


def process_recent_gmail_imports(
    session: Session,
    *,
    connection: ProviderConnectionORM,
    actor_id: str,
    since: datetime,
) -> int:
    """Inspect recently imported Gmail threads for new meeting requests."""
    links = list(
        session.scalars(
            select(CrmProviderRecordLinkORM)
            .where(
                CrmProviderRecordLinkORM.workspace_id == connection.workspace_id,
                CrmProviderRecordLinkORM.connection_id == connection.id,
                CrmProviderRecordLinkORM.provider == "gmail",
                CrmProviderRecordLinkORM.object_type == "message",
                CrmProviderRecordLinkORM.status == "active",
                CrmProviderRecordLinkORM.last_imported_at >= since - timedelta(seconds=5),
            )
            .order_by(CrmProviderRecordLinkORM.last_imported_at.desc())
            .limit(200)
        )
    )
    if not links:
        return 0
    try:
        credentials = _authorized_credentials(
            session, connection.workspace_id, connection, actor_id
        )
    except ProviderOperationError:
        return 0
    metadata = (
        connection.connection_metadata if isinstance(connection.connection_metadata, dict) else {}
    )
    mailbox = str(metadata.get("gmail_email") or connection.account_name)
    provider = GmailInboundProvider(
        connection.id,
        credentials["access_token"],
        mailbox,
    )
    processed = 0
    seen: set[tuple[UUID, str]] = set()
    for link in links:
        thread_id = str(link.provider_metadata.get("gmail_thread_id") or "")
        identity = (link.lead_id, thread_id)
        if not thread_id or identity in seen:
            continue
        seen.add(identity)
        lead = session.scalar(
            select(LeadORM).where(
                LeadORM.id == link.lead_id,
                LeadORM.workspace_id == connection.workspace_id,
            )
        )
        if lead is None:
            continue
        try:
            messages = provider.fetch_thread(thread_id, lead_email=lead.email)
            if messages:
                result = process_gmail_meeting_thread(
                    session,
                    workspace_id=connection.workspace_id,
                    lead=lead,
                    gmail_connection=connection,
                    messages=messages,
                    actor_id=actor_id,
                )
                processed += int(result in {"options_sent", "draft_created"})
        except ProviderOperationError as error:
            _LOGGER.warning(
                "meeting_email.imported_thread_failed",
                extra={
                    "fields": {
                        "workspace_id": str(connection.workspace_id),
                        "lead_id": str(lead.id),
                        "error_code": error.code,
                    }
                },
            )
    return processed


def _booking_link_event_matches(
    event_data: dict[str, Any],
    *,
    provider: str,
    lead_email: str,
    offered_at: datetime,
    now: datetime,
) -> dict[str, Any] | None:
    if provider == "calendly":
        created_at = _parse_datetime(event_data.get("created_at"))
        start_at = _parse_datetime(event_data.get("start_time"))
        end_at = _parse_datetime(event_data.get("end_time"))
        if (
            created_at is None
            or created_at < offered_at
            or start_at is None
            or end_at is None
            or start_at <= now
        ):
            return None
        invitees = event_data.get("invitees", [])
        if isinstance(invitees, list) and invitees:
            emails = {
                str(item.get("email") or "").strip().lower()
                for item in invitees
                if isinstance(item, dict)
            }
            if lead_email.strip().lower() not in emails:
                return None
        return {
            "provider_event_id": str(event_data.get("uri") or "").rsplit("/", 1)[-1],
            "provider_event_uri": str(event_data.get("uri") or ""),
            "event_url": "",
            "title": str(event_data.get("name") or "Meeting"),
            "start_at": start_at,
            "end_at": end_at,
            "time_zone": str(event_data.get("timezone") or "UTC"),
        }

    created_at = _parse_datetime(event_data.get("created"))
    if created_at is None or created_at < offered_at:
        return None
    attendees = event_data.get("attendees", [])
    attendee_emails = (
        {
            str(item.get("email") or "").strip().lower()
            for item in attendees
            if isinstance(item, dict)
        }
        if isinstance(attendees, list)
        else set()
    )
    if lead_email.strip().lower() not in attendee_emails:
        return None
    start = event_data.get("start")
    end = event_data.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        return None
    start_at = _parse_datetime(start.get("dateTime"))
    end_at = _parse_datetime(end.get("dateTime"))
    if start_at is None or end_at is None or start_at <= now:
        return None
    organizer = event_data.get("organizer")
    creator = event_data.get("creator")
    if (
        isinstance(organizer, dict)
        and organizer.get("self") is False
        and isinstance(creator, dict)
        and creator.get("self") is False
    ):
        return None
    return {
        "provider_event_id": str(event_data.get("id") or ""),
        "provider_event_uri": str(event_data.get("htmlLink") or ""),
        "event_url": str(event_data.get("htmlLink") or ""),
        "title": str(event_data.get("summary") or "Meeting"),
        "start_at": start_at,
        "end_at": end_at,
        "time_zone": str(start.get("timeZone") or "UTC"),
    }


def reconcile_booking_link_offer(
    session: Session,
    offer: PipelineEventORM,
    lead: LeadORM,
    *,
    actor_id: str = "system:meeting-email-automation",
) -> bool:
    """Record one unambiguous provider-side booking made through an emailed link."""
    details = offer.details if isinstance(offer.details, dict) else {}
    if not details.get("booking_link_mode") or offer.status != _OFFER_PENDING or not lead.email:
        return False
    offered_at = _parse_datetime(details.get("offered_at"))
    if offered_at is None:
        return False
    try:
        connection_id = UUID(str(details.get("calendar_connection_id") or ""))
    except ValueError:
        return False
    connection = session.scalar(
        select(ProviderConnectionORM).where(
            ProviderConnectionORM.id == connection_id,
            ProviderConnectionORM.workspace_id == offer.workspace_id,
            ProviderConnectionORM.provider_type == details.get("calendar_provider"),
            ProviderConnectionORM.is_active.is_(True),
            ProviderConnectionORM.status == "verified",
        )
    )
    if connection is None:
        return False
    credentials = _authorized_credentials(session, offer.workspace_id, connection, actor_id)
    now = datetime.now(UTC)
    event_window_end = now + timedelta(days=366)
    provider = str(details.get("calendar_provider") or "")
    if provider == "calendly":
        client = CalendlyClient(credentials["access_token"])
        user_uri = str(client.current_user().get("uri") or "")
        if not user_uri:
            return False
        provider_events = client.scheduled_events(
            user_uri=user_uri,
            invitee_email=lead.email,
            start_at=offered_at,
            end_at=event_window_end,
        )
    elif provider == "google_calendar":
        client = GoogleCalendarClient(credentials["access_token"])
        provider_events = client.events_between(offered_at, event_window_end)
    else:
        return False

    matches = [
        match
        for item in provider_events
        if (
            match := _booking_link_event_matches(
                item,
                provider=provider,
                lead_email=lead.email,
                offered_at=offered_at,
                now=now,
            )
        )
        is not None
    ]
    if len(matches) != 1:
        return False
    matched = matches[0]
    event_details = {
        "provider": provider,
        "connection_id": str(connection.id),
        "provider_event_id": matched["provider_event_id"],
        "provider_event_uri": matched["provider_event_uri"],
        "event_url": matched["event_url"],
        "start_at": matched["start_at"].astimezone(UTC).isoformat(),
        "end_at": matched["end_at"].astimezone(UTC).isoformat(),
        "time_zone": matched["time_zone"],
        "title": matched["title"],
        "source_message_id": str(details.get("source_message_id") or ""),
        "source_gmail_connection_id": str(
            details.get("source_gmail_connection_id") or details.get("gmail_connection_id") or ""
        ),
        "source_subject": str(details.get("source_subject") or ""),
        "booking_method": "provider_booking_link",
    }
    existing = next(
        (
            item
            for item in session.scalars(
                select(PipelineEventORM).where(
                    PipelineEventORM.workspace_id == offer.workspace_id,
                    PipelineEventORM.lead_id == lead.id,
                    PipelineEventORM.stage == "meeting_scheduled",
                )
            )
            if isinstance(item.details, dict)
            and item.details.get("source_message_id") == event_details["source_message_id"]
            and str(item.details.get("source_gmail_connection_id") or "")
            == event_details["source_gmail_connection_id"]
        ),
        None,
    )
    if existing is None:
        session.add(
            PipelineEventORM(
                id=uuid4(),
                workspace_id=offer.workspace_id,
                lead_id=lead.id,
                stage="meeting_scheduled",
                status="scheduled",
                details=event_details,
                created_at=now,
            )
        )
        decision = session.scalar(
            select(RoutingDecisionORM)
            .where(
                RoutingDecisionORM.lead_id == lead.id,
                RoutingDecisionORM.workspace_id == offer.workspace_id,
                RoutingDecisionORM.is_current.is_(True),
            )
            .order_by(RoutingDecisionORM.created_at.desc())
        )
        if decision is not None and decision.assigned_rep_id:
            try:
                notify_lead_action(
                    session,
                    workspace_id=offer.workspace_id,
                    lead_id=lead.id,
                    recipient_user_id=UUID(decision.assigned_rep_id),
                    kind="meeting_booked",
                    message=(
                        f"{lead.full_name} booked a meeting for "
                        f"{matched['start_at'].strftime('%A, %B %d at %I:%M %p %z')}."
                    ),
                )
            except ValueError:
                pass
    offer.status = "booked"
    offer.details = {
        **details,
        "booking_method": "provider_booking_link",
        "provider_event_id": matched["provider_event_id"],
        "provider_event_uri": matched["provider_event_uri"],
        "event_url": matched["event_url"],
        "booked_start_at": event_details["start_at"],
        "booked_end_at": event_details["end_at"],
        "booked_time_zone": matched["time_zone"],
    }
    session.commit()
    return True


def process_pending_meeting_confirmations(
    session: Session,
    *,
    limit: int = _MAX_PENDING_OFFERS_PER_TICK,
) -> int:
    """Poll pending offer threads so lead replies are booked without admin action."""
    events = list(
        session.scalars(
            select(PipelineEventORM)
            .where(
                PipelineEventORM.stage == _OFFER_STAGE,
                PipelineEventORM.status.in_({_OFFER_PENDING, "booking", "sending"}),
            )
            .order_by(PipelineEventORM.created_at.asc())
            .limit(max(1, min(limit, _MAX_PENDING_OFFERS_PER_TICK)))
        )
    )
    processed = 0
    for event in events:
        details = event.details if isinstance(event.details, dict) else {}
        lead = session.scalar(
            select(LeadORM).where(
                LeadORM.id == event.lead_id,
                LeadORM.workspace_id == event.workspace_id,
            )
        )
        if lead is not None and details.get("booking_link_mode") and event.status == _OFFER_PENDING:
            try:
                processed += int(reconcile_booking_link_offer(session, event, lead))
            except ProviderOperationError as error:
                _LOGGER.warning(
                    "meeting_email.booking_link_reconciliation_failed",
                    extra={
                        "fields": {
                            "workspace_id": str(event.workspace_id),
                            "lead_id": str(event.lead_id),
                            "error_code": error.code,
                        }
                    },
                )
            continue
        try:
            gmail_connection_id = UUID(str(details["gmail_connection_id"]))
        except (KeyError, ValueError):
            event.status = "needs_attention"
            session.commit()
            continue
        connection = session.scalar(
            select(ProviderConnectionORM).where(
                ProviderConnectionORM.id == gmail_connection_id,
                ProviderConnectionORM.workspace_id == event.workspace_id,
                ProviderConnectionORM.provider_type == "gmail",
                ProviderConnectionORM.is_active.is_(True),
                ProviderConnectionORM.status == "verified",
            )
        )
        if lead is None or connection is None:
            event.status = "needs_attention"
            event.details = {**details, "booking_error": "Gmail or lead connection unavailable."}
            session.commit()
            continue
        try:
            credentials = _authorized_credentials(
                session, event.workspace_id, connection, "system:meeting-email-automation"
            )
            metadata = (
                connection.connection_metadata
                if isinstance(connection.connection_metadata, dict)
                else {}
            )
            provider = GmailInboundProvider(
                connection.id,
                credentials["access_token"],
                str(metadata.get("gmail_email") or connection.account_name),
            )
            messages = provider.fetch_thread(
                str(details.get("thread_id") or ""),
                lead_email=lead.email,
            )
            result = process_gmail_meeting_thread(
                session,
                workspace_id=event.workspace_id,
                lead=lead,
                gmail_connection=connection,
                messages=messages,
                actor_id="system:meeting-email-automation",
            )
            processed += int(result == "booked")
        except ProviderOperationError as error:
            _LOGGER.warning(
                "meeting_email.confirmation_poll_failed",
                extra={
                    "fields": {
                        "workspace_id": str(event.workspace_id),
                        "lead_id": str(event.lead_id),
                        "error_code": error.code,
                    }
                },
            )
        except Exception:
            _LOGGER.exception(
                "meeting_email.confirmation_poll_unexpected_failure",
                extra={
                    "fields": {
                        "workspace_id": str(event.workspace_id),
                        "lead_id": str(event.lead_id),
                    }
                },
            )
    return processed
