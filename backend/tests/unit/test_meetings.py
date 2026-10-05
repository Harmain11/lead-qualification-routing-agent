from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx

from lead_agent.application.meetings import (
    find_meeting_request,
    recommend_calendly_slots,
    recommend_google_calendar_slots,
)
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
    }


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

