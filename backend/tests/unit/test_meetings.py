from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from lead_agent.api.routes.meetings import ConfirmMeetingRequest, confirm_meeting
from lead_agent.application import meeting_email
from lead_agent.application.meetings import (
    find_meeting_request,
    match_confirmed_meeting_option,
    meeting_reply_requests_other_times,
    recommend_calendly_slots,
    recommend_google_calendar_slots,
    requested_meeting_date,
    requested_meeting_datetime,
    requested_meeting_time,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.calendar_providers import (
    CALENDLY_SCOPES,
    GOOGLE_CALENDAR_EVENTS_SCOPE,
    GOOGLE_CALENDAR_FREEBUSY_SCOPE,
    GOOGLE_CALENDAR_METADATA_SCOPE,
    CalendlyClient,
    CalendlyConnectionValidator,
    GoogleCalendarConnectionValidator,
    _http_error,
)
from lead_agent.infrastructure.oauth import ProviderOAuthBroker
from lead_agent.settings.config import Settings


class _Session:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows

    def scalars(self, _statement):
        return iter(self.rows)


def test_find_meeting_request_uses_latest_matching_synced_message() -> None:
    lead_id = uuid4()
    events = [
        SimpleNamespace(
            details={
                "message_id": "msg-2",
                "thread_id": "thread-1",
                "subject": "Demo follow-up",
                "body": (
                    "Could we schedule a product demo next week?\n\n"
                    "On Monday, someone wrote:\n> quoted older text"
                ),
            }
        ),
        SimpleNamespace(
            details={
                "message_id": "msg-1",
                "subject": "Initial inquiry",
                "body": "Please send a short overview.",
            }
        ),
    ]

    result = find_meeting_request(_Session(events), SimpleNamespace(id=lead_id), lead_id)

    assert result == {
        "message_id": "msg-2",
        "thread_id": "thread-1",
        "subject": "Demo follow-up",
        "excerpt": "Could we schedule a product demo next week?",
        "sent_at": "",
        "rfc_message_id": "",
        "gmail_connection_id": "",
    }


def test_find_meeting_request_ignores_outbound_and_non_lead_senders() -> None:
    lead_id = uuid4()
    lead = SimpleNamespace(id=lead_id, email="buyer@example.test")
    events = [
        SimpleNamespace(
            status="outbound",
            details={
                "message_id": "sent",
                "thread_id": "thread-1",
                "subject": "Schedule a meeting",
                "body": "Please choose a time.",
                "sender": "rep@example.test",
            },
        ),
        SimpleNamespace(
            status="inbound",
            details={
                "message_id": "other",
                "thread_id": "thread-1",
                "subject": "Schedule a meeting",
                "body": "Can we book a demo?",
                "sender": "other@example.test",
            },
        ),
    ]

    assert find_meeting_request(
        _Session(events), lead, lead_id, thread_id="thread-1"
    ) is None


def test_find_meeting_request_can_be_scoped_to_the_source_gmail_mailbox() -> None:
    workspace_id = uuid4()
    lead = SimpleNamespace(id=uuid4(), email="buyer@example.test")
    events = [
        SimpleNamespace(
            details={
                "message_id": "same-provider-id",
                "thread_id": "same-thread-id",
                "gmail_connection_id": "mailbox-a",
                "sender": lead.email,
                "subject": "Can we schedule a demo?",
                "body": "Could we book a demo?",
            }
        ),
        SimpleNamespace(
            details={
                "message_id": "same-provider-id",
                "thread_id": "same-thread-id",
                "gmail_connection_id": "mailbox-b",
                "sender": lead.email,
                "subject": "Can we schedule a demo?",
                "body": "Could we book a demo?",
            }
        ),
    ]

    result = find_meeting_request(
        _Session(events),
        lead,
        workspace_id,
        thread_id="same-thread-id",
        gmail_connection_id="mailbox-b",
    )

    assert result is not None
    assert result["gmail_connection_id"] == "mailbox-b"


def test_gmail_message_deduplication_is_scoped_to_the_source_mailbox() -> None:
    existing = [
        SimpleNamespace(
            details={
                "message_id": "same-provider-id",
                "gmail_connection_id": "mailbox-a",
            }
        )
    ]
    added = []

    class RecordingSession:
        def scalars(self, _statement):
            return iter(existing)

        def add(self, event):
            added.append(event)
            existing.append(event)

        def flush(self):
            pass

    session = RecordingSession()
    message = {
        "message_id": "same-provider-id",
        "thread_id": "thread-b",
        "direction": "inbound",
        "sent_at": "2026-10-05T12:00:00+00:00",
    }
    mailbox_a = uuid4()
    mailbox_b = uuid4()

    first = meeting_email.record_gmail_thread_messages(
        session,  # type: ignore[arg-type]
        workspace_id=uuid4(),
        lead_id=uuid4(),
        gmail_connection_id=mailbox_a,
        messages=[message],
    )
    second = meeting_email.record_gmail_thread_messages(
        session,  # type: ignore[arg-type]
        workspace_id=uuid4(),
        lead_id=uuid4(),
        gmail_connection_id=mailbox_b,
        messages=[message],
    )
    third = meeting_email.record_gmail_thread_messages(
        session,  # type: ignore[arg-type]
        workspace_id=uuid4(),
        lead_id=uuid4(),
        gmail_connection_id=mailbox_b,
        messages=[message],
    )

    assert first == 1
    assert second == 1
    assert third == 0
    assert len(added) == 2


def test_meeting_reply_requires_an_explicit_valid_option_number() -> None:
    assert match_confirmed_meeting_option("Option 2 works for me.", 3) == 2
    assert match_confirmed_meeting_option("I choose option 1.", 3) == 1
    assert match_confirmed_meeting_option("3", 3) == 3
    assert match_confirmed_meeting_option("Yes, Tuesday works.", 3) is None
    assert match_confirmed_meeting_option("Option 4 works.", 3) is None
    assert match_confirmed_meeting_option("Option 2 doesn't work; option 3 does.", 3) is None
    assert match_confirmed_meeting_option("Option 1 or option 2.", 3) is None
    assert meeting_reply_requests_other_times("None of those times work.") is True
    assert meeting_reply_requests_other_times("none") is True
    assert meeting_reply_requests_other_times("Option 2 works.") is False


def test_meeting_date_and_time_parsing_is_explicit_and_timezone_aware() -> None:
    reference = datetime(2026, 10, 5, 12, tzinfo=UTC)
    text = "Could we meet on November 6, 2026 at 2:30 PM?"

    assert requested_meeting_date(text, reference=reference).isoformat() == "2026-11-06"
    assert requested_meeting_time(text).isoformat() == "14:30:00"
    assert requested_meeting_datetime(
        text, time_zone="Asia/Karachi", reference=reference
    ).isoformat() == "2026-11-06T14:30:00+05:00"
    assert requested_meeting_time("November 6 at 2:30") is None
    assert requested_meeting_time("November 6 at 14:30").isoformat() == "14:30:00"


def test_exact_time_booking_uses_calendar_timezone_and_source_mailbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_id = uuid4()
    lead = SimpleNamespace(id=uuid4())
    workspace_id = uuid4()
    gmail_connection_id = str(uuid4())
    captured: list[ConfirmMeetingRequest] = []

    class Calendar:
        def primary_calendar(self):
            return {"timeZone": "Asia/Karachi"}

    monkeypatch.setattr(
        meeting_email,
        "_active_connection",
        lambda _session, _workspace_id, provider: (
            SimpleNamespace(id=connection_id)
            if provider == "google_calendar"
            else None
        ),
    )
    monkeypatch.setattr(
        meeting_email,
        "_authorized_credentials",
        lambda *_args, **_kwargs: {"access_token": "calendar-token"},
    )
    monkeypatch.setattr(
        meeting_email,
        "GoogleCalendarClient",
        lambda _token: Calendar(),
    )

    def confirm(_lead_id, request, **_kwargs):
        captured.append(request)
        return SimpleNamespace(provider_event_id="event-1")

    monkeypatch.setattr(
        "lead_agent.api.routes.meetings.confirm_meeting",
        confirm,
    )

    booked = meeting_email._attempt_requested_time_booking(
        object(),  # type: ignore[arg-type]
        lead,  # type: ignore[arg-type]
        workspace_id,
        "actor-1",
        "source-message-1",
        gmail_connection_id,
        "November 6, 2026 at 2:30 PM",
        datetime(2026, 10, 5, 12, tzinfo=UTC),
    )

    assert booked is True
    assert len(captured) == 1
    assert captured[0].start_at.isoformat() == "2026-11-06T14:30:00+05:00"
    assert str(captured[0].source_gmail_connection_id) == gmail_connection_id


def test_date_only_request_does_not_attempt_direct_booking() -> None:
    booked = meeting_email._attempt_requested_time_booking(
        object(),  # type: ignore[arg-type]
        SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
        uuid4(),
        "actor-1",
        "source-message-1",
        str(uuid4()),
        "November 6, 2026",
        datetime(2026, 10, 5, 12, tzinfo=UTC),
    )

    assert booked is False


def test_calendar_options_fall_back_when_requested_date_has_no_openings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = SimpleNamespace(
        id=uuid4(), provider_type="google_calendar", account_name="calendar@example.test"
    )
    queried_dates: list[object] = []

    def recommend(_client, *, requested_date=None, **_kwargs):
        queried_dates.append(requested_date)
        return [] if requested_date is not None else [{"start_at": "nearby-slot"}]

    monkeypatch.setattr(
        meeting_email,
        "_active_connection",
        lambda *_args, **_kwargs: connection,
    )
    monkeypatch.setattr(
        meeting_email,
        "_authorized_credentials",
        lambda *_args, **_kwargs: {"access_token": "calendar-token"},
    )
    monkeypatch.setattr(
        meeting_email,
        "GoogleCalendarClient",
        lambda _token: object(),
    )
    monkeypatch.setattr(
        meeting_email,
        "recommend_google_calendar_slots",
        recommend,
    )

    result = meeting_email._calendar_options(
        object(),  # type: ignore[arg-type]
        uuid4(),
        {
            "message_id": "source-message-1",
            "gmail_connection_id": str(uuid4()),
            "excerpt": "Could we book a demo on November 6, 2026?",
            "sent_at": "2026-10-05T12:00:00+00:00",
        },
        "actor-1",
    )

    assert result is not None
    assert result[1] == [{"start_at": "nearby-slot"}]
    assert len(queried_dates) == 2
    assert queried_dates[0].isoformat() == "2026-11-06"
    assert queried_dates[1] is None


def test_confirm_meeting_reuses_existing_booking_and_closes_pending_offer() -> None:
    lead_id = uuid4()
    workspace_id = uuid4()
    source_id = "gmail-message-1"
    lead = SimpleNamespace(
        id=lead_id,
        workspace_id=workspace_id,
        email="buyer@example.test",
        full_name="Buyer Person",
    )
    source = SimpleNamespace(
        details={
            "message_id": source_id,
            "thread_id": "gmail-thread-1",
            "direction": "inbound",
            "sender": "buyer@example.test",
            "subject": "Can we schedule a demo?",
            "body": "Could we schedule a product demo?",
        }
    )
    scheduled_at = datetime.now(UTC) + timedelta(days=1)
    existing_event = SimpleNamespace(
        details={
            "provider": "google_calendar",
            "provider_event_id": "calendar-event-1",
            "title": "Meeting with Buyer Person",
            "source_message_id": source_id,
            "start_at": scheduled_at.isoformat(),
            "end_at": (scheduled_at + timedelta(minutes=30)).isoformat(),
            "time_zone": "UTC",
        }
    )
    offer = SimpleNamespace(
        status="awaiting_confirmation",
        details={"source_message_id": source_id, "thread_id": "gmail-thread-1"},
    )

    class BookingSession:
        def __init__(self) -> None:
            self.scalar_calls = 0
            self.scalar_results = [lead, lead_id]
            self.scalars_results = [
                [source],
                [source],
                [existing_event],
                [offer],
            ]
            self.commits = 0

        def scalar(self, _statement):
            result = self.scalar_results[self.scalar_calls]
            self.scalar_calls += 1
            return result

        def scalars(self, _statement):
            return iter(self.scalars_results.pop(0))

        def commit(self):
            self.commits += 1

    session = BookingSession()
    request = ConfirmMeetingRequest(
        provider="google_calendar",
        connection_id=uuid4(),
        source_message_id=source_id,
        start_at=scheduled_at,
        end_at=scheduled_at + timedelta(minutes=30),
        time_zone="UTC",
    )

    result = confirm_meeting(
        lead_id,
        request,
        session=session,  # type: ignore[arg-type]
        workspace=WorkspaceContext(workspace_id=workspace_id, actor_id="admin"),
    )

    assert result.provider_event_id == "calendar-event-1"
    assert offer.status == "booked"
    assert offer.details["booking_method"] == "manual"
    assert session.commits == 1


def test_find_meeting_request_ignores_messages_without_meeting_intent() -> None:
    lead_id = uuid4()
    events = [
        SimpleNamespace(
            details={
                "message_id": "msg-1",
                "subject": "Project overview",
                "body": "Please send the updated pricing and team details.",
            }
        )
    ]

    assert find_meeting_request(
        _Session(events), SimpleNamespace(id=lead_id), lead_id
    ) is None


def test_google_calendar_suggestions_exclude_busy_time() -> None:
    current = datetime(2026, 10, 4, 12, tzinfo=UTC)
    busy_start = datetime(2026, 10, 5, 9, 30, tzinfo=UTC)
    busy_end = datetime(2026, 10, 5, 10, 30, tzinfo=UTC)

    class Calendar:
        def primary_calendar(self):
            return {"timeZone": "UTC"}

        def busy_intervals(self, _start: datetime, _end: datetime):
            return [
                {
                    "start": busy_start.isoformat(),
                    "end": busy_end.isoformat(),
                }
            ]

    slots = recommend_google_calendar_slots(
        Calendar(),  # type: ignore[arg-type]
        connection_id="connection-1",
        account_name="calendar@example.test",
        source_message_id="message-1",
        now=current,
        limit=5,
    )

    assert len(slots) == 5
    assert slots[0]["start_at"] == "2026-10-05T09:00:00+00:00"
    for slot in slots:
        start = datetime.fromisoformat(slot["start_at"])
        end = datetime.fromisoformat(slot["end_at"])
        assert end - start == timedelta(minutes=30)
        assert not (start < busy_end and end > busy_start)
        assert slot["source_message_id"] == "message-1"


def test_calendly_suggestions_include_event_duration_and_timezone() -> None:
    start_at = datetime(2026, 10, 5, 16, tzinfo=UTC)

    class Calendly:
        def current_user(self):
            return {
                "uri": "https://api.calendly.com/users/123",
                "email": "admin@example.test",
                "timezone": "Asia/Karachi",
            }

        def event_types(self, _user_uri: str):
            return [
                {
                    "uri": "https://api.calendly.com/event_types/456",
                    "name": "Intro call",
                    "duration": 45,
                    "active": True,
                }
            ]

        def available_times(self, _event_type_uri: str, _start: datetime, _end: datetime):
            return [{"start_time": start_at.isoformat()}]

    slots = recommend_calendly_slots(
        Calendly(),  # type: ignore[arg-type]
        connection_id="connection-2",
        source_message_id="message-2",
        now=datetime(2026, 10, 4, tzinfo=UTC),
    )

    assert len(slots) == 1
    assert slots[0]["event_type_name"] == "Intro call"
    assert slots[0]["account_name"] == "admin@example.test"
    assert slots[0]["start_at"] == start_at.isoformat()
    assert slots[0]["end_at"] == (start_at + timedelta(minutes=45)).isoformat()
    assert slots[0]["time_zone"] == "Asia/Karachi"
    assert slots[0]["source_message_id"] == "message-2"


def test_calendly_suggestions_query_the_requested_day_with_a_future_buffer() -> None:
    requested_day = datetime(2026, 11, 6, tzinfo=UTC).date()
    current = datetime(2026, 10, 5, 10, tzinfo=UTC)
    query_ranges: list[tuple[datetime, datetime]] = []

    class Calendly:
        def current_user(self):
            return {
                "uri": "https://api.calendly.com/users/123",
                "email": "admin@example.test",
                "timezone": "UTC",
            }

        def event_types(self, _user_uri: str):
            return [
                {
                    "uri": "https://api.calendly.com/event_types/456",
                    "name": "Intro call",
                    "duration": 30,
                    "active": True,
                }
            ]

        def available_times(self, _event_type_uri: str, start: datetime, end: datetime):
            query_ranges.append((start, end))
            return [{"start_time": "2026-11-06T14:00:00Z"}]

    slots = recommend_calendly_slots(
        Calendly(),  # type: ignore[arg-type]
        connection_id="connection-2",
        source_message_id="message-2",
        now=current,
        requested_date=requested_day,
    )

    assert len(slots) == 1
    assert query_ranges == [
        (
            datetime(2026, 11, 6, 0, tzinfo=UTC),
            datetime(2026, 11, 7, 0, tzinfo=UTC),
        )
    ]
    assert slots[0]["start_at"] == "2026-11-06T14:00:00+00:00"


def test_calendly_availability_serializes_utc_as_z() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["start_time"] == "2026-11-06T09:00:00Z"
        assert request.url.params["end_time"] == "2026-11-06T10:00:00Z"
        return httpx.Response(200, json={"collection": []})

    client = CalendlyClient(
        "calendly-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.available_times(
        "https://api.calendly.com/event_types/123",
        datetime(2026, 11, 6, 9, tzinfo=UTC),
        datetime(2026, 11, 6, 10, tzinfo=UTC),
    )


def test_calendly_bad_request_includes_provider_validation_detail() -> None:
    error = _http_error(
        "calendly",
        httpx.Response(
            400,
            json={
                "message": "Request validation failed.",
                "details": [
                    {
                        "parameter": "start_time",
                        "message": "must be within the supported booking window.",
                    }
                ],
            },
        ),
        "event_type_available_times",
    )

    assert "Calendly rejected the request." in str(error)
    assert "start_time" in str(error)
    assert "supported booking window" in str(error)


def test_calendar_connection_validators_require_scopes_and_check_account_identity() -> None:
    def google_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer google-token"
        assert request.url.path.endswith("/calendars/primary")
        return httpx.Response(
            200,
            json={"id": "primary", "summary": "Sales calendar", "timeZone": "UTC"},
        )

    google_client = httpx.Client(transport=httpx.MockTransport(google_handler))
    google_validator = GoogleCalendarConnectionValidator(client=google_client)
    missing_calendar_metadata = google_validator.validate(
        "google_calendar",
        {"access_token": "google-token"},
        [GOOGLE_CALENDAR_EVENTS_SCOPE, GOOGLE_CALENDAR_FREEBUSY_SCOPE],
    )
    assert missing_calendar_metadata.status == "insufficient_permissions"
    google_result = google_validator.validate(
        "google_calendar",
        {"access_token": "google-token"},
        [
            GOOGLE_CALENDAR_EVENTS_SCOPE,
            GOOGLE_CALENDAR_FREEBUSY_SCOPE,
            GOOGLE_CALENDAR_METADATA_SCOPE,
        ],
    )
    assert google_result.verified
    assert google_result.metadata["google_calendar_name"] == "Sales calendar"

    def calendly_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer calendly-token"
        assert request.url.path.endswith("/users/me")
        return httpx.Response(
            200,
            json={
                "resource": {
                    "uri": "https://api.calendly.com/users/123",
                    "email": "admin@example.test",
                    "timezone": "Asia/Karachi",
                }
            },
        )

    calendly_client = httpx.Client(transport=httpx.MockTransport(calendly_handler))
    calendly_validator = CalendlyConnectionValidator(client=calendly_client)
    calendly_result = calendly_validator.validate(
        "calendly",
        {"access_token": "calendly-token"},
        list(CALENDLY_SCOPES),
    )
    assert calendly_result.verified
    assert calendly_result.metadata["calendly_email"] == "admin@example.test"


def test_calendly_oauth_uses_pkce_and_requested_scopes() -> None:
    captured: list[httpx.Request] = []

    def token_handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"access_token": "access-token"})

    broker = ProviderOAuthBroker(
        Settings(
            calendly_client_id="client-id",
            calendly_client_secret="client-secret",
        ),
        client=httpx.Client(transport=httpx.MockTransport(token_handler)),
    )
    redirect_uri = "https://corral.example.test/v1/integrations/calendly/oauth/callback"
    authorization_url = broker.begin(
        "calendly",
        uuid4(),
        "oauth-state",
        "pkce-challenge",
        redirect_uri,
        list(CALENDLY_SCOPES),
    )

    tokens = broker.exchange(
        "calendly",
        "authorization-code",
        "pkce-verifier",
        redirect_uri,
    )

    assert "code_challenge=pkce-challenge" in authorization_url
    assert "code_challenge_method=S256" in authorization_url
    assert "scope=users%3Aread" in authorization_url
    assert tokens["access_token"] == "access-token"
    assert b"code_verifier=pkce-verifier" in captured[0].content

