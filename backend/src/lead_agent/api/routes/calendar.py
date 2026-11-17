"""Workspace calendar: recorded bookings and manually scheduled activity."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission
from lead_agent.application.integration_access import (
    allowed_connection_actions,
    assert_connection_action,
)
from lead_agent.application.lead_access import assert_lead_action
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.calendar_providers import GoogleCalendarClient
from lead_agent.infrastructure.db.models import (
    LeadORM,
    PipelineEventORM,
    ProviderConnectionORM,
    WorkspaceCalendarEventORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.oauth import get_oauth_broker
from lead_agent.infrastructure.provider_lifecycle import WorkspaceAuthorizedConnectionResolver
from lead_agent.infrastructure.secrets import get_secret_store

router = APIRouter(prefix="/v1/calendar", tags=["calendar"])


class GoogleMeetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    connection_id: UUID
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)
    start_at: datetime
    end_at: datetime
    time_zone: str = Field(min_length=1, max_length=80)
    attendees: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_event(self) -> GoogleMeetRequest:
        if self.start_at.utcoffset() is None or self.end_at.utcoffset() is None:
            raise ValueError("Meeting times must include a timezone.")
        duration = self.end_at - self.start_at
        if (
            self.start_at <= datetime.now(UTC)
            or not timedelta(minutes=5) <= duration <= timedelta(hours=12)
        ):
            raise ValueError("Choose a future meeting lasting between 5 minutes and 12 hours.")
        try:
            ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Choose a valid IANA timezone.") from error
        if any(
            len(email) > 254 or "@" not in email or any(char.isspace() for char in email)
            for email in self.attendees
        ):
            raise ValueError("Enter valid attendee email addresses.")
        return self


@router.get("/google-meet/connections")
def google_meet_connections(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> list[dict[str, str]]:
    rows = session.scalars(select(ProviderConnectionORM).where(
        ProviderConnectionORM.workspace_id == workspace.workspace_id,
        ProviderConnectionORM.provider_type == "google_calendar",
        ProviderConnectionORM.is_active.is_(True), ProviderConnectionORM.status == "verified",
    ))
    return [{"id": str(row.id), "name": row.account_name} for row in rows
            if "use" in allowed_connection_actions(session, workspace, row)]


@router.post("/google-meet/events")
def create_google_meet(
    request: GoogleMeetRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> dict[str, str | None]:
    connection = session.scalar(select(ProviderConnectionORM).where(
        ProviderConnectionORM.id == request.connection_id,
        ProviderConnectionORM.workspace_id == workspace.workspace_id,
        ProviderConnectionORM.provider_type == "google_calendar",
        ProviderConnectionORM.is_active.is_(True), ProviderConnectionORM.status == "verified",
    ))
    if connection is None:
        raise HTTPException(404, "Google Calendar connection not found")
    assert_connection_action(session, workspace, connection, "use")
    resolver = WorkspaceAuthorizedConnectionResolver(get_secret_store(), get_oauth_broker())
    try:
        resolved = resolver.resolve_connection(
            session, workspace.workspace_id, connection.id, actor_id=workspace.actor_id
        )
    except ProviderOperationError as error:
        raise HTTPException(503 if error.retryable else 409, str(error)) from error
    if not resolved or not resolved.credentials.get("access_token"):
        raise HTTPException(409, "Reconnect Google Calendar and try again.")
    actor = str(workspace.user_id or workspace.actor_id)
    request_hash = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
    event_key = f"{workspace.workspace_id}:{actor}:{request.request_id}"
    event_id = hashlib.sha256(event_key.encode()).hexdigest()[:40]
    client = GoogleCalendarClient(resolved.credentials["access_token"])
    try:
        try:
            event = client.create_meet_event(
                event_id=event_id, summary=request.title, description=request.description,
                start_at=request.start_at, end_at=request.end_at, time_zone=request.time_zone,
                attendees=request.attendees, request_id=str(request.request_id),
                workspace_id=str(workspace.workspace_id), actor_id=actor, request_hash=request_hash,
            )
        except ProviderOperationError as error:
            if error.category != "conflict":
                raise
            event = client.event(event_id)
            metadata = event.get("extendedProperties", {}).get("private", {})
            if not isinstance(metadata, dict) or metadata.get("corralRequestHash") != request_hash:
                raise HTTPException(
                    409, "This request ID was used for different meeting details."
                ) from error
    except ProviderOperationError as error:
        raise HTTPException(503 if error.retryable else 409, str(error)) from error
    return {"event_id": str(event.get("id") or event_id),
            "event_url": str(event.get("htmlLink") or "") or None,
            "meet_url": str(event.get("hangoutLink") or "") or None}


class CalendarEvent(BaseModel):
    id: str
    kind: Literal["meeting", "call", "event"]
    source: Literal["manual", "google_calendar", "calendly"]
    title: str
    start_at: datetime
    end_at: datetime
    location: str | None = None
    notes: str | None = None
    lead_id: UUID | None = None
    lead_name: str | None = None
    company_name: str | None = None
    event_url: str | None = None
    editable: bool = False


class CreateCalendarEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["meeting", "call", "event"]
    title: str = Field(min_length=1, max_length=255)
    start_at: datetime
    end_at: datetime
    location: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=4000)
    lead_id: UUID | None = None

    @model_validator(mode="after")
    def validate_times(self) -> CreateCalendarEvent:
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("Start and end times must include a timezone.")
        duration = self.end_at - self.start_at
        if duration < timedelta(minutes=5) or duration > timedelta(days=7):
            raise ValueError("Events must last between 5 minutes and 7 days.")
        return self


def _manual_event(row: WorkspaceCalendarEventORM, lead: LeadORM | None) -> CalendarEvent:
    return CalendarEvent(
        id=f"manual:{row.id}", kind=row.kind, source="manual", title=row.title,
        start_at=row.start_at, end_at=row.end_at, location=row.location,
        notes=row.notes, lead_id=row.lead_id,
        lead_name=lead.full_name if lead else None,
        company_name=lead.company_name if lead else None, editable=True,
    )


@router.get("/events", response_model=list[CalendarEvent])
def list_calendar_events(
    start: date,
    end: date,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> list[CalendarEvent]:
    if end < start or end - start > timedelta(days=120):
        raise HTTPException(status_code=422, detail="Choose a calendar range of 120 days or less.")
    start_at = datetime.combine(start - timedelta(days=1), time.min, tzinfo=UTC)
    end_at = datetime.combine(end + timedelta(days=2), time.min, tzinfo=UTC)
    rows = session.execute(
        select(WorkspaceCalendarEventORM, LeadORM)
        .outerjoin(LeadORM, LeadORM.id == WorkspaceCalendarEventORM.lead_id)
        .where(
            WorkspaceCalendarEventORM.workspace_id == workspace.workspace_id,
            WorkspaceCalendarEventORM.start_at < end_at,
            WorkspaceCalendarEventORM.end_at >= start_at,
        )
    ).all()
    events = [_manual_event(row, lead) for row, lead in rows]

    bookings = session.execute(
        select(PipelineEventORM, LeadORM)
        .join(LeadORM, LeadORM.id == PipelineEventORM.lead_id)
        .where(
            PipelineEventORM.workspace_id == workspace.workspace_id,
            PipelineEventORM.stage == "meeting_scheduled",
            PipelineEventORM.status == "scheduled",
        )
    ).all()
    for row, lead in bookings:
        details = row.details if isinstance(row.details, dict) else {}
        try:
            meeting_start = datetime.fromisoformat(str(details["start_at"]).replace("Z", "+00:00"))
            meeting_end = datetime.fromisoformat(str(details["end_at"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            continue
        if meeting_start.tzinfo is None or meeting_end.tzinfo is None:
            continue
        if meeting_start >= end_at or meeting_end < start_at:
            continue
        provider = str(details.get("provider") or "")
        if provider not in {"google_calendar", "calendly"}:
            continue
        url = str(details.get("event_url") or "")
        events.append(CalendarEvent(
            id=f"booking:{row.id}", kind="meeting", source=provider,
            title=str(details.get("title") or f"Meeting with {lead.full_name or lead.company_name}"),  # noqa: E501
            start_at=meeting_start, end_at=meeting_end,
            lead_id=lead.id, lead_name=lead.full_name,
            company_name=lead.company_name,
            event_url=url if url.startswith("https://") else None,
        ))
    return sorted(events, key=lambda event: event.start_at)


@router.post("/events", response_model=CalendarEvent, status_code=201)
def create_calendar_event(
    request: CreateCalendarEvent,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> CalendarEvent:
    lead = None
    if request.lead_id:
        lead = session.scalar(select(LeadORM).where(
            LeadORM.id == request.lead_id,
            LeadORM.workspace_id == workspace.workspace_id,
        ))
        if lead is None:
            raise HTTPException(status_code=404, detail="Lead not found in this workspace.")
        assert_lead_action(session, workspace, lead, "edit")
    row = WorkspaceCalendarEventORM(
        id=uuid4(), workspace_id=workspace.workspace_id, lead_id=request.lead_id,
        kind=request.kind, title=request.title,
        start_at=request.start_at.astimezone(UTC),
        end_at=request.end_at.astimezone(UTC),
        location=request.location or None, notes=request.notes or None,
        created_by=str(workspace.actor_id), created_at=datetime.now(UTC),
    )
    session.add(row)
    session.commit()
    return _manual_event(row, lead)


@router.delete("/events/{event_id}", status_code=204)
def delete_calendar_event(
    event_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> None:
    row = session.scalar(select(WorkspaceCalendarEventORM).where(
        WorkspaceCalendarEventORM.id == event_id,
        WorkspaceCalendarEventORM.workspace_id == workspace.workspace_id,
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="Manual event not found.")
    session.delete(row)
    session.commit()
