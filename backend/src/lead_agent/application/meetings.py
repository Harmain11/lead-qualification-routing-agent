from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.infrastructure.calendar_providers import CalendlyClient, GoogleCalendarClient
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


def find_meeting_request(
    session: Session, lead: LeadORM, workspace_id: Any
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
        subject = str(details.get("subject") or "").strip()
        body = str(details.get("body") or details.get("snippet") or "").strip()
        text = f"{subject}\n{body}"
        if not any(pattern.search(text) for pattern in _MEETING_PATTERNS):
            continue
        message_id = str(details.get("message_id") or "")
        if not message_id:
            continue
        excerpt = _plain_excerpt(body)
        return {
            "message_id": message_id,
            "subject": subject[:500],
            "excerpt": excerpt,
            "thread_id": str(details.get("thread_id") or ""),
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


def _parse_provider_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def recommend_google_calendar_slots(
    client: GoogleCalendarClient,
    *,
    connection_id: str,
    account_name: str,
    source_message_id: str,
    now: datetime | None = None,
    limit: int = 5,
) -> list[dict[str, str]]:
    current = now or datetime.now(UTC)
    calendar = client.primary_calendar()
    zone = _timezone(str(calendar.get("timeZone") or "UTC"))
    time_zone = zone.key
    window_end = current + timedelta(days=14)
    busy = [
        (start, end)
        for item in client.busy_intervals(current, window_end)
        if (start := _parse_provider_time(item.get("start"))) is not None
        and (end := _parse_provider_time(item.get("end"))) is not None
    ]
    available: list[dict[str, str]] = []
    local_today = current.astimezone(zone).date()
    for day_offset in range(15):
        day = local_today + timedelta(days=day_offset)
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
    now: datetime | None = None,
    limit_per_event_type: int = 5,
) -> list[dict[str, str]]:
    current = now or datetime.now(UTC)
    user = client.current_user()
    user_uri = str(user.get("uri") or "")
    time_zone = _timezone(str(user.get("timezone") or "UTC")).key
    if not user_uri:
        return []
    window_end = current + timedelta(days=14)
    suggestions: list[dict[str, str]] = []
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
        event_slots = client.available_times(uri, current, window_end)
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
                }
            )
            event_suggestions += 1
            if event_suggestions >= limit_per_event_type:
                break
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
            and details.get("start_at") == target_start
        ):
            return event
    return None
