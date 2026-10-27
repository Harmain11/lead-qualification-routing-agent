from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from lead_agent.api.routes.meetings import ConfirmMeetingRequest
from lead_agent.application import meeting_email
from lead_agent.application.meetings import (
    lead_time_zone,
    recommend_calendly_slots,
    recommend_google_calendar_slots,
)
from lead_agent.infrastructure.communications import (
    contact_phone_details,
    extract_international_phone_numbers,
)
from lead_agent.infrastructure.gmail import contact_phone_from_gmail_messages


def test_international_phones_identify_country_and_single_timezone() -> None:
    pakistan = contact_phone_details("+92 301 605 7779")
    uk = contact_phone_details("Call me at +44 (20) 7031 3000")

    assert pakistan is not None
    assert pakistan["phone"] == "+923016057779"
    assert pakistan["country"] == "Pakistan"
    assert pakistan["time_zone"] == "Asia/Karachi"
    assert uk is not None
    assert uk["phone"] == "+442070313000"
    assert uk["country"] == "United Kingdom"
    assert uk["time_zone"] == "Europe/London"


def test_country_and_timezone_can_be_derived_from_local_number_and_geography() -> None:
    details = contact_phone_details("0301 6057779", "Pakistan")

    assert details is not None
    assert details["phone"] == "+923016057779"
    assert details["country"] == "Pakistan"
    assert details["time_zone"] == "Asia/Karachi"


def test_gmail_phone_extraction_uses_contact_authored_messages_only() -> None:
    messages = [
        {
            "sender": "sales@workspace.example",
            "direction": "outbound",
            "body": "Best,\nHarmain\n+92 301 6057779",
        },
        {
            "sender": "junaid@example.test",
            "direction": "inbound",
            "body": "Best,\nJunaid\nMobile: +44 20 7031 3000",
        },
    ]

    details = contact_phone_from_gmail_messages(messages, "junaid@example.test")

    assert details is not None
    assert details["phone"] == "+442070313000"
    assert details["country"] == "United Kingdom"
    assert details["raw"] == "+44 20 7031 3000"


def test_phone_country_timezone_is_not_guessed_for_ambiguous_numbers() -> None:
    numbers = extract_international_phone_numbers(
        "Call +1 800 555 0199 or +92 301 6057779."
    )
    contact = SimpleNamespace(phone="+1 800 555 0199", geography=None)

    assert len(numbers) == 2
    assert lead_time_zone(contact, "UTC") == "UTC"


def test_email_domain_does_not_infer_a_contact_timezone() -> None:
    contact = SimpleNamespace(
        phone=None,
        geography=None,
        email="buyer@example.pk",
    )

    assert lead_time_zone(contact, "UTC") == "UTC"


def test_exact_time_request_is_not_booked_without_a_known_contact_timezone() -> None:
    from lead_agent.infrastructure.db.models import LeadORM

    booked = meeting_email._attempt_requested_time_booking(
        object(),  # type: ignore[arg-type]
        cast(
            LeadORM,
            SimpleNamespace(id=uuid4(), phone=None, geography=None),
        ),
        uuid4(),
        "actor-1",
        "source-message-1",
        None,
        "How does tomorrow 12 pm sound?",
        datetime.now(UTC),
    )

    assert booked is False


def test_timezone_unknown_offer_asks_contact_to_confirm_displayed_timezone() -> None:
    from lead_agent.infrastructure.db.models import LeadORM

    _subject, body = meeting_email._offer_email(
        cast(
            LeadORM,
            SimpleNamespace(full_name="Buyer", phone=None, geography=None),
        ),
        {
            "subject": "Meeting request",
            "excerpt": "How does tomorrow 12 pm sound?",
            "sent_at": datetime.now(UTC).isoformat(),
        },
        "google_calendar",
        [
            {
                "start_at": "2026-10-28T09:00:00+05:00",
                "time_zone": "Asia/Karachi",
                "event_type_name": "30 Minute Meeting",
            }
        ],
    )

    assert "time zone isn't clear" in body
    assert "shown in Asia/Karachi" in body
    assert "Please confirm that time zone is correct" in body


def test_google_calendar_suggestions_use_contact_timezone_override() -> None:
    class Calendar:
        def primary_calendar(self) -> dict[str, str]:
            return {"timeZone": "UTC"}

        def busy_intervals(
            self, _start: datetime, _end: datetime
        ) -> list[dict[str, str]]:
            return []

    slots = recommend_google_calendar_slots(
        Calendar(),  # type: ignore[arg-type]
        connection_id="calendar-1",
        account_name="calendar@example.test",
        source_message_id="message-1",
        now=datetime(2026, 10, 27, 0, tzinfo=UTC),
        requested_date=date(2026, 10, 28),
        time_zone_override="Asia/Karachi",
        limit=1,
    )

    assert slots[0]["time_zone"] == "Asia/Karachi"
    assert slots[0]["start_at"] == "2026-10-28T09:00:00+05:00"


def test_calendly_suggestions_display_slots_in_contact_timezone() -> None:
    class Calendly:
        def current_user(self) -> dict[str, str]:
            return {"uri": "user-1", "timezone": "UTC"}

        def event_types(self, _user_uri: str) -> list[dict[str, str]]:
            return [{"uri": "type-1", "name": "30 Minute Meeting", "duration": "30"}]

        def available_times(
            self, _uri: str, _start: datetime, _end: datetime
        ) -> list[dict[str, str]]:
            return [{"start_time": "2026-10-28T12:00:00Z"}]

    slots = recommend_calendly_slots(
        Calendly(),  # type: ignore[arg-type]
        connection_id="calendly-1",
        source_message_id="message-1",
        now=datetime(2026, 10, 27, 0, tzinfo=UTC),
        requested_date=date(2026, 10, 28),
        time_zone_override="Asia/Karachi",
    )

    assert len(slots) == 1
    assert slots[0]["time_zone"] == "Asia/Karachi"
    assert datetime.fromisoformat(slots[0]["start_at"]).astimezone(
        ZoneInfo("Asia/Karachi")
    ).hour == 17


def test_calendly_exact_request_uses_contact_timezone_not_owner_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = SimpleNamespace(id=uuid4(), provider_type="calendly")
    lead = SimpleNamespace(
        id=uuid4(),
        phone="+923016057779",
        geography=None,
        email="buyer@example.test",
        full_name="Buyer",
    )
    reference = datetime(2026, 10, 27, 12, tzinfo=UTC)
    requested_start = datetime(2026, 10, 28, 12, tzinfo=ZoneInfo("Asia/Karachi"))
    captured: list[ConfirmMeetingRequest] = []

    class Calendly:
        def current_user(self) -> dict[str, str]:
            return {"uri": "user-1", "timezone": "UTC"}

        def event_types(self, _user_uri: str) -> list[dict[str, str]]:
            return [{"uri": "type-1", "name": "30 Minute Meeting", "duration": "30"}]

        def available_times(
            self, _uri: str, _start: datetime, _end: datetime
        ) -> list[dict[str, str]]:
            return [{"start_time": requested_start.astimezone(UTC).isoformat()}]

    monkeypatch.setattr(
        meeting_email,
        "_active_connection",
        lambda _session, _workspace_id, provider: (
            connection if provider == "calendly" else None
        ),
    )
    monkeypatch.setattr(
        meeting_email,
        "_authorized_credentials",
        lambda *_args, **_kwargs: {"access_token": "calendly-token"},
    )
    monkeypatch.setattr(meeting_email, "CalendlyClient", lambda _token: Calendly())
    def confirm(
        _lead_id: object,
        request: ConfirmMeetingRequest,
        **_kwargs: object,
    ) -> SimpleNamespace:
        captured.append(request)
        return SimpleNamespace(provider_event_id="event-1")

    monkeypatch.setattr("lead_agent.api.routes.meetings.confirm_meeting", confirm)

    booked = meeting_email._attempt_requested_time_booking(
        object(),  # type: ignore[arg-type]
        lead,  # type: ignore[arg-type]
        uuid4(),
        "actor-1",
        "source-message-1",
        str(uuid4()),
        "How does tomorrow 12 pm sound?",
        reference,
    )

    assert booked is True
    assert len(captured) == 1
    assert captured[0].start_at == requested_start
    assert captured[0].time_zone == "Asia/Karachi"


def test_meeting_options_are_saved_as_a_draft_and_notify_the_assigned_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lead_agent.infrastructure.db.models import (
        LeadFollowUpEmailORM,
        LeadORM,
        PipelineEventORM,
    )

    workspace_id = uuid4()
    lead_id = uuid4()
    assigned_user_id = uuid4()
    gmail_connection = SimpleNamespace(id=uuid4())
    calendar_connection = SimpleNamespace(
        id=uuid4(), provider_type="google_calendar"
    )
    now = datetime.now(UTC)
    slots = [
        {
            "provider": "google_calendar",
            "connection_id": str(calendar_connection.id),
            "account_name": "Calendar",
            "start_at": (now + timedelta(days=1)).isoformat(),
            "end_at": (now + timedelta(days=1, minutes=30)).isoformat(),
            "time_zone": "UTC",
            "source_message_id": "source-1",
        }
    ]
    notices: list[dict[str, object]] = []

    class Session:
        def __init__(self) -> None:
            self.added: list[object] = []

        def scalars(self, _statement: Any) -> Iterator[Any]:
            return iter([])

        def scalar(self, _statement: Any) -> Any:
            return SimpleNamespace(assigned_rep_id=str(assigned_user_id))

        def add(self, value: object) -> None:
            self.added.append(value)

        def commit(self) -> None:
            pass

    monkeypatch.setattr(
        meeting_email,
        "_calendar_options",
        lambda *_args, **_kwargs: (calendar_connection, slots),
    )
    monkeypatch.setattr(
        meeting_email, "assert_outreach_allowed", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        meeting_email,
        "notify_lead_action",
        lambda _session, **kwargs: notices.append(kwargs),
    )

    session = Session()
    result = meeting_email._send_options(
        session,  # type: ignore[arg-type]
        cast(
            LeadORM,
            SimpleNamespace(
                id=lead_id, full_name="Junaid Buyer", email="buyer@example.test"
            ),
        ),
        workspace_id,
        gmail_connection,  # type: ignore[arg-type]
        [],
        {
            "thread_id": "thread-1",
            "message_id": "source-1",
            "rfc_message_id": "<source@example.test>",
            "subject": "Meeting request",
            "excerpt": "Please share some meeting times.",
            "sent_at": now.isoformat(),
        },
        "workspace-owner",
    )

    offer = next(row for row in session.added if isinstance(row, PipelineEventORM))
    draft = next(row for row in session.added if isinstance(row, LeadFollowUpEmailORM))
    assert result == "draft_created"
    assert offer.status == "awaiting_send"
    assert draft.status == "draft"
    assert draft.recipient == "buyer@example.test"
    assert draft.kind == f"meeting_options:{offer.id}"
    assert notices[0]["recipient_user_id"] == assigned_user_id
    assert notices[0]["kind"] == "meeting_reply_draft"


def test_sending_meeting_draft_replies_in_original_gmail_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lead_agent.application import follow_up
    from lead_agent.infrastructure.gmail import GMAIL_SEND_SCOPE

    workspace_id = uuid4()
    lead_id = uuid4()
    gmail_connection_id = uuid4()
    offer_id = uuid4()
    draft_id = uuid4()
    offer = SimpleNamespace(
        id=offer_id,
        workspace_id=workspace_id,
        lead_id=lead_id,
        stage="meeting_email_offer",
        status="awaiting_send",
        details={
            "email_draft_id": str(draft_id),
            "gmail_connection_id": str(gmail_connection_id),
            "thread_id": "thread-1",
            "source_rfc_message_id": "<source@example.test>",
        },
    )
    draft = SimpleNamespace(
        id=draft_id,
        kind=f"meeting_options:{offer_id}",
        status="draft",
        workspace_id=workspace_id,
        lead_id=lead_id,
        provider_connection_id=gmail_connection_id,
        recipient="buyer@example.test",
        subject="Re: Meeting request",
        body="Here are several available meeting times.",
        attempts=0,
        approved_by=None,
        approved_at=None,
        error_message=None,
        updated_at=None,
        external_message_id=None,
        sent_at=None,
    )
    connection = SimpleNamespace(
        id=gmail_connection_id,
        workspace_id=workspace_id,
        provider_type="gmail",
        is_active=True,
        status="verified",
        scopes=[GMAIL_SEND_SCOPE],
        account_name="team@example.test",
        connection_metadata={"gmail_email": "team@example.test"},
    )
    sent: list[dict[str, object]] = []

    class Session:
        def __init__(self) -> None:
            self.scalar_calls = 0

        def scalar(self, _statement: Any) -> Any:
            self.scalar_calls += 1
            return offer if self.scalar_calls == 1 else connection

        def execute(self, _statement: Any) -> Any:
            return SimpleNamespace(rowcount=1)

        def commit(self) -> None:
            pass

        def refresh(self, _row: object) -> None:
            pass

    class Resolver:
        def resolve_connection(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(credentials={"access_token": "gmail-token"})

    class Outbound:
        def __init__(self, *_args: Any) -> None:
            pass

        def send_email(self, **kwargs: Any) -> str:
            sent.append(kwargs)
            return "outbound-message-1"

    monkeypatch.setattr(follow_up, "assert_outreach_allowed", lambda *_args: None)
    monkeypatch.setattr(
        follow_up, "get_workspace_connection_resolver", lambda: Resolver()
    )
    monkeypatch.setattr(follow_up, "GmailOutboundProvider", Outbound)

    result = follow_up.send_approved_follow_up(
        Session(),  # type: ignore[arg-type]
        draft,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        actor_id="assigned-member",
    )

    assert result.status == "sent"
    assert offer.status == "awaiting_confirmation"
    assert offer.details["outbound_message_id"] == "outbound-message-1"
    assert sent[0]["thread_id"] == "thread-1"
    assert sent[0]["in_reply_to"] == "<source@example.test>"
