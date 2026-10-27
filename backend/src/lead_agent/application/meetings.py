from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.infrastructure.calendar_providers import CalendlyClient, GoogleCalendarClient
from lead_agent.infrastructure.communications import contact_phone_details
from lead_agent.infrastructure.db.models import LeadORM, PipelineEventORM

_MEETING_PATTERNS = (
    re.compile(
        r"\b(?:schedule|book|arrange|set up|find|pick)\b.{0,80}"
        r"\b(?:meeting|call|demo|appointment|consultation|time|slot)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:meeting|call|demo|appointment|consultation)\b.{0,80}"
        r"\b(?:available|availability|schedule|book|time|calendar|works)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:calendly\.com|calendar invite|available times|availability|"
        r"what times work|when can we meet|when are you free)\b",
        re.IGNORECASE,
    ),
)
_QUOTED_LINE = re.compile(r"^\s*(?:>|On .+wrote:|From:|Sent:)", re.IGNORECASE)
_OTHER_TIME_PATTERNS = (
    re.compile(
        r"\b(?:none of these|none of them|none of those|neither|none work|no times? work|"
        r"different times?|other times?|another time|can't make|cannot make|"
        r"not available)\b",
        re.IGNORECASE,
    ),
)
_DECLINE_REPLY_PATTERNS = (
    re.compile(
        r"^\s*(?:no|nope|nah|no thanks|no thank you|not interested|not now)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:doesn't|does not|don't|do not|can't|cannot|won't|will not|"
        r"wouldn't|would not)\s+(?:work|suit|fit|make it|do it)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:not for me|not available|not possible|doesn't suit|doesn't fit)\b",
        re.IGNORECASE,
    ),
)
_UNCERTAIN_REPLY_PATTERNS = (
    re.compile(r"\b(?:maybe|perhaps|might|possibly|not sure|unsure|i think|i guess)\b", re.I),
    re.compile(r"\b(?:but|however|unless)\b", re.I),
    re.compile(r"\boption\s*#?\s*\d+\s+(?:could|might|may)\s+work\b", re.I),
)
_SPECIFIC_TIME_PROPOSAL = re.compile(
    r"\b(?:how\s+(?:does|about)|would|does|could)\b.{0,80}"
    r"\b(?:sound|work|suit|fit)\b",
    re.IGNORECASE | re.DOTALL,
)
_MONTH_TOKEN = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?"
)
_DAY_MONTH_DATE = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+"
    rf"(?P<month>{_MONTH_TOKEN})(?:\s*,?\s*(?P<year>\d{{4}}))?\b",
    re.IGNORECASE,
)
_MONTH_DAY_DATE = re.compile(
    rf"\b(?P<month>{_MONTH_TOKEN})\s+"
    rf"(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(?P<year>\d{{4}}))?\b",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"\b(?P<year>20\d{2})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b")
_MERIDIEM_TIME = re.compile(
    r"\b(?:(?:at)\s+|@\s*)?(?P<hour>\d{1,2})(?::(?P<minute>[0-5]\d))?\s*"
    r"(?P<meridiem>a\.?m\.?|p\.?m\.?)(?=\s|[,.!?]|$)",
    re.IGNORECASE,
)
_24_HOUR_TIME = re.compile(
    r"\b(?:(?:at)\s+|@\s*)?(?P<hour>(?:[01]\d|2[0-3])):"
    r"(?P<minute>[0-5]\d)\b",
    re.IGNORECASE,
)


def find_meeting_request(
    session: Session,
    lead: LeadORM,
    workspace_id: Any,
    *,
    thread_id: str | None = None,
    gmail_connection_id: str | None = None,
) -> dict[str, str] | None:
    messages = list(
        session.scalars(
            select(PipelineEventORM)
            .where(
                PipelineEventORM.lead_id == lead.id,
                PipelineEventORM.workspace_id == workspace_id,
                PipelineEventORM.stage == "gmail_message",
            )
            .order_by(PipelineEventORM.created_at.desc())
            .limit(100)
        )
    )
    for event in messages:
        details = event.details if isinstance(event.details, dict) else {}
        direction = str(details.get("direction") or getattr(event, "status", "")).lower()
        sender = str(details.get("sender") or "").strip().lower()
        lead_email = str(getattr(lead, "email", "") or "").strip().lower()
        if direction == "outbound" or (
            sender and (not lead_email or sender != lead_email)
        ):
            continue
        if thread_id is not None and str(details.get("thread_id") or "") != thread_id:
            continue
        if (
            gmail_connection_id is not None
            and str(details.get("gmail_connection_id") or "") != gmail_connection_id
        ):
            continue
        subject = str(details.get("subject") or "").strip()
        body = str(details.get("body") or details.get("snippet") or "").strip()
        excerpt = _plain_excerpt(body)
        if (
            match_confirmed_meeting_option(excerpt, option_count=5) is not None
            and not meeting_reply_requests_other_times(excerpt)
        ):
            continue
        text = f"{subject}\n{excerpt}"
        reference = _parse_provider_time(details.get("sent_at"))
        contact_zone = lead_time_zone(lead, "UTC") or "UTC"
        specific_time_proposal = (
            _SPECIFIC_TIME_PROPOSAL.search(excerpt) is not None
            and requested_meeting_date(
                excerpt, reference=reference, time_zone=contact_zone
            ) is not None
            and requested_meeting_time(excerpt) is not None
        )
        if (
            not any(pattern.search(text) for pattern in _MEETING_PATTERNS)
            and not specific_time_proposal
        ):
            continue
        message_id = str(details.get("message_id") or "")
        if not message_id:
            continue
        return {
            "message_id": message_id,
            "subject": subject[:500],
            "excerpt": excerpt,
            "thread_id": str(details.get("thread_id") or ""),
            "sent_at": str(details.get("sent_at") or ""),
            "rfc_message_id": str(details.get("rfc_message_id") or ""),
            "gmail_connection_id": str(details.get("gmail_connection_id") or ""),
        }
    return None


def _plain_excerpt(body: str, limit: int = 320) -> str:
    clean_lines: list[str] = []
    for line in body.splitlines():
        if _QUOTED_LINE.match(line):
            break
        if line.strip():
            clean_lines.append(line.strip())
    excerpt = " ".join(clean_lines).strip()
    return excerpt[:limit] + ("…" if len(excerpt) > limit else "")


def match_confirmed_meeting_option(body: str, option_count: int) -> int | None:
    """Accept a clear numbered choice, but reject mixed or uncertain replies."""
    if option_count < 1:
        return None
    clean = _plain_excerpt(body, limit=2000)
    if (
        "?" in clean
        or meeting_reply_requests_other_times(clean)
        or meeting_reply_declines_offer(clean)
        or any(pattern.search(clean) for pattern in _UNCERTAIN_REPLY_PATTERNS)
    ):
        return None
    first_line = next((line.strip() for line in clean.splitlines() if line.strip()), "")
    leading_pattern = (
        r"^(?:hi\b[^,\n]*,\s*)?(?:(?:i\s+(?:choose|pick|select|take|confirm)\s+)?)"
        r"(?:(?:option|slot|time|choice|number)\s*)?#?\s*(\d{1,2})(?=\b|$)"
    )
    match = re.search(leading_pattern, first_line, flags=re.IGNORECASE)
    if match is not None:
        if re.search(r"\d", first_line[match.end() :]):
            return None
        selected = int(match.group(1))
        return selected if 1 <= selected <= option_count else None
    mentions = list(
        re.finditer(
            r"\b(?:option|slot|time|choice|number)\s*#?\s*(\d{1,2})\b",
            clean,
            flags=re.IGNORECASE,
        )
    )
    if len(mentions) != 1:
        return None
    mention = mentions[0]
    context = clean[max(0, mention.start() - 30) : mention.end() + 40]
    if re.search(
        r"\b(?:not|no|don't|doesn't|cannot|can't|won't|unavailable)\b",
        context,
        flags=re.IGNORECASE,
    ):
        return None
    selected = int(mention.group(1))
    return selected if 1 <= selected <= option_count else None


def meeting_reply_requests_other_times(body: str) -> bool:
    clean = _plain_excerpt(body, limit=2000).strip()
    if clean.lower().rstrip(".!?") in {"none", "neither", "no"}:
        return True
    return any(pattern.search(clean) for pattern in _OTHER_TIME_PATTERNS)


def meeting_reply_declines_offer(body: str) -> bool:
    clean = _plain_excerpt(body, limit=2000).strip()
    clean = re.sub(r"\bno\s+(?:problem|worries)\b", "", clean, flags=re.IGNORECASE)
    return any(pattern.search(clean) for pattern in _DECLINE_REPLY_PATTERNS)


def _parse_provider_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def lead_time_zone(lead: Any, fallback: str | None = None) -> str | None:
    """Prefer a contact's explicit or unambiguous phone-derived timezone."""
    explicit = str(
        getattr(lead, "time_zone", None) or getattr(lead, "timezone", None) or ""
    ).strip()
    if explicit:
        try:
            return ZoneInfo(explicit).key
        except (ZoneInfoNotFoundError, ValueError):
            pass
    details = contact_phone_details(
        getattr(lead, "phone", None),
        getattr(lead, "geography", None),
    )
    if details and details.get("time_zone"):
        return str(details["time_zone"])
    return fallback


def requested_meeting_date(
    text: str, *, reference: datetime | None = None, time_zone: str = "UTC"
) -> date | None:
    """Parse explicit dates and today/tomorrow without guessing a weekday."""
    month_numbers = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    current = reference or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    local_current = current.astimezone(_timezone(time_zone))
    if re.search(r"\btoday\b", text, re.IGNORECASE):
        return local_current.date()
    if re.search(r"\btomorrow\b", text, re.IGNORECASE):
        return local_current.date() + timedelta(days=1)

    match = _ISO_DATE.search(text)
    month_is_numeric = match is not None
    if match is None:
        match = _DAY_MONTH_DATE.search(text) or _MONTH_DAY_DATE.search(text)
    if match is None:
        return None

    try:
        day = int(match.group("day"))
        month_text = match.group("month")
        month = (
            int(month_text)
            if month_is_numeric
            else month_numbers[month_text.lower()]
        )
        supplied_year = match.group("year")
        year = int(supplied_year) if supplied_year else local_current.year
        requested = date(year, month, day)
        if supplied_year is None and requested < local_current.date():
            requested = date(year + 1, month, day)
        return requested
    except (ValueError, KeyError):
        return None


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def requested_meeting_time(text: str) -> time | None:
    """Parse a stated clock time only when its meaning is unambiguous."""
    clean = text.strip()
    if re.search(r"\bnoon\b", clean, re.IGNORECASE):
        return time(12, 0)
    if re.search(r"\bmidnight\b", clean, re.IGNORECASE):
        return time(0, 0)
    match = _MERIDIEM_TIME.search(clean)
    if match is not None:
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or "0")
        meridiem = match.group("meridiem").lower().replace(".", "")
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
        return time(hour, minute)
    match = _24_HOUR_TIME.search(clean)
    if match is None:
        return None
    return time(int(match.group("hour")), int(match.group("minute")))


def requested_meeting_datetime(
    text: str,
    *,
    time_zone: str,
    reference: datetime | None = None,
) -> datetime | None:
    day = requested_meeting_date(text, reference=reference, time_zone=time_zone)
    clock = requested_meeting_time(text)
    if day is None or clock is None:
        return None
    return datetime.combine(day, clock, tzinfo=_timezone(time_zone))


def recommend_google_calendar_slots(
    client: GoogleCalendarClient,
    *,
    connection_id: str,
    account_name: str,
    source_message_id: str,
    source_gmail_connection_id: str | None = None,
    now: datetime | None = None,
    requested_date: date | None = None,
    requested_text: str | None = None,
    reference: datetime | None = None,
    time_zone_override: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    current = now or datetime.now(UTC)
    calendar = client.primary_calendar()
    zone = _timezone(time_zone_override or str(calendar.get("timeZone") or "UTC"))
    time_zone = zone.key
    if requested_text is not None:
        requested_date = requested_meeting_date(
            requested_text, reference=reference, time_zone=time_zone
        )
    local_today = current.astimezone(zone).date()
    if requested_date is not None:
        day_start = datetime.combine(requested_date, time.min, tzinfo=zone)
        window_end = day_start + timedelta(days=1)
        if window_end <= current:
            return []
        window_start = max(current, day_start)
        days = [requested_date]
    else:
        window_start = current
        window_end = current + timedelta(days=14)
        days = [local_today + timedelta(days=day_offset) for day_offset in range(15)]
    busy = [
        (start, end)
        for item in client.busy_intervals(window_start, window_end)
        if (start := _parse_provider_time(item.get("start"))) is not None
        and (end := _parse_provider_time(item.get("end"))) is not None
    ]
    available: list[dict[str, Any]] = []
    for day in days:
        if day.weekday() >= 5:
            continue
        for minute_of_day in range(9 * 60, 17 * 60, 30):
            local_start = datetime.combine(
                day,
                time(hour=minute_of_day // 60, minute=minute_of_day % 60),
                tzinfo=zone,
            )
            local_end = local_start + timedelta(minutes=30)
            if local_start <= current + timedelta(minutes=5) or local_end > local_start.replace(
                hour=17, minute=0
            ):
                continue
            if any(
                local_start < busy_end and local_end > busy_start
                for busy_start, busy_end in busy
            ):
                continue
            available.append(
                {
                    "provider": "google_calendar",
                    "connection_id": connection_id,
                    "account_name": account_name,
                    "event_type_uri": "",
                    "event_type_name": "",
                    "start_at": local_start.isoformat(),
                    "end_at": local_end.isoformat(),
                    "time_zone": time_zone,
                    "source_message_id": source_message_id,
                    "source_gmail_connection_id": source_gmail_connection_id,
                }
            )
            if len(available) >= limit:
                return available
    return available


def recommend_calendly_slots(
    client: CalendlyClient,
    *,
    connection_id: str,
    source_message_id: str,
    source_gmail_connection_id: str | None = None,
    now: datetime | None = None,
    requested_date: date | None = None,
    requested_text: str | None = None,
    reference: datetime | None = None,
    time_zone_override: str | None = None,
    limit_per_event_type: int = 5,
    event_type_statuses: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    current = now or datetime.now(UTC)
    user = client.current_user()
    user_uri = str(user.get("uri") or "")
    time_zone = _timezone(
        time_zone_override or str(user.get("timezone") or "UTC")
    ).key
    if requested_text is not None:
        requested_date = requested_meeting_date(
            requested_text, reference=reference, time_zone=time_zone
        )
    if not user_uri:
        return []
    window_start = current + timedelta(minutes=5)
    if requested_date is not None:
        day_start = datetime.combine(requested_date, time.min, tzinfo=_timezone(time_zone))
        window_end = day_start + timedelta(days=1)
        window_start = max(window_start, day_start)
        if window_start >= window_end:
            return []
    else:
        window_end = current + timedelta(days=14)
    suggestions: list[dict[str, Any]] = []
    unavailable_event_type_errors: list[ProviderOperationError] = []
    for event_type in client.event_types(user_uri)[:5]:
        uri = str(event_type.get("uri") or "")
        name = str(event_type.get("name") or "Calendly meeting")
        if not uri or event_type.get("active") is False:
            continue
        try:
            duration = int(event_type.get("duration") or 0)
        except (TypeError, ValueError):
            continue
        if duration < 5 or duration > 180:
            continue
        event_type_status = {
            "event_type_uri": uri,
            "event_type_name": name,
            "status": "no_times",
        }
        if event_type_statuses is not None:
            event_type_statuses.append(event_type_status)
        try:
            event_slots = client.available_times(uri, window_start, window_end)
        except ProviderOperationError as error:
            if error.code != "calendly_external_calendar_unavailable":
                raise
            event_type_status["status"] = "calendar_unavailable"
            unavailable_event_type_errors.append(error)
            continue
        event_suggestions = 0
        for slot in event_slots:
            start_at = _parse_provider_time(slot.get("start_time"))
            if start_at is None or start_at <= current + timedelta(minutes=5):
                continue
            end_at = start_at + timedelta(minutes=duration)
            suggestions.append(
                {
                    "provider": "calendly",
                    "connection_id": connection_id,
                    "account_name": str(user.get("email") or "Calendly"),
                    "event_type_uri": uri,
                    "event_type_name": name,
                    "start_at": start_at.isoformat(),
                    "end_at": end_at.isoformat(),
                    "time_zone": time_zone,
                    "source_message_id": source_message_id,
                    "source_gmail_connection_id": source_gmail_connection_id,
                }
            )
            event_suggestions += 1
            if event_suggestions >= limit_per_event_type:
                break
        if event_suggestions:
            event_type_status["status"] = "available"
    if not suggestions and unavailable_event_type_errors:
        raise unavailable_event_type_errors[0]
    return suggestions


def meeting_title(lead: LeadORM) -> str:
    return f"Meeting with {lead.full_name}"


def meeting_description(
    lead: LeadORM, source_message: dict[str, str], *, application_name: str = "Corral"
) -> str:
    lines = [
        f"Contact: {lead.full_name}",
        f"Email: {lead.email}",
        f"Company: {lead.company_name}",
    ]
    if source_message.get("subject"):
        lines.append(f"Gmail subject: {source_message['subject']}")
    if source_message.get("excerpt"):
        lines.extend(["", "Conversation context:", source_message["excerpt"]])
    return "\n".join(lines) + f"\n\nCreated from a confirmed {application_name} suggestion."


def appointment_event_exists(
    session: Session,
    *,
    workspace_id: Any,
    lead_id: Any,
    provider: str,
    connection_id: str,
    source_message_id: str,
    source_gmail_connection_id: str | None = None,
    start_at: datetime,
) -> PipelineEventORM | None:
    events = session.scalars(
        select(PipelineEventORM).where(
            PipelineEventORM.workspace_id == workspace_id,
            PipelineEventORM.lead_id == lead_id,
            PipelineEventORM.stage == "meeting_scheduled",
        )
    )
    target_start = start_at.astimezone(UTC).isoformat()
    for event in events:
        details = event.details if isinstance(event.details, dict) else {}
        if (
            details.get("provider") == provider
            and details.get("connection_id") == connection_id
            and details.get("source_message_id") == source_message_id
            and (
                source_gmail_connection_id is None
                or str(details.get("source_gmail_connection_id") or "")
                == source_gmail_connection_id
            )
            and details.get("start_at") == target_start
        ):
            return event
    return None
