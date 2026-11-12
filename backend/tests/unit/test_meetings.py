from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

import lead_agent.application.meetings as meeting_application
from lead_agent.api.routes.leads import (
    RetryConfirmedMeetingBookingRequest,
    retry_saved_meeting_booking,
)
from lead_agent.api.routes.meetings import (
    ConfirmMeetingRequest,
    MeetingSuggestion,
    MeetingProviderError,
    _calendly_invitee_location,
    create_meeting_offer_draft,
    confirm_meeting,
    get_meeting_recommendations,
)
from lead_agent.application import meeting_email
from lead_agent.application.meetings import (
    find_meeting_request,
    match_confirmed_meeting_option,
    meeting_reply_declines_offer,
    meeting_reply_requests_other_times,
    recommend_calendly_slots,
    recommend_google_calendar_slots,
    requested_meeting_date,
    requested_meeting_datetime,
    requested_meeting_time,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.domain.meeting_intent import MeetingIntentClassification
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


def _editable_owner(workspace_id, actor_id: str = "owner") -> WorkspaceContext:
    """Use the same explicit permission shape provided by the request dependency."""
    return WorkspaceContext(
        workspace_id=workspace_id,
        actor_id=actor_id,
        role="owner",
        permissions=frozenset({"leads.edit", "integrations.gmail.use"}),
    )


@pytest.fixture(autouse=True)
def _mock_workspace_meeting_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    class TestReasoningProvider:
        provider_name = "test"
        model_name = "meeting-intent-test"
        prompt_version = "test"

        def classify_meeting_intent(self, payload: dict[str, object]):
            candidates = [
                message
                for message in payload.get("messages", [])
                if isinstance(message, dict) and message.get("candidate_key")
            ]
            for message in reversed(candidates):
                body = str(message.get("body") or "")
                match = next(
                    (
                        line.strip()
                        for line in body.splitlines()
                        if any(
                            term in line.casefold()
                            for term in (
                                "meeting",
                                "call",
                                "demo",
                                "appointment",
                                "schedule",
                                "book",
                                "tomorrow",
                                "time",
                                "time between",
                                "availability",
                            )
                        )
                    ),
                    None,
                )
                if match:
                    return MeetingIntentClassification(
                        intent="meeting_request",
                        candidate_key=str(message["candidate_key"]),
                        confidence=0.99,
                        evidence=match[:240],
                    )
            return MeetingIntentClassification(
                intent="not_meeting",
                candidate_key="",
                confidence=0.99,
                evidence="",
            )

    monkeypatch.setattr(
        meeting_application,
        "resolve_workspace_reasoning_provider",
        lambda *_args: TestReasoningProvider(),
    )


def test_manual_offer_draft_uses_only_a_freshly_available_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    lead_id = uuid4()
    actor_id = str(uuid4())
    calendar_connection_id = uuid4()
    gmail_connection_id = uuid4()
    start_at = datetime.now(UTC) + timedelta(days=2)
    end_at = start_at + timedelta(minutes=30)
    request = ConfirmMeetingRequest(
        provider="calendly",
        connection_id=calendar_connection_id,
        source_message_id="source-1",
        source_gmail_connection_id=gmail_connection_id,
        start_at=start_at,
        end_at=end_at,
        time_zone="UTC",
        event_type_uri="https://api.calendly.com/event_types/type-1",
        event_type_name="30 Minute Meeting",
    )
    suggestion = MeetingSuggestion(
        provider="calendly",
        connection_id=calendar_connection_id,
        account_name="Calendly",
        source_message_id="source-1",
        source_gmail_connection_id=gmail_connection_id,
        start_at=start_at,
        end_at=end_at,
        time_zone="UTC",
        event_type_uri=request.event_type_uri,
        event_type_name=request.event_type_name,
    )
    lead = SimpleNamespace(
        id=lead_id, workspace_id=workspace_id, email="buyer@example.test"
    )
    workspace = WorkspaceContext(
        workspace_id=workspace_id,
        actor_id=actor_id,
        role="owner",
        permissions=frozenset({"leads.edit"}),
    )
    draft_id = uuid4()
    received: list[dict[str, object]] = []

    monkeypatch.setattr(
        "lead_agent.api.routes.meetings._lead_or_404",
        lambda *_args, **_kwargs: lead,
    )
    monkeypatch.setattr(
        "lead_agent.api.routes.meetings.get_meeting_recommendations",
        lambda *_args, **_kwargs: SimpleNamespace(suggestions=[suggestion]),
    )

    def create_draft(_session, **kwargs):
        received.append(kwargs)
        return SimpleNamespace(id=draft_id)

    monkeypatch.setattr(
        "lead_agent.api.routes.meetings.create_selected_slot_offer_draft",
        create_draft,
    )

    response = create_meeting_offer_draft(
        lead_id,
        request,
        session=object(),  # type: ignore[arg-type]
        workspace=workspace,
    )

    assert response.draft_id == draft_id
    assert response.status == "draft"
    assert received[0]["lead"] is lead
    assert received[0]["workspace_id"] == workspace_id
    assert (
        datetime.fromisoformat(str(received[0]["slot"]["start_at"]).replace("Z", "+00:00"))
        == start_at
    )


def test_manual_offer_draft_rejects_a_slot_missing_from_fresh_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    lead_id = uuid4()
    start_at = datetime.now(UTC) + timedelta(days=2)
    request = ConfirmMeetingRequest(
        provider="calendly",
        connection_id=uuid4(),
        source_message_id="source-1",
        source_gmail_connection_id=uuid4(),
        start_at=start_at,
        end_at=start_at + timedelta(minutes=30),
        time_zone="UTC",
        event_type_uri="https://api.calendly.com/event_types/type-1",
        event_type_name="30 Minute Meeting",
    )
    lead = SimpleNamespace(
        id=lead_id, workspace_id=workspace_id, email="buyer@example.test"
    )
    workspace = WorkspaceContext(
        workspace_id=workspace_id,
        actor_id=str(uuid4()),
        role="owner",
        permissions=frozenset({"leads.edit"}),
    )
    monkeypatch.setattr(
        "lead_agent.api.routes.meetings._lead_or_404",
        lambda *_args, **_kwargs: lead,
    )
    monkeypatch.setattr(
        "lead_agent.api.routes.meetings.get_meeting_recommendations",
        lambda *_args, **_kwargs: SimpleNamespace(suggestions=[]),
    )
    monkeypatch.setattr(
        "lead_agent.api.routes.meetings.create_selected_slot_offer_draft",
        lambda *_args, **_kwargs: pytest.fail("Unavailable slots must not create drafts."),
    )

    with pytest.raises(HTTPException) as error:
        create_meeting_offer_draft(
            lead_id,
            request,
            session=object(),  # type: ignore[arg-type]
            workspace=workspace,
        )
    assert error.value.status_code == 409


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


def test_meeting_reply_requires_a_clear_valid_option_number() -> None:
    assert match_confirmed_meeting_option("Option 2 works for me.", 3) == 2
    assert match_confirmed_meeting_option("I choose option 1.", 3) == 1
    assert match_confirmed_meeting_option("3", 3) == 3
    assert match_confirmed_meeting_option("Yes, Tuesday works.", 3) is None
    assert match_confirmed_meeting_option("Option 2 could work.", 3) is None
    assert match_confirmed_meeting_option("Option 4 works.", 3) is None
    assert match_confirmed_meeting_option("Option 2 doesn't work; option 3 does.", 3) is None
    assert match_confirmed_meeting_option("Option 1 or option 2.", 3) is None
    assert match_confirmed_meeting_option("1, but none of these times work.", 1) is None
    assert meeting_reply_requests_other_times("None of those times work.") is True
    assert meeting_reply_requests_other_times("none") is True
    assert meeting_reply_requests_other_times("Option 2 works.") is False
    assert meeting_reply_declines_offer("No, that doesn't work.") is True
    assert meeting_reply_declines_offer("No problem, that works for me.") is False
    assert meeting_reply_declines_offer("I am not able to attend.") is True


def test_ai_meeting_reply_interpretation_requires_unambiguous_option_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    options = [
        {
            "start_at": "2026-11-03T14:00:00+00:00",
            "end_at": "2026-11-03T14:30:00+00:00",
            "time_zone": "UTC",
            "event_type_name": "30 Minute Meeting",
        },
        {
            "start_at": "2026-11-04T14:00:00+00:00",
            "end_at": "2026-11-04T14:30:00+00:00",
            "time_zone": "UTC",
            "event_type_name": "30 Minute Meeting",
        },
    ]

    class Classifier:
        def __init__(self, result: dict[str, object]) -> None:
            self.result = result
            self.payload: dict[str, object] | None = None

        def classify_meeting_reply(self, payload: dict[str, object]) -> dict[str, object]:
            self.payload = payload
            return self.result

    ordinal_classifier = Classifier(
        {
            "intent": "confirm",
            "option_number": 2,
            "confidence": 0.99,
            "evidence": "the 2nd one",
        }
    )
    monkeypatch.setattr(
        meeting_email,
        "resolve_workspace_reasoning_provider",
        lambda *_args, **_kwargs: ordinal_classifier,
    )
    result, source = meeting_email._classify_meeting_reply_with_ai(
        object(),  # type: ignore[arg-type]
        workspace_id,
        "Sure, the 2nd one.",
        options,
    )
    assert source == "ai"
    assert result is not None
    assert result.intent == "confirm"
    assert result.option_number == 2
    assert ordinal_classifier.payload is not None
    assert len(ordinal_classifier.payload["options"]) == 2  # type: ignore[arg-type]

    vague_classifier = Classifier(
        {
            "intent": "confirm",
            "option_number": 2,
            "confidence": 0.99,
            "evidence": "that time makes sense",
        }
    )
    monkeypatch.setattr(
        meeting_email,
        "resolve_workspace_reasoning_provider",
        lambda *_args, **_kwargs: vague_classifier,
    )
    vague_result, _ = meeting_email._classify_meeting_reply_with_ai(
        object(),  # type: ignore[arg-type]
        workspace_id,
        "That time makes sense.",
        options,
    )
    assert vague_result is not None
    assert vague_result.intent == "uncertain"
    assert vague_result.option_number == 0

    single_classifier = Classifier(
        {
            "intent": "confirm",
            "option_number": 1,
            "confidence": 0.99,
            "evidence": "sure, that time makes sense",
        }
    )
    monkeypatch.setattr(
        meeting_email,
        "resolve_workspace_reasoning_provider",
        lambda *_args, **_kwargs: single_classifier,
    )
    single_result, _ = meeting_email._classify_meeting_reply_with_ai(
        object(),  # type: ignore[arg-type]
        workspace_id,
        "Sure, that time makes sense.",
        options[:1],
    )
    assert single_result is not None
    assert single_result.intent == "confirm"
    assert single_result.option_number == 1

    tentative_classifier = Classifier(
        {
            "intent": "confirm",
            "option_number": 2,
            "confidence": 0.99,
            "evidence": "maybe the second one",
        }
    )
    monkeypatch.setattr(
        meeting_email,
        "resolve_workspace_reasoning_provider",
        lambda *_args, **_kwargs: tentative_classifier,
    )
    tentative_result, _ = meeting_email._classify_meeting_reply_with_ai(
        object(),  # type: ignore[arg-type]
        workspace_id,
        "Maybe the second one.",
        options,
    )
    assert tentative_result is not None
    assert tentative_result.intent == "uncertain"

    decline_classifier = Classifier(
        {
            "intent": "decline",
            "option_number": 0,
            "confidence": 0.99,
            "evidence": "No thanks",
        }
    )
    monkeypatch.setattr(
        meeting_email,
        "resolve_workspace_reasoning_provider",
        lambda *_args, **_kwargs: decline_classifier,
    )
    decline_result, _ = meeting_email._classify_meeting_reply_with_ai(
        object(),  # type: ignore[arg-type]
        workspace_id,
        "No thanks.",
        options,
    )
    assert decline_result is not None
    assert decline_result.intent == "decline"

    invalid_classifier = Classifier(
        {
            "intent": "confirm",
            "option_number": "2",
            "confidence": 0.99,
            "evidence": "the second one",
        }
    )
    monkeypatch.setattr(
        meeting_email,
        "resolve_workspace_reasoning_provider",
        lambda *_args, **_kwargs: invalid_classifier,
    )
    invalid_result, invalid_source = meeting_email._classify_meeting_reply_with_ai(
        object(),  # type: ignore[arg-type]
        workspace_id,
        "Sure, the second one.",
        options,
    )
    assert invalid_result is None
    assert invalid_source == "unavailable"


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


def test_tomorrow_time_uses_the_connected_calendar_timezone() -> None:
    reference = datetime(2026, 10, 27, 20, 30, tzinfo=UTC)
    text = "Worth giving it a shot. How does tomorrow 12 pm sound?"

    assert (
        requested_meeting_date(
            text, reference=reference, time_zone="Asia/Karachi"
        ).isoformat()
        == "2026-10-29"
    )
    assert requested_meeting_time(text).isoformat() == "12:00:00"
    assert requested_meeting_datetime(
        text, time_zone="Asia/Karachi", reference=reference
    ).isoformat() == "2026-10-29T12:00:00+05:00"


def test_meeting_request_detection_accepts_a_relative_exact_time_proposal() -> None:
    sent_at = datetime(2026, 10, 27, 20, 30, tzinfo=UTC)
    message = SimpleNamespace(
        status="inbound",
        details={
            "direction": "inbound",
            "sender": "lead@example.test",
            "subject": "Re: Quick chat",
            "body": "Worth giving it a shot. How does tomorrow 12 pm sound?",
            "message_id": "message-1",
            "thread_id": "thread-1",
            "gmail_connection_id": "gmail-1",
            "sent_at": sent_at.isoformat(),
        },
    )
    session = SimpleNamespace(scalars=lambda _query: [message])
    lead = SimpleNamespace(id=uuid4(), email="lead@example.test")

    source = find_meeting_request(
        session,  # type: ignore[arg-type]
        lead,  # type: ignore[arg-type]
        uuid4(),
        thread_id="thread-1",
        gmail_connection_id="gmail-1",
    )

    assert source is not None
    assert source["message_id"] == "message-1"


def test_exact_time_request_books_after_calendar_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lead_id = uuid4()
    workspace_id = uuid4()
    gmail_connection = SimpleNamespace(id=uuid4())
    now = datetime.now(UTC)
    lead = SimpleNamespace(
        id=lead_id,
        email="lead@example.test",
        phone="+923016057779",
        geography="Pakistan",
    )
    source = {
        "thread_id": "thread-1",
        "message_id": "message-1",
        "gmail_connection_id": str(gmail_connection.id),
        "excerpt": "Can we set up a meeting tomorrow at 2 pm?",
        "sent_at": now.isoformat(),
    }
    message = {
        "thread_id": "thread-1",
        "message_id": "message-1",
        "direction": "inbound",
        "sender": "lead@example.test",
        "body": source["excerpt"],
        "sent_at": now.isoformat(),
    }
    session = SimpleNamespace(
        scalar=lambda _statement: lead_id,
        flush=lambda: None,
        commit=lambda: None,
    )
    captured: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        meeting_email,
        "record_gmail_thread_messages",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(meeting_email, "_thread_offer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(meeting_email, "find_meeting_request", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(
        meeting_email,
        "_attempt_requested_time_booking",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        meeting_email,
        "_send_options",
        lambda *args: captured.append(args) or "draft_created",
    )

    result = meeting_email.process_gmail_meeting_thread(
        session,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        lead=lead,  # type: ignore[arg-type]
        gmail_connection=gmail_connection,  # type: ignore[arg-type]
        messages=[message],
        actor_id="workspace-owner",
    )

    assert result == "booked"
    assert captured == []


def test_request_without_a_date_creates_booking_link_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    lead = SimpleNamespace(
        id=uuid4(), full_name="Kathi", email="kathi@example.test",
        phone=None, geography=None,
    )
    gmail = SimpleNamespace(id=uuid4())
    calendar = SimpleNamespace(
        id=uuid4(), provider_type="google_calendar", account_name="Sales",
    )
    now = datetime.now(UTC)
    booking_url = "https://calendar.app.google/sales-booking"
    source = {
        "thread_id": "thread-1", "message_id": "message-1",
        "gmail_connection_id": str(gmail.id), "subject": "Meeting",
        "excerpt": "Can we meet sometime next week?", "sent_at": now.isoformat(),
    }
    slots = [{
        "provider": "google_calendar",
        "connection_id": str(calendar.id),
        "source_message_id": source["message_id"],
        "source_gmail_connection_id": str(gmail.id),
        "booking_link_mode": True,
        "booking_url": booking_url,
    }]
    session = MagicMock()
    monkeypatch.setattr(meeting_email, "_thread_offer", lambda *_args: None)
    monkeypatch.setattr(
        meeting_email, "_calendar_options",
        lambda *_args, **_kwargs: (calendar, slots),
    )
    monkeypatch.setattr(
        meeting_email,
        "_offer_email",
        lambda *_args: (
            "Re: Meeting",
            f"Thanks for your message. Choose a time here: {booking_url}",
        ),
    )
    monkeypatch.setattr(meeting_email, "assert_outreach_allowed", lambda *_args: None)
    monkeypatch.setattr(meeting_email, "_notify_meeting_draft", lambda *_args, **_kwargs: None)

    result = meeting_email._send_options(
        session, lead, workspace_id, gmail, [], source, "owner",
    )

    assert result == "draft_created"
    offer, draft = (call.args[0] for call in session.add.call_args_list)
    assert offer.details["options"] == []
    assert offer.details["booking_link_mode"] is True
    assert offer.details["booking_link_url"] == booking_url
    assert booking_url in draft.body
    assert "Reply with" not in draft.body


def test_new_exact_time_reply_books_after_calendar_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lead_id = uuid4()
    workspace_id = uuid4()
    gmail_connection = SimpleNamespace(id=uuid4())
    now = datetime.now(UTC)
    lead = SimpleNamespace(
        id=lead_id,
        email="lead@example.test",
        phone="+923016057779",
        geography="Pakistan",
    )
    source = {
        "thread_id": "thread-1",
        "message_id": "request-1",
        "gmail_connection_id": str(gmail_connection.id),
        "excerpt": "Could we meet sometime tomorrow?",
        "sent_at": (now - timedelta(hours=2)).isoformat(),
    }
    offer = SimpleNamespace(
        status="awaiting_confirmation",
        details={
            "thread_id": "thread-1",
            "gmail_connection_id": str(gmail_connection.id),
            "source_message_id": "request-1",
            "outbound_message_id": "booking-link-1",
            "offered_at": (now - timedelta(hours=1)).isoformat(),
            "options": [{"start_at": (now + timedelta(days=1)).isoformat()}],
        },
    )
    messages = [
        {
            "thread_id": "thread-1",
            "message_id": "request-1",
            "direction": "inbound",
            "sender": "lead@example.test",
            "body": source["excerpt"],
            "sent_at": source["sent_at"],
        },
        {
            "thread_id": "thread-1",
            "message_id": "booking-link-1",
            "direction": "outbound",
            "sender": "rep@example.test",
            "body": "Here is the booking link.",
            "sent_at": offer.details["offered_at"],
        },
        {
            "thread_id": "thread-1",
            "message_id": "reply-1",
            "direction": "inbound",
            "sender": "lead@example.test",
            "body": "Tomorrow at 2 pm instead would work.",
            "sent_at": now.isoformat(),
        },
    ]
    session = SimpleNamespace(
        scalar=lambda _statement: lead_id,
        flush=lambda: None,
        commit=lambda: None,
    )
    captured: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        meeting_email,
        "record_gmail_thread_messages",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(meeting_email, "_thread_offer", lambda *_args, **_kwargs: offer)
    monkeypatch.setattr(meeting_email, "find_meeting_request", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(meeting_email, "match_confirmed_meeting_option", lambda *_args: None)
    monkeypatch.setattr(meeting_email, "_cached_reply_interpretation", lambda *_args: None)
    monkeypatch.setattr(
        meeting_email,
        "_classify_meeting_reply_with_ai",
        lambda *_args: (
            SimpleNamespace(
                intent="decline",
                option_number=None,
                confidence=0.99,
            ),
            "test",
        ),
    )
    monkeypatch.setattr(
        meeting_email,
        "_store_reply_interpretation",
        lambda details, *_args, **_kwargs: details,
    )
    monkeypatch.setattr(meeting_email, "lead_time_zone", lambda *_args: "Asia/Karachi")
    monkeypatch.setattr(
        meeting_email,
        "_attempt_requested_time_booking",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        meeting_email,
        "_send_options",
        lambda *args: captured.append(args) or "draft_created",
    )

    result = meeting_email.process_gmail_meeting_thread(
        session,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        lead=lead,  # type: ignore[arg-type]
        gmail_connection=gmail_connection,  # type: ignore[arg-type]
        messages=messages,
        actor_id="workspace-owner",
    )

    assert result == "booked"
    assert offer.status == "superseded"
    assert offer.details["closed_reason"] == "lead_requested_new_time"
    assert captured == []


def test_date_only_request_creates_three_slots_on_requested_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    lead = SimpleNamespace(
        id=uuid4(), full_name="Kathi", email="kathi@example.test",
        phone="+923016057779", geography="Pakistan",
    )
    gmail = SimpleNamespace(id=uuid4())
    calendar = SimpleNamespace(
        id=uuid4(), provider_type="calendly", account_name="Sales",
    )
    now = datetime.now(UTC)
    source = {
        "thread_id": "thread-1", "message_id": "message-1",
        "gmail_connection_id": str(gmail.id), "subject": "Meeting",
        "excerpt": "Can we meet tomorrow?", "sent_at": now.isoformat(),
    }
    slots = [
        {
            "provider": "calendly",
            "connection_id": str(calendar.id),
            "source_message_id": source["message_id"],
            "source_gmail_connection_id": str(gmail.id),
            "start_at": (now + timedelta(days=1, hours=index + 1)).isoformat(),
            "end_at": (now + timedelta(days=1, hours=index + 1, minutes=30)).isoformat(),
            "time_zone": "Asia/Karachi",
            "event_type_name": "30 Minute Meeting",
        }
        for index in range(5)
    ]
    session = MagicMock()
    monkeypatch.setattr(meeting_email, "_thread_offer", lambda *_args: None)
    monkeypatch.setattr(
        meeting_email, "_available_offer_slots", lambda *_args, **_kwargs: (calendar, slots[:3])
    )
    monkeypatch.setattr(meeting_email, "assert_outreach_allowed", lambda *_args: None)
    monkeypatch.setattr(meeting_email, "_notify_meeting_draft", lambda *_args, **_kwargs: None)

    result = meeting_email._send_options(
        session, lead, workspace_id, gmail, [], source, "owner",
    )

    assert result == "draft_created"
    offer, draft = (call.args[0] for call in session.add.call_args_list)
    assert offer.details["booking_link_mode"] is False
    assert len(offer.details["options"]) == 3
    assert "1. " in draft.body
    assert "2. " in draft.body
    assert "3. " in draft.body
    assert "4. " not in draft.body


def test_exact_time_booking_uses_contact_timezone_and_source_mailbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_id = uuid4()
    lead = SimpleNamespace(id=uuid4(), phone="+923016057779", geography=None)
    workspace_id = uuid4()
    gmail_connection_id = str(uuid4())
    captured: list[ConfirmMeetingRequest] = []

    class Calendar:
        def primary_calendar(self):
            return {"timeZone": "UTC"}

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
    reference = datetime.now(UTC) + timedelta(days=2)
    requested_date = requested_meeting_date(
        "tomorrow", reference=reference, time_zone="Asia/Karachi"
    )

    booked = meeting_email._attempt_requested_time_booking(
        object(),  # type: ignore[arg-type]
        lead,  # type: ignore[arg-type]
        workspace_id,
        "actor-1",
        "source-message-1",
        gmail_connection_id,
        "How does tomorrow 12 pm sound?",
        reference,
    )

    assert booked is True
    assert len(captured) == 1
    assert requested_date is not None
    assert captured[0].start_at.isoformat() == (
        f"{requested_date.isoformat()}T12:00:00+05:00"
    )
    assert str(captured[0].source_gmail_connection_id) == gmail_connection_id


def test_calendly_event_type_location_is_sent_with_invitee_creation() -> None:
    location = _calendly_invitee_location(
        {"locations": [{"kind": "google_conference"}]}
    )
    assert location == {"kind": "google_conference"}

    class FakeApi:
        def request(self, method: str, path: str, *, payload: dict[str, object]):
            assert method == "POST"
            assert path == "invitees"
            assert payload["location"] == location
            return {"resource": {"uri": "https://api.calendly.com/invitees/123"}}

    client = CalendlyClient("calendly-token")
    client.api = FakeApi()  # type: ignore[assignment]
    invitee = client.create_invitee(
        event_type_uri="https://api.calendly.com/event_types/demo",
        start_at=datetime(2026, 11, 6, 14, tzinfo=UTC),
        name="Buyer Person",
        email="buyer@example.test",
        time_zone="UTC",
        location=location,
    )

    assert invitee["uri"] == "https://api.calendly.com/invitees/123"


def test_calendly_invitee_location_checks_event_type_configuration() -> None:
    assert _calendly_invitee_location(
        {"locations": [{"kind": "physical", "location": "Room 1"}]}
    ) == {"kind": "physical", "location": "Room 1"}
    assert _calendly_invitee_location(
        {
            "pooling_type": "round_robin",
            "locations": [{"kind": "google_conference"}],
        }
    ) is None

    with pytest.raises(HTTPException) as multiple_locations:
        _calendly_invitee_location(
            {
                "locations": [
                    {"kind": "google_conference"},
                    {"kind": "zoom_conference"},
                ]
            }
        )
    assert multiple_locations.value.status_code == 409

    with pytest.raises(HTTPException) as invitee_location:
        _calendly_invitee_location({"locations": [{"kind": "ask_invitee"}]})
    assert invitee_location.value.status_code == 409


def test_exact_calendly_booking_skips_disconnected_event_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_start = (datetime.now(UTC) + timedelta(days=2)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )
    connection = SimpleNamespace(
        id=uuid4(), provider_type="calendly", account_name="admin@example.test"
    )
    captured: list[ConfirmMeetingRequest] = []

    class Calendly:
        def current_user(self):
            return {
                "uri": "https://api.calendly.com/users/123",
                "timezone": "UTC",
            }

        def event_types(self, _user_uri: str):
            return [
                {
                    "uri": "https://api.calendly.com/event_types/disconnected",
                    "name": "Disconnected calendar",
                    "duration": 30,
                    "active": True,
                },
                {
                    "uri": "https://api.calendly.com/event_types/working",
                    "name": "Working calendar",
                    "duration": 30,
                    "active": True,
                },
            ]

        def available_times(self, event_type_uri: str, _start: datetime, _end: datetime):
            if event_type_uri.endswith("/disconnected"):
                raise _http_error(
                    "calendly",
                    httpx.Response(
                        400,
                        json={
                            "details": [
                                {
                                    "parameter": "event_type",
                                    "message": "Failed to connect to external calendar.",
                                }
                            ]
                        },
                    ),
                    "event_type_available_times",
                )
            if event_type_uri.endswith("/no-open-times"):
                return []
            return [{"start_time": requested_start.isoformat()}]

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
    monkeypatch.setattr(
        "lead_agent.api.routes.meetings.confirm_meeting",
        lambda _lead_id, request, **_kwargs: (
            captured.append(request) or SimpleNamespace(provider_event_id="event-1")
        ),
    )

    booked = meeting_email._attempt_requested_time_booking(
        object(),  # type: ignore[arg-type]
        SimpleNamespace(id=uuid4(), time_zone="UTC"),  # type: ignore[arg-type]
        uuid4(),
        "actor-1",
        "source-message-1",
        str(uuid4()),
        (
            f"{requested_start.strftime('%B')} {requested_start.day}, "
            f"{requested_start.year} at 2:00 PM"
        ),
        datetime.now(UTC),
    )

    assert booked is True
    assert len(captured) == 1
    assert captured[0].event_type_uri == "https://api.calendly.com/event_types/working"


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


def test_calendar_options_prefers_calendly_booking_link_when_both_are_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendly = SimpleNamespace(
        id=uuid4(), provider_type="calendly", account_name="calendar@example.test"
    )
    google = SimpleNamespace(
        id=uuid4(), provider_type="google_calendar", account_name="calendar@example.test"
    )

    monkeypatch.setattr(
        meeting_email,
        "_active_connection",
        lambda _session, _workspace_id, provider: (
            calendly if provider == "calendly" else google
        ),
    )
    monkeypatch.setattr(meeting_email, "meeting_booking_link", lambda *_args: "https://calendly.com/team/intro")

    result = meeting_email._calendar_options(
        object(),  # type: ignore[arg-type]
        uuid4(),
        {
            "message_id": "source-message-1",
            "gmail_connection_id": str(uuid4()),
        },
        "actor-1",
    )

    assert result is not None
    assert result[0] is calendly
    assert result[1][0]["booking_url"] == "https://calendly.com/team/intro"
    assert result[1][0]["booking_link_mode"] is True
    assert result[1][0]["source_message_id"] == "source-message-1"


def test_booking_link_draft_personalizes_the_gmail_thread_without_numbered_slots() -> None:
    subject, body = meeting_email._offer_email(
        SimpleNamespace(full_name="Mina Khan", company_name="Northstar Labs"),  # type: ignore[arg-type]
        {
            "subject": "Quarterly demo",
            "excerpt": "Could we meet next week?",
            "sent_at": datetime.now(UTC).isoformat(),
        },
        "calendly",
        [{"booking_url": "https://calendly.com/team/intro"}],
    )

    assert subject == "Re: Quarterly demo"
    assert body.startswith("Hi Mina,")
    assert "Thanks for reaching out about Quarterly demo." in body
    assert "https://calendly.com/team/intro" in body
    assert "Reply with" not in body
    assert "option 1" not in body


def test_google_booking_link_reconciliation_requires_new_matching_invitee_event() -> None:
    now = datetime.now(UTC)
    offered_at = now - timedelta(minutes=10)
    event = {
        "created": (now - timedelta(minutes=5)).isoformat(),
        "attendees": [{"email": "buyer@example.test"}],
        "organizer": {"self": True},
        "start": {
            "dateTime": (now + timedelta(days=2)).isoformat(),
            "timeZone": "UTC",
        },
        "end": {
            "dateTime": (now + timedelta(days=2, minutes=30)).isoformat(),
            "timeZone": "UTC",
        },
        "id": "google-event-1",
        "htmlLink": "https://calendar.google.com/event?eid=event-1",
    }

    match = meeting_email._booking_link_event_matches(
        event,
        provider="google_calendar",
        lead_email="buyer@example.test",
        offered_at=offered_at,
        now=now,
    )
    assert match is not None
    assert match["provider_event_id"] == "google-event-1"

    event["created"] = (offered_at - timedelta(seconds=1)).isoformat()
    assert meeting_email._booking_link_event_matches(
        event,
        provider="google_calendar",
        lead_email="buyer@example.test",
        offered_at=offered_at,
        now=now,
    ) is None


def test_confirm_meeting_reuses_existing_booking_and_closes_pending_offer() -> None:
    lead_id = uuid4()
    workspace_id = uuid4()
    source_id = "gmail-message-1"
    lead = SimpleNamespace(
        id=lead_id,
        workspace_id=workspace_id,
        email="buyer@example.test",
        full_name="Buyer Person",
        company_name="Buyer Company",
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
        workspace=_editable_owner(workspace_id, "admin"),
    )

    assert result.provider_event_id == "calendar-event-1"
    assert offer.status == "booked"
    assert offer.details["booking_method"] == "manual"
    assert session.commits == 1


def test_confirm_meeting_reconciles_existing_calendly_event_before_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lead_id = uuid4()
    workspace_id = uuid4()
    source_id = "gmail-message-calendly"
    gmail_connection_id = uuid4()
    connection_id = uuid4()
    event_type_uri = "https://api.calendly.com/event_types/30-minute"
    start_at = datetime.now(UTC) + timedelta(days=1)
    event_uri = "https://api.calendly.com/scheduled_events/event-123"
    lead = SimpleNamespace(
        id=lead_id,
        workspace_id=workspace_id,
        email="buyer@example.test",
        full_name="Buyer Person",
        company_name="Buyer Company",
    )
    source = SimpleNamespace(
        details={
            "message_id": source_id,
            "gmail_connection_id": str(gmail_connection_id),
            "thread_id": "gmail-thread-calendly",
            "direction": "inbound",
            "sender": "buyer@example.test",
            "subject": "Can we schedule a demo?",
            "body": "Could we schedule a product demo?",
        }
    )
    connection = SimpleNamespace(
        id=connection_id,
        provider_type="calendly",
        account_name="admin@example.test",
    )

    class BookingSession:
        def __init__(self) -> None:
            self.scalar_calls = 0
            self.scalar_results = [lead, lead_id, connection]
            self.scalars_results = [[source], [source], [], [], []]
            self.added: list[object] = []
            self.commits = 0

        def scalar(self, _statement):
            result = self.scalar_results[self.scalar_calls]
            self.scalar_calls += 1
            return result

        def scalars(self, _statement):
            return iter(self.scalars_results.pop(0))

        def add(self, value) -> None:
            self.added.append(value)

        def commit(self) -> None:
            self.commits += 1

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
                    "uri": event_type_uri,
                    "name": "30 Minute Meeting",
                    "duration": 30,
                    "active": True,
                }
            ]

        def scheduled_events(self, **_kwargs):
            return [
                {
                    "uri": event_uri,
                    "event_type": event_type_uri,
                    "start_time": start_at.isoformat(),
                }
            ]

        def available_times(self, *_args):
            raise AssertionError("An existing booking must be checked before availability.")

        def create_invitee(self, **_kwargs):
            raise AssertionError("An existing Calendly event must not be booked twice.")

    monkeypatch.setattr(
        "lead_agent.api.routes.meetings._resolve_credentials",
        lambda *_args, **_kwargs: {"access_token": "calendly-token"},
    )
    monkeypatch.setattr(
        "lead_agent.api.routes.meetings.CalendlyClient",
        lambda _token: Calendly(),
    )
    session = BookingSession()
    request = ConfirmMeetingRequest(
        provider="calendly",
        connection_id=connection_id,
        source_message_id=source_id,
        source_gmail_connection_id=gmail_connection_id,
        start_at=start_at,
        end_at=start_at + timedelta(minutes=30),
        time_zone="UTC",
        event_type_uri=event_type_uri,
        event_type_name="30 Minute Meeting",
    )

    result = confirm_meeting(
        lead_id,
        request,
        session=session,  # type: ignore[arg-type]
        workspace=_editable_owner(workspace_id, "admin"),
    )

    assert result.provider_event_id == "event-123"
    assert result.provider_event_uri == event_uri
    assert len(session.added) == 1
    assert session.added[0].stage == "meeting_scheduled"
    assert session.commits == 1


def test_confirm_meeting_sends_event_type_location_to_calendly_invitee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lead_id = uuid4()
    workspace_id = uuid4()
    source_id = "gmail-message-calendly-location"
    gmail_connection_id = uuid4()
    connection_id = uuid4()
    event_type_uri = "https://api.calendly.com/event_types/30-minute"
    event_uri = "https://api.calendly.com/scheduled_events/event-location-123"
    start_at = datetime.now(UTC) + timedelta(days=1)
    location = {"kind": "google_conference"}
    lead = SimpleNamespace(
        id=lead_id,
        workspace_id=workspace_id,
        email="buyer@example.test",
        full_name="Buyer Person",
        company_name="Buyer Company",
    )
    source = SimpleNamespace(
        details={
            "message_id": source_id,
            "gmail_connection_id": str(gmail_connection_id),
            "thread_id": "gmail-thread-calendly-location",
            "direction": "inbound",
            "sender": "buyer@example.test",
            "subject": "Can we schedule a demo?",
            "body": "Could we schedule a product demo?",
        }
    )
    connection = SimpleNamespace(
        id=connection_id,
        provider_type="calendly",
        account_name="admin@example.test",
    )
    captured_invitees: list[dict[str, object]] = []

    class BookingSession:
        def __init__(self) -> None:
            self.scalar_results = [lead, lead_id, connection]
            self.scalars_results = [[source], [source], [], [], []]
            self.added: list[object] = []
            self.commits = 0

        def scalar(self, _statement):
            return self.scalar_results.pop(0)

        def scalars(self, _statement):
            return iter(self.scalars_results.pop(0))

        def add(self, value) -> None:
            self.added.append(value)

        def commit(self) -> None:
            self.commits += 1

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
                    "uri": event_type_uri,
                    "name": "30 Minute Meeting",
                    "duration": 30,
                    "active": True,
                    "locations": [location],
                }
            ]

        def scheduled_events(self, **_kwargs):
            return []

        def available_times(self, *_args):
            return [{"start_time": start_at.isoformat()}]

        def create_invitee(self, **kwargs):
            captured_invitees.append(kwargs)
            return {
                "uri": "https://api.calendly.com/invitees/invitee-123",
                "event": event_uri,
            }

    monkeypatch.setattr(
        "lead_agent.api.routes.meetings._resolve_credentials",
        lambda *_args, **_kwargs: {"access_token": "calendly-token"},
    )
    monkeypatch.setattr(
        "lead_agent.api.routes.meetings.CalendlyClient",
        lambda _token: Calendly(),
    )
    session = BookingSession()
    request = ConfirmMeetingRequest(
        provider="calendly",
        connection_id=connection_id,
        source_message_id=source_id,
        source_gmail_connection_id=gmail_connection_id,
        start_at=start_at,
        end_at=start_at + timedelta(minutes=30),
        time_zone="UTC",
        event_type_uri=event_type_uri,
        event_type_name="30 Minute Meeting",
    )

    result = confirm_meeting(
        lead_id,
        request,
        session=session,  # type: ignore[arg-type]
        workspace=_editable_owner(workspace_id, "admin"),
    )

    assert result.provider_event_id == "event-location-123"
    assert len(captured_invitees) == 1
    assert captured_invitees[0]["location"] == location
    assert len(session.added) == 1
    assert session.commits == 1


def test_gmail_refresh_retries_only_a_previously_confirmed_booking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lead_id = uuid4()
    workspace_id = uuid4()
    gmail_connection_id = uuid4()
    reply_id = "gmail-reply-confirmed"
    lead = SimpleNamespace(
        id=lead_id, workspace_id=workspace_id, email="buyer@example.test"
    )
    gmail_connection = SimpleNamespace(
        id=gmail_connection_id, workspace_id=workspace_id
    )
    offer = SimpleNamespace(
        status="needs_attention",
        details={
            "thread_id": "gmail-thread-1",
            "gmail_connection_id": str(gmail_connection_id),
            "source_message_id": "gmail-source-1",
            "reply_message_id": reply_id,
            "selected_option": 2,
            "options": [{}, {}, {}],
        },
    )
    message = {
        "message_id": reply_id,
        "thread_id": "gmail-thread-1",
        "direction": "inbound",
        "sender": "buyer@example.test",
        "body": "2",
        "sent_at": datetime.now(UTC).isoformat(),
    }

    class RefreshSession:
        def __init__(self) -> None:
            self.scalar_calls = 0
            self.scalars_results = [[], [offer]]
            self.added: list[object] = []

        def scalar(self, _statement):
            self.scalar_calls += 1
            return lead_id

        def scalars(self, _statement):
            return iter(self.scalars_results.pop(0))

        def add(self, value) -> None:
            self.added.append(value)

        def flush(self) -> None:
            pass

    captured: list[tuple[object, ...]] = []

    def retry_booking(*args):
        captured.append(args)
        return "booked"

    monkeypatch.setattr(meeting_email, "_book_selected_option", retry_booking)
    session = RefreshSession()

    result = meeting_email.process_gmail_meeting_thread(
        session,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        lead=lead,  # type: ignore[arg-type]
        gmail_connection=gmail_connection,  # type: ignore[arg-type]
        messages=[message],
        actor_id="workspace-owner",
    )

    assert result == "booked"
    assert len(captured) == 1
    assert captured[0][4:] == (offer, reply_id, 2)


def test_gmail_reply_ai_ordinal_confirmation_books_the_selected_offer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lead_id = uuid4()
    workspace_id = uuid4()
    gmail_connection_id = uuid4()
    now = datetime.now(UTC)
    reply_id = "gmail-reply-ordinal"
    lead = SimpleNamespace(
        id=lead_id, workspace_id=workspace_id, email="buyer@example.test"
    )
    gmail_connection = SimpleNamespace(id=gmail_connection_id)
    options = [
        {
            "start_at": (now + timedelta(days=1)).isoformat(),
            "end_at": (now + timedelta(days=1, minutes=30)).isoformat(),
            "time_zone": "UTC",
            "event_type_name": "30 Minute Meeting",
        },
        {
            "start_at": (now + timedelta(days=2)).isoformat(),
            "end_at": (now + timedelta(days=2, minutes=30)).isoformat(),
            "time_zone": "UTC",
            "event_type_name": "30 Minute Meeting",
        },
    ]
    offer = SimpleNamespace(
        status="awaiting_confirmation",
        details={
            "thread_id": "gmail-thread-ordinal",
            "gmail_connection_id": str(gmail_connection_id),
            "source_message_id": "gmail-source-ordinal",
            "outbound_message_id": "gmail-offer-ordinal",
            "offered_at": (now - timedelta(minutes=10)).isoformat(),
            "options": options,
        },
    )
    message = {
        "message_id": reply_id,
        "thread_id": "gmail-thread-ordinal",
        "direction": "inbound",
        "sender": "buyer@example.test",
        "body": "Sure, the 2nd one.",
        "sent_at": now.isoformat(),
    }

    class ReplySession:
        def __init__(self) -> None:
            self.scalar_results = [lead_id]
            self.scalars_results = [[], [offer], []]
            self.added: list[object] = []

        def scalar(self, _statement):
            return self.scalar_results.pop(0)

        def scalars(self, _statement):
            return iter(self.scalars_results.pop(0))

        def add(self, value) -> None:
            self.added.append(value)

        def flush(self) -> None:
            pass

        def commit(self) -> None:
            pass

    class Classifier:
        provider_name = "test"

        def classify_meeting_reply(self, payload: dict[str, object]) -> dict[str, object]:
            assert payload["reply"] == "Sure, the 2nd one."
            return {
                "intent": "confirm",
                "option_number": 2,
                "confidence": 0.99,
                "evidence": "the 2nd one",
            }

    captured: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        meeting_email,
        "resolve_workspace_reasoning_provider",
        lambda *_args, **_kwargs: Classifier(),
    )
    monkeypatch.setattr(
        meeting_email,
        "_book_selected_option",
        lambda *args: captured.append(args) or "booked",
    )

    result = meeting_email.process_gmail_meeting_thread(
        ReplySession(),  # type: ignore[arg-type]
        workspace_id=workspace_id,
        lead=lead,  # type: ignore[arg-type]
        gmail_connection=gmail_connection,  # type: ignore[arg-type]
        messages=[message],
        actor_id="workspace-owner",
    )

    assert result == "booked"
    assert len(captured) == 1
    assert captured[0][4:] == (offer, reply_id, 2)
    assert offer.details["reply_classifications"][reply_id]["intent"] == "confirm"
    assert offer.details["reply_classifications"][reply_id]["option_number"] == 2


def test_confirmed_booking_retry_stops_after_a_newer_lead_reply() -> None:
    reply_at = datetime.now(UTC)
    details = {
        "reply_message_id": "reply-1",
        "selected_option": 1,
        "options": [{}],
    }
    messages = [
        {
            "message_id": "reply-1",
            "direction": "inbound",
            "sender": "buyer@example.test",
            "body": "1",
            "sent_at": reply_at.isoformat(),
        },
        {
            "message_id": "reply-2",
            "direction": "inbound",
            "sender": "buyer@example.test",
            "body": "Actually, another time would be better.",
            "sent_at": (reply_at + timedelta(minutes=1)).isoformat(),
        },
    ]

    assert (
        meeting_email._confirmed_booking_retry(
            details, messages, "buyer@example.test"
        )
        is None
    )


def test_confirmed_booking_retry_reuses_ai_interpretation_for_an_ordinal_reply() -> None:
    reply_at = datetime.now(UTC)
    details = {
        "reply_message_id": "reply-1",
        "selected_option": 2,
        "options": [{}, {}, {}],
        "reply_classifications": {
            "reply-1": {
                "intent": "confirm",
                "option_number": 2,
                "confidence": 0.99,
                "source": "ai",
            }
        },
    }
    messages = [
        {
            "message_id": "reply-1",
            "direction": "inbound",
            "sender": "buyer@example.test",
            "body": "Sure, the second one.",
            "sent_at": reply_at.isoformat(),
        }
    ]

    assert meeting_email._confirmed_booking_retry(
        details, messages, "buyer@example.test"
    ) == ("reply-1", 2)


def test_confirmed_booking_retry_stops_when_choice_also_requests_other_times() -> None:
    details = {
        "reply_message_id": "reply-1",
        "selected_option": 1,
        "options": [{}],
    }
    messages = [
        {
            "message_id": "reply-1",
            "direction": "inbound",
            "sender": "buyer@example.test",
            "body": "1, but none of these times work. Please send other times.",
            "sent_at": datetime.now(UTC).isoformat(),
        }
    ]

    assert (
        meeting_email._confirmed_booking_retry(
            details, messages, "buyer@example.test"
        )
        is None
    )


def test_direct_booking_retry_fetches_the_saved_gmail_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lead_id = uuid4()
    workspace_id = uuid4()
    gmail_connection_id = uuid4()
    lead = SimpleNamespace(
        id=lead_id, workspace_id=workspace_id, email="buyer@example.test"
    )
    gmail_connection = SimpleNamespace(
        id=gmail_connection_id,
        workspace_id=workspace_id,
        provider_type="gmail",
        connection_type="workspace_managed",
        connection_metadata={},
    )
    offer = SimpleNamespace(
        status="needs_attention",
        details={
            "source_message_id": "gmail-source-1",
            "thread_id": "gmail-thread-1",
            "gmail_connection_id": str(gmail_connection_id),
            "reply_message_id": "gmail-reply-1",
            "selected_option": 2,
            "options": [{}, {}, {}],
        },
    )
    reply = {
        "message_id": "gmail-reply-1",
        "thread_id": "gmail-thread-1",
        "direction": "inbound",
        "sender": "buyer@example.test",
        "body": "2",
        "sent_at": datetime.now(UTC).isoformat(),
    }

    class RetrySession:
        def __init__(self) -> None:
            self.scalar_results = [lead, gmail_connection]
            self.scalar_calls = 0
            self.commits = 0

        def scalar(self, _statement):
            result = self.scalar_results[self.scalar_calls]
            self.scalar_calls += 1
            return result

        def scalars(self, _statement):
            return iter([offer])

        def commit(self) -> None:
            self.commits += 1

    class GmailProvider:
        def fetch_thread(self, thread_id: str, *, lead_email: str):
            assert thread_id == "gmail-thread-1"
            assert lead_email == "buyer@example.test"
            return [reply]

    class Resolver:
        def resolve(self, *_args, **_kwargs):
            return GmailProvider()

    def process_retry(_session, **kwargs):
        assert kwargs["messages"][0]["gmail_connection_id"] == str(gmail_connection_id)
        offer.status = "booked"
        offer.details = {**offer.details, "provider_event_id": "calendly-event-1"}
        return "booked"

    monkeypatch.setattr(meeting_email, "process_gmail_meeting_thread", process_retry)
    session = RetrySession()

    result = retry_saved_meeting_booking(
        lead_id,
        RetryConfirmedMeetingBookingRequest(offer_source_message_id="gmail-source-1"),
        session=session,  # type: ignore[arg-type]
        resolver=Resolver(),  # type: ignore[arg-type]
        workspace=_editable_owner(workspace_id),
    )

    assert result.status == "booked"
    assert "existing calendar event" in result.message
    assert session.commits == 1


def test_recommendations_expose_saved_offer_source_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lead_id = uuid4()
    workspace_id = uuid4()
    gmail_connection_id = str(uuid4())
    lead = SimpleNamespace(
        id=lead_id,
        workspace_id=workspace_id,
        email="buyer@example.test",
    )
    offer = SimpleNamespace(
        status="needs_attention",
        details={
            "source_message_id": "original-request-1",
            "thread_id": "gmail-thread-1",
            "gmail_connection_id": gmail_connection_id,
            "reply_message_id": "numbered-reply-1",
            "selected_option": 2,
            "options": [{}, {}, {}],
            "calendar_provider": "calendly",
        },
    )
    source = {
        "message_id": "numbered-reply-1",
        "thread_id": "gmail-thread-1",
        "gmail_connection_id": gmail_connection_id,
        "subject": "Re: Timing confirmation",
        "excerpt": "5",
        "sent_at": datetime.now(UTC).isoformat(),
    }

    class RecommendationsSession:
        def scalar(self, _statement):
            return None

        def scalars(self, _statement):
            return iter([offer])

    monkeypatch.setattr(
        "lead_agent.api.routes.meetings._lead_or_404",
        lambda *_args, **_kwargs: lead,
    )
    monkeypatch.setattr(
        "lead_agent.api.routes.meetings._workspace_calendar_connections",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "lead_agent.api.routes.meetings.find_meeting_request",
        lambda *_args, **_kwargs: source,
    )

    result = get_meeting_recommendations(
        lead_id,
        session=RecommendationsSession(),  # type: ignore[arg-type]
        workspace=WorkspaceContext(workspace_id=workspace_id, actor_id="owner"),
    )

    assert result.source_message_id == "numbered-reply-1"
    assert result.offer_source_message_id == "original-request-1"
    assert result.offer_has_confirmed_selection is True


def test_booked_meeting_survives_unavailable_thread_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lead_id, workspace_id = uuid4(), uuid4()
    booked_at = datetime.now(UTC) + timedelta(days=1)

    class BookedSession:
        def scalar(self, _statement):
            return SimpleNamespace(details={
                "provider": "calendly",
                "start_at": booked_at.isoformat(),
            })

    monkeypatch.setattr(
        "lead_agent.api.routes.meetings._lead_or_404",
        lambda *_args, **_kwargs: SimpleNamespace(id=lead_id),
    )
    monkeypatch.setattr(
        "lead_agent.api.routes.meetings._workspace_calendar_connections",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "lead_agent.api.routes.meetings.find_meeting_request",
        lambda *_args, **_kwargs: None,
    )

    result = get_meeting_recommendations(
        lead_id,
        session=BookedSession(),  # type: ignore[arg-type]
        workspace=WorkspaceContext(workspace_id=workspace_id, actor_id="owner"),
    )

    assert result.offer_status == "booked"
    assert result.booked_meeting_at == booked_at
    assert result.offer_calendar_provider == "calendly"


def test_sent_appointment_link_does_not_create_another_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        "thread_id": "gmail-thread-1",
        "gmail_connection_id": str(uuid4()),
        "message_id": "request-1",
        "sent_at": datetime.now(UTC).isoformat(),
    }
    offer = SimpleNamespace(
        status="awaiting_confirmation",
        details={
            "source_message_id": "request-1",
            "offered_at": datetime.now(UTC).isoformat(),
        },
    )
    monkeypatch.setattr(meeting_email, "_find_meeting_request_for_draft", lambda *_args: source)
    monkeypatch.setattr(meeting_email, "_thread_offer", lambda *_args: offer)

    with pytest.raises(HTTPException) as error:
        meeting_email.create_booking_link_offer_draft(
            session=object(),  # type: ignore[arg-type]
            lead=SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
            workspace_id=uuid4(),
            actor_id="owner",
        )

    assert error.value.status_code == 409
    assert "already sent" in error.value.detail


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


def test_find_meeting_request_detects_open_ended_time_window_reply() -> None:
    lead_id = uuid4()
    lead = SimpleNamespace(id=lead_id, email="buyer@example.test")
    events = [
        SimpleNamespace(
            status="inbound",
            details={
                "message_id": "time-window-reply",
                "thread_id": "meeting-thread",
                "direction": "inbound",
                "sender": "buyer@example.test",
                "subject": "Re: Meeting tomorrow",
                "body": (
                    "Hey,\nYes, tomorrow is fine with me, do you have any time "
                    "between 12 pm and 2 pm?\nRegards"
                ),
            },
        )
    ]

    result = find_meeting_request(_Session(events), lead, lead_id)

    assert result is not None
    assert result["message_id"] == "time-window-reply"
    assert result["thread_id"] == "meeting-thread"


def test_ai_meeting_intent_uses_the_full_gmail_thread_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    lead = SimpleNamespace(id=uuid4(), email="buyer@example.test")
    events = [
        SimpleNamespace(
            status="inbound",
            details={
                "message_id": "time-window-reply",
                "thread_id": "meeting-thread",
                "direction": "inbound",
                "sender": "buyer@example.test",
                "subject": "Re: Tomorrow",
                "body": "Yes, tomorrow works. Do you have time between 12 and 2?",
            },
        ),
        SimpleNamespace(
            status="outbound",
            details={
                "message_id": "prior-offer",
                "thread_id": "meeting-thread",
                "direction": "outbound",
                "sender": "rep@example.test",
                "subject": "Tomorrow",
                "body": "Would tomorrow at 2 pm work for a 30 minute call?",
            },
        ),
    ]
    captured: dict[str, object] = {}

    class ThreadReasoningProvider:
        provider_name = "test"
        model_name = "thread-context-test"
        prompt_version = "test"

        def classify_meeting_intent(self, payload: dict[str, object]):
            captured["payload"] = payload
            messages = payload["messages"]
            lead_message = next(
                message for message in messages if message["role"] == "lead"
            )
            return MeetingIntentClassification(
                intent="meeting_request",
                candidate_key=lead_message["candidate_key"],
                confidence=0.98,
                evidence="Do you have time between 12 and 2?",
            )

    monkeypatch.setattr(
        meeting_application,
        "resolve_workspace_reasoning_provider",
        lambda *_args: ThreadReasoningProvider(),
    )
    result = find_meeting_request(
        _Session(events),
        lead,
        workspace_id,
    )

    assert result is not None
    assert result["message_id"] == "time-window-reply"
    thread_payload = captured["payload"]
    assert isinstance(thread_payload, dict)
    assert any(message["role"] == "workspace" for message in thread_payload["messages"])
    assert any(message["role"] == "lead" for message in thread_payload["messages"])


def test_find_meeting_request_skips_numbered_reply_and_quoted_request_text() -> None:
    lead_id = uuid4()
    events = [
        SimpleNamespace(
            details={
                "message_id": "numbered-reply",
                "thread_id": "gmail-thread-1",
                "direction": "inbound",
                "sender": "buyer@example.test",
                "subject": "Re: Timing confirmation",
                "body": (
                    "5\n\nOn Monday, buyer@example.test wrote:\n"
                    "Can we schedule a meeting next week?"
                ),
                "sent_at": "2026-10-05T10:05:00+00:00",
            }
        ),
        SimpleNamespace(
            details={
                "message_id": "original-request",
                "thread_id": "gmail-thread-1",
                "direction": "inbound",
                "sender": "buyer@example.test",
                "subject": "Timing confirmation",
                "body": "Can we schedule a meeting next week?",
                "sent_at": "2026-10-05T09:55:00+00:00",
            }
        ),
    ]

    result = find_meeting_request(
        _Session(events),
        SimpleNamespace(id=lead_id, email="buyer@example.test"),
        lead_id,
    )

    assert result is not None
    assert result["message_id"] == "original-request"


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


def test_calendly_suggestions_skip_event_types_with_unavailable_calendars() -> None:
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
                    "uri": "https://api.calendly.com/event_types/disconnected",
                    "name": "Disconnected calendar",
                    "duration": 30,
                    "active": True,
                },
                {
                    "uri": "https://api.calendly.com/event_types/working",
                    "name": "Working calendar",
                    "duration": 30,
                    "active": True,
                },
                {
                    "uri": "https://api.calendly.com/event_types/no-open-times",
                    "name": "No open times",
                    "duration": 30,
                    "active": True,
                },
            ]

        def available_times(self, event_type_uri: str, _start: datetime, _end: datetime):
            if event_type_uri.endswith("/disconnected"):
                raise _http_error(
                    "calendly",
                    httpx.Response(
                        400,
                        json={
                            "details": [
                                {
                                    "parameter": "event_type",
                                    "message": "Failed to connect to external calendar.",
                                }
                            ]
                        },
                    ),
                    "event_type_available_times",
                )
            if event_type_uri.endswith("/no-open-times"):
                return []
            return [{"start_time": "2026-11-06T14:00:00Z"}]

    event_type_statuses: list[dict[str, str]] = []
    slots = recommend_calendly_slots(
        Calendly(),  # type: ignore[arg-type]
        connection_id="connection-2",
        source_message_id="message-2",
        now=datetime(2026, 10, 5, 10, tzinfo=UTC),
        requested_date=datetime(2026, 11, 6, tzinfo=UTC).date(),
        event_type_statuses=event_type_statuses,
    )

    assert len(slots) == 1
    assert slots[0]["event_type_uri"].endswith("/working")
    assert slots[0]["event_type_name"] == "Working calendar"
    assert [event_type["status"] for event_type in event_type_statuses] == [
        "calendar_unavailable",
        "available",
        "no_times",
    ]


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


def test_calendly_external_calendar_failure_gives_reconnect_guidance() -> None:
    error = _http_error(
        "calendly",
        httpx.Response(
            400,
            json={
                "message": "The supplied parameters are invalid.",
                "details": [
                    {
                        "parameter": "event_type",
                        "message": "Failed to connect to external calendar.",
                    }
                ],
            },
        ),
        "event_type_available_times",
    )

    assert error.code == "calendly_external_calendar_unavailable"
    assert error.category == "configuration"
    assert "Reconnect that calendar in Calendly" in str(error)
    assert error.retryable is False


def test_meeting_provider_error_includes_provider_error_code() -> None:
    response_error = MeetingProviderError(
        provider="calendly",
        message="Reconnect that calendar in Calendly.",
        retryable=False,
        code="calendly_external_calendar_unavailable",
    )

    assert response_error.model_dump()["code"] == "calendly_external_calendar_unavailable"


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

