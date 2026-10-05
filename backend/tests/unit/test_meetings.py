from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx

from lead_agent.api.routes.meetings import ConfirmMeetingRequest, confirm_meeting
from lead_agent.application.meetings import (
    find_meeting_request,
    match_confirmed_meeting_option,
    meeting_reply_requests_other_times,
    recommend_calendly_slots,
    recommend_google_calendar_slots,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.calendar_providers import (
    CALENDLY_SCOPES,
    GOOGLE_CALENDAR_EVENTS_SCOPE,
    GOOGLE_CALENDAR_FREEBUSY_SCOPE,
    GOOGLE_CALENDAR_METADATA_SCOPE,
    CalendlyConnectionValidator,
    GoogleCalendarConnectionValidator,
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

