from __future__ import annotations

import pytest

from lead_agent.application.claims import NEUTRAL_SENTENCE, NEUTRAL_SUBJECT, guard_outgoing_email

CLAIMS = (
    "I wanted to confirm that our meeting is scheduled for tomorrow at 3 PM.",
    "You're all set for Thursday at 3 PM - see you then!",
    "Your call is locked in for tomorrow at 3 PM.",
    "I've gone ahead and put us down for 3 PM tomorrow.",
    "Our meeting is on the calendar for tomorrow at 3 PM.",
    "The demo has been booked for Friday at 10am.",
    "We look forward to our meeting tomorrow at 3 PM.",
)

SAFE = (
    "Please confirm which time works best for you.",
    "Could you confirm your availability this week?",
    "Choose a time that works for you using my booking link: https://calendly.com/x",
    "Thanks for reaching out about integrating a RAG assistant.",
    "I'd be glad to talk this through with you.",
    "The meeting is not confirmed until you pick a slot.",
)


@pytest.mark.parametrize("sentence", CLAIMS)
def test_unverified_claims_are_replaced(sentence: str) -> None:
    result = guard_outgoing_email(None, f"Hi Sam,\n\n{sentence}\n\nBest,\nTeam", meeting_verified=False)
    assert result.changed
    assert NEUTRAL_SENTENCE in (result.body or "")
    assert sentence not in (result.body or "")
    assert (result.body or "").startswith("Hi Sam,")


@pytest.mark.parametrize("sentence", SAFE)
def test_requests_and_neutral_text_are_untouched(sentence: str) -> None:
    result = guard_outgoing_email("Hello", sentence, meeting_verified=False)
    assert not result.changed and result.body == sentence


def test_verified_meeting_is_not_rewritten() -> None:
    text = "Your meeting is confirmed for tomorrow at 3 PM."
    assert not guard_outgoing_email("Meeting confirmed", text, meeting_verified=True).changed


def test_subject_claims_are_neutralised() -> None:
    result = guard_outgoing_email(
        "Your Meeting Confirmation for Tomorrow at 3 PM", "Hi", meeting_verified=False
    )
    assert result.subject == NEUTRAL_SUBJECT
    reply = guard_outgoing_email("Re: Meeting confirmed", "Hi", meeting_verified=False)
    assert reply.subject == f"Re: {NEUTRAL_SUBJECT}"


def test_repeated_claims_collapse_to_one_neutral_sentence() -> None:
    body = "Our meeting is booked for 3 PM tomorrow. You're all set. See you then."
    result = guard_outgoing_email(None, body, meeting_verified=False)
    assert (result.body or "").count(NEUTRAL_SENTENCE) == 1
