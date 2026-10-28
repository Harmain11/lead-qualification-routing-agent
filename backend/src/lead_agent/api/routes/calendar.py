"""Workspace calendar: recorded bookings and manually scheduled activity."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import LeadORM, PipelineEventORM, WorkspaceCalendarEventORM
from lead_agent.infrastructure.db.session import get_db

router = APIRouter(prefix="/v1/calendar", tags=["calendar"])


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
            title=str(details.get("title") or f"Meeting with {lead.full_name or lead.company_name}"),
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
