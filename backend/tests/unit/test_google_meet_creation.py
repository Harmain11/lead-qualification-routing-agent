from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from lead_agent.api.routes import calendar
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.calendar_providers import GoogleCalendarClient


def test_google_meet_request_enables_conference_and_invitations() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = dict(request.url.params)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "test"})

    now = datetime.now(UTC) + timedelta(days=1)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = GoogleCalendarClient("test", client=client)
        provider.create_meet_event(
            event_id="event", summary="Review", description="Agenda", start_at=now,
            end_at=now + timedelta(hours=1), time_zone="UTC", attendees=["a@example.test"],
            request_id="request", workspace_id="workspace", actor_id="actor", request_hash="hash",
        )
    assert captured["query"] == {"sendUpdates": "all", "conferenceDataVersion": "1"}
    assert captured["body"]["conferenceData"]["createRequest"]["requestId"] == "request"
    assert captured["body"]["attendees"] == [{"email": "a@example.test"}]


def test_google_meet_retries_same_event_and_checks_connection_access(monkeypatch) -> None:
    actor = uuid4()
    workspace = WorkspaceContext(workspace_id=uuid4(), actor_id=str(actor), user_id=actor)
    connection = SimpleNamespace(id=uuid4())
    session = SimpleNamespace(scalar=lambda _: connection)
    start = datetime.now(UTC) + timedelta(days=1)
    request = calendar.GoogleMeetRequest(
        request_id=uuid4(), connection_id=connection.id, title="Review", start_at=start,
        end_at=start + timedelta(hours=1), time_zone="UTC", attendees=["a@example.test"],
    )
    saved = {}

    class Resolver:
        def __init__(self, *_args):
            pass

        def resolve_connection(self, *_args, **_kwargs):
            return SimpleNamespace(credentials={"access_token": "test"})

    class Provider:
        def __init__(self, _token):
            pass

        def create_meet_event(self, **kwargs):
            if saved:
                raise ProviderOperationError("exists", provider="google_calendar", code="conflict",
                                             category="conflict", retryable=False)
            saved.update(kwargs)
            return {"id": kwargs["event_id"], "hangoutLink": "https://meet.google.com/test"}

        def event(self, _event_id):
            return {"id": saved["event_id"], "hangoutLink": "https://meet.google.com/test",
                    "extendedProperties": {"private": {"corralRequestHash": saved["request_hash"]}}}

    monkeypatch.setattr(calendar, "get_secret_store", lambda: None)
    monkeypatch.setattr(calendar, "get_oauth_broker", lambda: None)
    monkeypatch.setattr(calendar, "WorkspaceAuthorizedConnectionResolver", Resolver)
    monkeypatch.setattr(calendar, "GoogleCalendarClient", Provider)
    monkeypatch.setattr(calendar, "assert_connection_action", lambda *_: None)
    first = calendar.create_google_meet(request, session, workspace)
    second = calendar.create_google_meet(request, session, workspace)
    assert first == second
    changed = request.model_copy(update={"title": "Different"})
    with pytest.raises(HTTPException) as conflict:
        calendar.create_google_meet(changed, session, workspace)
    assert conflict.value.status_code == 409

    def deny(*_args):
        raise HTTPException(403, "forbidden")

    monkeypatch.setattr(calendar, "assert_connection_action", deny)
    with pytest.raises(HTTPException) as denied:
        calendar.create_google_meet(request, session, workspace)
    assert denied.value.status_code == 403
