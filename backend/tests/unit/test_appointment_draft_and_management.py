from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from lead_agent.api.routes import meeting_management
from lead_agent.application import meeting_email
from lead_agent.domain.operations import WorkspaceBusinessProfile
from lead_agent.domain.provider_errors import ProviderOperationError


def test_ai_booking_draft_uses_lead_thread_and_workspace_context(monkeypatch):
    workspace_id, lead_id = uuid4(), uuid4()
    profile = WorkspaceBusinessProfile(business_name="Corral", email_signature="Regards,\nCorral")
    lead = SimpleNamespace(
        id=lead_id,
        workspace_id=workspace_id,
        full_name="Harmain Mughal",
        company_name="HR Tech",
        industry="IT services",
        job_title="Owner",
        product_or_service="AI receptionist",
        message="What does the basic plan cost?",
    )
    rows = [
        SimpleNamespace(
            details={"thread_id": "other", "gmail_connection_id": "mail", "body": "Unrelated"}
        ),
        SimpleNamespace(
            details={
                "thread_id": "thread",
                "gmail_connection_id": "mail",
                "direction": "inbound",
                "body": "Could we meet tomorrow at 2?",
                "subject": "Quick meeting",
            }
        ),
    ]

    class Session:
        count = 0

        def scalars(self, _query):
            self.count += 1
            return iter(rows if self.count == 1 else [])

        def get(self, *_args):
            return SimpleNamespace(name="Corral")

    captured = {}

    class Provider:
        def draft_follow_up(self, facts):
            captured.update(facts)
            return {
                "subject": "Ignored new subject",
                "body": "Hi Harmain, let's discuss the basic plan for HR Tech.",
                "rationale": "Used the lead's plan question and business.",
            }

    monkeypatch.setattr(
        meeting_email, "operations_policy", lambda *_args: SimpleNamespace(business_profile=profile)
    )
    monkeypatch.setattr(
        meeting_email, "get_email_template", lambda *_args: {"subject": "Hello", "body": "Sample"}
    )
    monkeypatch.setattr(
        meeting_email, "resolve_workspace_reasoning_provider", lambda *_args: Provider()
    )
    subject, body, rationale = meeting_email._ai_booking_email(
        Session(),
        lead,
        {
            "thread_id": "thread",
            "gmail_connection_id": "mail",
            "subject": "Quick meeting",
            "excerpt": "Could we meet?",
        },
        "calendly",
        "https://calendly.com/corral/demo",
    )
    assert subject == "Re: Quick meeting"
    assert "https://calendly.com/corral/demo" in body
    assert body.endswith("Regards,\nCorral")
    assert "plan question" in rationale
    assert captured["lead"]["industry"] == "IT services"
    assert len(captured["gmail_conversation"]) == 1
    assert captured["gmail_conversation"][0]["body"] == "Could we meet tomorrow at 2?"


def test_ai_booking_draft_fails_closed_when_provider_unavailable(monkeypatch):
    lead = SimpleNamespace(
        id=uuid4(),
        workspace_id=uuid4(),
        full_name="Harmain Mughal",
        company_name="HR Tech",
        industry="IT services",
        job_title="Owner",
        product_or_service="AI receptionist",
        message="Hello",
    )
    session = SimpleNamespace(scalars=lambda *_args: iter([]), get=lambda *_args: None)
    monkeypatch.setattr(
        meeting_email,
        "operations_policy",
        lambda *_args: SimpleNamespace(business_profile=WorkspaceBusinessProfile()),
    )
    monkeypatch.setattr(
        meeting_email, "get_email_template", lambda *_args: {"subject": "Hi", "body": "Body"}
    )
    monkeypatch.setattr(
        meeting_email,
        "resolve_workspace_reasoning_provider",
        lambda *_args: SimpleNamespace(
            draft_follow_up=lambda _facts: (_ for _ in ()).throw(RuntimeError("offline"))
        ),
    )
    with pytest.raises(HTTPException) as error:
        meeting_email._ai_booking_email(
            session,
            lead,
            {"thread_id": "thread", "gmail_connection_id": "mail"},
            "calendly",
            "https://calendly.com/corral/demo",
        )
    assert error.value.status_code == 503


def test_cancel_does_not_mark_local_booking_without_provider_confirmation(monkeypatch):
    lead_id, workspace_id, booking_id = uuid4(), uuid4(), uuid4()
    lead = SimpleNamespace(id=lead_id, workspace_id=workspace_id, email="lead@example.com")
    row = SimpleNamespace(
        id=booking_id,
        workspace_id=workspace_id,
        lead_id=lead_id,
        status="scheduled",
        details={"provider_event_id": "event"},
    )
    managed = meeting_management.ManagedMeeting(
        id=booking_id,
        provider="google_calendar",
        title="Meeting",
        start_at=datetime.now(UTC) + timedelta(days=1),
        end_at=datetime.now(UTC) + timedelta(days=1, minutes=30),
        time_zone="UTC",
    )
    monkeypatch.setattr(meeting_management, "_booking", lambda *_args: (lead, row))
    monkeypatch.setattr(meeting_management, "_verified_meeting", lambda *_args: managed)
    monkeypatch.setattr(meeting_management, "_connection", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(
        meeting_management, "_resolve_credentials", lambda *_args: {"access_token": "test"}
    )

    class Calendar:
        def __init__(self, _token):
            pass

        def cancel_event(self, _event_id):
            raise ProviderOperationError(
                "Calendar unavailable",
                provider="google_calendar",
                code="unavailable",
                category="temporarily_unavailable",
                retryable=True,
            )

    monkeypatch.setattr(meeting_management, "GoogleCalendarClient", Calendar)
    with pytest.raises(HTTPException) as error:
        meeting_management.cancel_lead_meeting(
            lead_id,
            booking_id,
            meeting_management.CancelMeetingRequest(reason="Customer requested cancellation"),
            session=SimpleNamespace(),
            workspace=SimpleNamespace(workspace_id=workspace_id),
        )
    assert error.value.status_code == 503
    assert row.status == "scheduled"
