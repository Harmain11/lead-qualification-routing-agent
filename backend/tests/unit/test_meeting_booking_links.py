from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from lead_agent.api.routes.integrations import GoogleAppointmentScheduleRequest
from lead_agent.application.meeting_email import (
    _booking_link_email,
    _record_external_booking,
)


class _Session:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def scalars(self, _statement):
        return iter([])

    def add(self, row: object) -> None:
        self.added.append(row)

    def commit(self) -> None:
        self.commits += 1


def test_google_schedule_configuration_requires_a_google_link_and_event_title() -> None:
    configured = GoogleAppointmentScheduleRequest(
        url="https://calendar.app.google/example",
        event_title="Introductory call",
    )
    assert configured.url.endswith("/example")

    with pytest.raises(ValidationError):
        GoogleAppointmentScheduleRequest(url="https://example.com/book", event_title="Call")
    with pytest.raises(ValidationError):
        GoogleAppointmentScheduleRequest(url="https://calendar.app.google/example")
    with pytest.raises(ValidationError):
        GoogleAppointmentScheduleRequest(url="", event_title="Call")


def test_booking_link_email_contains_one_provider_link_and_no_numbered_options() -> None:
    subject, body = _booking_link_email(
        SimpleNamespace(full_name="Asha Khan"),
        {"subject": "Product meeting"},
        {
            "provider": "calendly",
            "booking_url": "https://calendly.com/example/intro",
            "event_type_name": "Intro",
        },
    )

    assert subject == "Re: Product meeting"
    assert "Hi Asha," in body
    assert "https://calendly.com/example/intro" in body
    assert "Reply with the option number" not in body


def test_external_booking_is_recorded_on_the_lead_and_offer() -> None:
    session = _Session()
    lead_id = uuid4()
    workspace_id = uuid4()
    connection_id = uuid4()
    start_at = datetime.now(UTC) + timedelta(days=2)
    offer = SimpleNamespace(
        id=uuid4(),
        status="awaiting_booking",
        details={
            "calendar_provider": "google_calendar",
            "source_message_id": "source-1",
            "source_gmail_connection_id": "gmail-1",
            "calendar_connection_id": str(connection_id),
        },
    )
    lead = SimpleNamespace(id=lead_id, workspace_id=workspace_id, full_name="Asha Khan")
    connection = SimpleNamespace(id=connection_id)

    recorded = _record_external_booking(
        session,
        offer,
        lead,
        connection,
        {"id": "event-1", "summary": "Introductory call", "htmlLink": "https://calendar.google.com/event"},
        start_at=start_at,
        end_at=start_at + timedelta(minutes=30),
        time_zone="Asia/Karachi",
    )

    assert recorded is True
    assert offer.status == "booked"
    assert len(session.added) == 1
    scheduled_event = session.added[0]
    assert scheduled_event.stage == "meeting_scheduled"
    assert scheduled_event.details["source_message_id"] == "source-1"
    assert scheduled_event.details["title"] == "Introductory call"
    assert scheduled_event.details["booking_method"] == "provider_booking_link"
    assert session.commits == 1
