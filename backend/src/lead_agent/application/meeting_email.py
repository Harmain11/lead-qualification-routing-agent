from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.application.assignment_notifications import notify_lead_action
from lead_agent.application.governance import OutreachBlocked, assert_outreach_allowed
from lead_agent.application.meetings import (
    _plain_excerpt,
    find_meeting_request,
    lead_time_zone,
    match_confirmed_meeting_option,
    meeting_reply_requests_other_times,
    recommend_calendly_slots,
    recommend_google_calendar_slots,
    requested_meeting_date,
    requested_meeting_datetime,
    requested_meeting_time,
)
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
)
from lead_agent.infrastructure.gmail import (
    GMAIL_SEND_SCOPE,
    GmailInboundProvider,
    contact_phone_from_gmail_messages,
)
from lead_agent.infrastructure.oauth import get_oauth_broker
from lead_agent.infrastructure.provider_lifecycle import WorkspaceAuthorizedConnectionResolver
from lead_agent.infrastructure.secrets import get_secret_store

_LOGGER = logging.getLogger("lead_agent.meeting_email")
_OFFER_STAGE = "meeting_email_offer"
_OFFER_PENDING = "awaiting_confirmation"
_OFFER_AWAITING_SEND = "awaiting_send"
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
        if details.get("thread_id") == thread_id and (
            gmail_connection_id is None
            or str(details.get("gmail_connection_id") or "") == gmail_connection_id
        ):
            return event
    return None


def record_gmail_thread_messages(
    session: Session,
    *,
    workspace_id: UUID,
    lead_id: UUID,
    gmail_connection_id: UUID,
    messages: list[dict[str, str]],
) -> int:
    existing = list(
        session.scalars(
            select(PipelineEventORM).where(
                PipelineEventORM.workspace_id == workspace_id,
                PipelineEventORM.lead_id == lead_id,
                PipelineEventORM.stage == "gmail_message",
            )
        )
    )
    known_ids_by_connection: set[tuple[str, str]] = set()
    legacy_known_ids: set[str] = set()
    for event in existing:
        if not isinstance(event.details, dict) or not event.details.get("message_id"):
            continue
        message_id = str(event.details["message_id"])
        connection_id = str(event.details.get("gmail_connection_id") or "")
        if connection_id:
            known_ids_by_connection.add((connection_id, message_id))
        else:
            legacy_known_ids.add(message_id)
    added = 0
    for message in messages:
        message_id = str(message.get("message_id") or "")
        scoped_id = (str(gmail_connection_id), message_id)
        if not message_id or scoped_id in known_ids_by_connection or message_id in legacy_known_ids:
            continue
        details = dict(message)
        details["gmail_connection_id"] = str(gmail_connection_id)
        sent_at = _parse_datetime(details.get("sent_at"))
        session.add(
            PipelineEventORM(
                id=uuid4(),
                workspace_id=workspace_id,
                lead_id=lead_id,
                stage="gmail_message",
                status=str(details.get("direction") or "inbound"),
                details=details,
                created_at=sent_at or datetime.now(UTC),
            )
        )
        known_ids_by_connection.add(scoped_id)
        added += 1
    if added:
        session.flush()
    return added


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
    body = (
        f"Hi {first_name},\n\n"
        f"{introduction}{timezone_note}\n\n"
        f"{options}\n\n"
        "Reply with the option number (for example, 2) to confirm. "
        f"We’ll add the meeting to {provider_name} and send you a calendar invitation. "
        "If none of these times work, reply “none” and Corral will flag it for your team.\n\n"
        "Best,\nThe team"
    )
    return subject, body


def _calendar_options(
    session: Session,
    workspace_id: UUID,
    source: dict[str, str],
    actor_id: str,
    *,
    lead: LeadORM | None = None,
) -> tuple[ProviderConnectionORM, list[dict[str, Any]]] | None:
    requested_text = source.get("excerpt", "")
    reference = _parse_datetime(source.get("sent_at"))
    contact_zone = lead_time_zone(lead) if lead is not None else None
    requested_date = requested_meeting_date(
        requested_text,
        reference=reference,
        time_zone=contact_zone or "UTC",
    )
    # Use one calendar for each offer to avoid presenting duplicate or competing
    # slots when both providers are connected. Prefer the direct Google Calendar.
    for provider_name in ("google_calendar", "calendly"):
        connection = _active_connection(session, workspace_id, provider_name)
        if connection is None:
            continue
        try:
            credentials = _authorized_credentials(session, workspace_id, connection, actor_id)
            if provider_name == "google_calendar":
                client = GoogleCalendarClient(credentials["access_token"])
                slots = recommend_google_calendar_slots(
                    client,
                    connection_id=str(connection.id),
                    account_name=connection.account_name,
                    source_message_id=source["message_id"],
                    source_gmail_connection_id=source.get("gmail_connection_id"),
                    requested_date=requested_date,
                    requested_text=requested_text,
                    reference=reference,
                    time_zone_override=contact_zone,
                    limit=5,
                )
                if not slots and requested_date is not None:
                    slots = recommend_google_calendar_slots(
                        client,
                        connection_id=str(connection.id),
                        account_name=connection.account_name,
                        source_message_id=source["message_id"],
                        source_gmail_connection_id=source.get("gmail_connection_id"),
                        time_zone_override=contact_zone,
                        limit=5,
                    )
            else:
                client = CalendlyClient(credentials["access_token"])
                slots = recommend_calendly_slots(
                    client,
                    connection_id=str(connection.id),
                    source_message_id=source["message_id"],
                    source_gmail_connection_id=source.get("gmail_connection_id"),
                    requested_date=requested_date,
                    requested_text=requested_text,
                    reference=reference,
                    time_zone_override=contact_zone,
                    limit_per_event_type=5,
                )
                if not slots and requested_date is not None:
                    slots = recommend_calendly_slots(
                        client,
                        connection_id=str(connection.id),
                        source_message_id=source["message_id"],
                        source_gmail_connection_id=source.get("gmail_connection_id"),
                        time_zone_override=contact_zone,
                        limit_per_event_type=5,
                    )
                for slot in slots:
                    slot["account_name"] = connection.account_name
                slots.sort(key=lambda slot: str(slot.get("start_at") or ""))
                slots = slots[:5]
            if slots:
                return connection, slots
        except ProviderOperationError as error:
            _LOGGER.warning(
                "meeting_email.calendar_unavailable",
                extra={
                    "fields": {
                        "workspace_id": str(workspace_id),
                        "provider": provider_name,
                        "error_code": error.code,
                    }
                },
            )
    return None


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
                if requested_meeting_date(
                    request_text, reference=reference, time_zone=time_zone
                ) is None:
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
                if requested_meeting_date(
                    request_text, reference=reference, time_zone=time_zone
                ) is None:
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

    selected_calendar = _calendar_options(
        session, workspace_id, source, actor_id, lead=lead
    )
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
            "options": slots,
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
        rationale="Meeting options prepared for human review before sending.",
        generation_method="meeting_request",
        generation_note=None,
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

    decision = session.scalar(
        select(RoutingDecisionORM)
        .where(
            RoutingDecisionORM.lead_id == lead.id,
            RoutingDecisionORM.workspace_id == workspace_id,
            RoutingDecisionORM.is_current.is_(True),
        )
        .order_by(RoutingDecisionORM.created_at.desc())
    )
    if decision is not None and decision.assigned_rep_id:
        try:
            recipient_user_id = UUID(decision.assigned_rep_id)
        except ValueError:
            recipient_user_id = None
        if recipient_user_id is not None:
            notify_lead_action(
                session,
                workspace_id=workspace_id,
                lead_id=lead.id,
                recipient_user_id=recipient_user_id,
                kind="meeting_reply_draft",
                message=(
                    f"{lead.full_name} asked to meet. Review the prepared email "
                    "in this lead’s Follow-up tab and send it when ready."
                ),
            )
    session.commit()
    return "draft_created"


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
    if details.get("lead_requested_other_times"):
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
            and match_confirmed_meeting_option(message.get("body", ""), len(options))
            == selected_option
            and not meeting_reply_requests_other_times(message.get("body", ""))
        ),
        None,
    )
    if reply is None:
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
    """Offer available times and book only after the lead selects a numbered option."""
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
            for message in messages:
                sent_at = _parse_datetime(message.get("sent_at"))
                if (
                    message.get("direction") == "outbound"
                    and message.get("sender", "").strip().lower() == mailbox
                    and sent_at is not None
                    and sent_at > created_at
                    and "reply with the option number" in message.get("body", "").lower()
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

    if offer is not None and offer.status in {_OFFER_PENDING, "booking"}:
        details = offer.details if isinstance(offer.details, dict) else {}
        offered_at = _parse_datetime(details.get("offered_at") or details.get("created_at"))
        source = find_meeting_request(
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
            options = details.get("options", [])
            if not isinstance(options, list):
                continue
            option_number = match_confirmed_meeting_option(message.get("body", ""), len(options))
            if option_number is not None:
                return _book_selected_option(
                    session,
                    lead,
                    workspace_id,
                    actor_id,
                    offer,
                    str(message.get("message_id") or ""),
                    option_number,
                )

            reply_excerpt = _plain_excerpt(str(message.get("body") or ""), limit=2000)
            request_text = "\n".join(
                part
                for part in (
                    reply_excerpt,
                    source.get("excerpt", "") if source is not None else "",
                )
                if part
            )
            contact_zone = lead_time_zone(lead, "UTC") or "UTC"
            if (
                source is not None
                and requested_meeting_date(
                    request_text, reference=sent_at, time_zone=contact_zone
                ) is not None
                and requested_meeting_time(request_text) is not None
            ):
                if _attempt_requested_time_booking(
                    session,
                    lead,
                    workspace_id,
                    actor_id,
                    source["message_id"],
                    source.get("gmail_connection_id"),
                    request_text,
                    sent_at,
                ):
                    offer.status = "booked"
                    offer.details = {
                        **(offer.details if isinstance(offer.details, dict) else details),
                        "reply_message_id": str(message.get("message_id") or ""),
                        "booking_source": "lead_requested_exact_time",
                    }
                    session.commit()
                    return "booked"

                offer.status = "superseded"
                offer.details = {
                    **details,
                    "reply_message_id": str(message.get("message_id") or ""),
                    "closed_reason": "requested_time_unavailable",
                }
                session.commit()
                updated_source = {
                    **source,
                    "excerpt": request_text[:320],
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
                    "reply_message_id": str(message.get("message_id") or ""),
                    "lead_requested_other_times": True,
                }
                session.commit()
                return "needs_attention"

        if source is None or details.get("source_message_id") == source["message_id"]:
            return offer.status
        offer.status = "superseded"
        offer.details = {**details, "closed_reason": "newer_meeting_request"}
        session.commit()
        offer = None
    else:
        source = find_meeting_request(
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
    source_sent_at = _parse_datetime(source.get("sent_at"))
    request_is_fresh = source_sent_at is not None and datetime.now(
        UTC
    ) - _MAX_REQUEST_AGE <= source_sent_at <= datetime.now(UTC) + timedelta(minutes=5)
    contact_zone = lead_time_zone(lead, "UTC") or "UTC"
    if (
        request_is_fresh
        and requested_meeting_date(
            source.get("excerpt", ""),
            reference=source_sent_at,
            time_zone=contact_zone,
        )
        is not None
        and requested_meeting_time(source.get("excerpt", "")) is not None
        and _attempt_requested_time_booking(
            session,
            lead,
            workspace_id,
            actor_id,
            source["message_id"],
            source.get("gmail_connection_id"),
            source.get("excerpt", ""),
            source_sent_at,
        )
    ):
        return "booked"
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
