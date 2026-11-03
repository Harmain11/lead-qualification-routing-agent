"""Manage a lead's verified provider booking without claiming unconfirmed changes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission
from lead_agent.api.routes.meetings import _resolve_credentials
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.calendar_providers import CalendlyClient, GoogleCalendarClient
from lead_agent.infrastructure.db.models import LeadORM, PipelineEventORM, ProviderConnectionORM
from lead_agent.infrastructure.db.session import get_db

router = APIRouter(prefix="/v1/leads", tags=["lead-meetings"])


class ManagedMeeting(BaseModel):
    id: UUID
    provider: Literal["google_calendar", "calendly"]
    title: str
    start_at: datetime
    end_at: datetime
    time_zone: str
    event_url: str | None = None
    reschedule_url: str | None = None
    cancel_url: str | None = None
    can_cancel_here: bool = True


class SavedMeetingStatus(BaseModel):
    status: Literal["scheduled", "cancelled"]
    start_at: datetime | None = None
    title: str
    provider: Literal["google_calendar", "calendly"] | None = None
    cancellation_reason: str | None = None


class CancelMeetingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=3, max_length=500)


class RescheduleMeetingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    start_at: datetime
    end_at: datetime
    time_zone: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_window(self) -> RescheduleMeetingRequest:
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("Include a timezone with the new meeting time.")
        if self.start_at <= datetime.now(UTC):
            raise ValueError("Choose a future meeting time.")
        if not timedelta(minutes=5) <= self.end_at - self.start_at <= timedelta(hours=3):
            raise ValueError("Meeting duration must be between 5 minutes and 3 hours.")
        try:
            ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Use a valid IANA timezone.") from error
        return self


def _booking(
    session: Session,
    lead_id: UUID,
    workspace_id: UUID,
    booking_id: UUID | None = None,
) -> tuple[LeadORM, PipelineEventORM | None]:
    lead = session.scalar(
        select(LeadORM).where(
            LeadORM.id == lead_id,
            LeadORM.workspace_id == workspace_id,
        )
    )
    if lead is None:
        raise HTTPException(404, "Lead not found.")
    query = select(PipelineEventORM).where(
        PipelineEventORM.lead_id == lead_id,
        PipelineEventORM.workspace_id == workspace_id,
        PipelineEventORM.stage == "meeting_scheduled",
        PipelineEventORM.status == "scheduled",
    )
    if booking_id is not None:
        query = query.where(PipelineEventORM.id == booking_id)
    row = session.scalar(query.order_by(PipelineEventORM.created_at.desc()).limit(1))
    return lead, row


def _connection(
    session: Session,
    row: PipelineEventORM,
    workspace: WorkspaceContext,
) -> ProviderConnectionORM:
    details = row.details if isinstance(row.details, dict) else {}
    try:
        connection_id = UUID(str(details.get("connection_id") or ""))
    except ValueError:
        raise HTTPException(
            409, "This booking has no verified calendar account. Refresh the connection."
        ) from None
    connection = session.scalar(
        select(ProviderConnectionORM).where(
            ProviderConnectionORM.id == connection_id,
            ProviderConnectionORM.workspace_id == workspace.workspace_id,
            ProviderConnectionORM.provider_type == details.get("provider"),
            ProviderConnectionORM.is_active.is_(True),
            ProviderConnectionORM.status == "verified",
        )
    )
    if connection is None:
        raise HTTPException(409, "Reconnect the calendar account used for this meeting.")
    return connection


def _datetime(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else None


def _safe_calendly_link(value: object) -> str | None:
    link = str(value or "")
    return link if link.startswith("https://calendly.com/") else None


def _record_cancelled(session: Session, row: PipelineEventORM, *, reason: str) -> None:
    details = row.details if isinstance(row.details, dict) else {}
    row.status = "cancelled"
    row.details = {
        **details,
        "cancelled_at": datetime.now(UTC).isoformat(),
        "cancellation_reason": reason,
    }
    for offer in session.scalars(
        select(PipelineEventORM).where(
            PipelineEventORM.workspace_id == row.workspace_id,
            PipelineEventORM.lead_id == row.lead_id,
            PipelineEventORM.stage == "meeting_email_offer",
            PipelineEventORM.status == "booked",
        )
    ):
        offer_details = offer.details if isinstance(offer.details, dict) else {}
        if str(offer_details.get("provider_event_id") or "") == str(
            details.get("provider_event_id") or ""
        ):
            offer.status = "superseded"
            offer.details = {**offer_details, "closed_reason": "meeting_cancelled"}
    session.commit()


def _verified_meeting(
    session: Session,
    lead: LeadORM,
    row: PipelineEventORM,
    workspace: WorkspaceContext,
) -> ManagedMeeting | None:
    details = row.details if isinstance(row.details, dict) else {}
    provider = str(details.get("provider") or "")
    event_id = str(details.get("provider_event_id") or "")
    if provider not in {"google_calendar", "calendly"} or not event_id:
        raise HTTPException(
            409, "This booking has no manageable provider event. Check the connected calendar."
        )
    connection = _connection(session, row, workspace)
    try:
        token = _resolve_credentials(session, workspace, connection)["access_token"]
        if provider == "google_calendar":
            event = GoogleCalendarClient(token).event(event_id)
            if event.get("status") == "cancelled":
                _record_cancelled(session, row, reason="Cancelled in Google Calendar")
                return None
            attendees = event.get("attendees") or []
            private = (event.get("extendedProperties") or {}).get("private") or {}
            lead_matches = private.get("corralLeadId") == str(lead.id) or any(
                str(item.get("email") or "").casefold() == str(lead.email or "").casefold()
                for item in attendees
                if isinstance(item, dict)
            )
            if (
                event.get("status") != "confirmed"
                or event.get("id") != event_id
                or not lead_matches
            ):
                raise HTTPException(
                    409, "Calendar event does not match this lead. No change was made."
                )
            start_at = _datetime((event.get("start") or {}).get("dateTime"))
            end_at = _datetime((event.get("end") or {}).get("dateTime"))
            reschedule_url = None
            cancel_url = None
            can_cancel_here = True
        else:
            if "/invitees/" in str(details.get("provider_event_uri") or ""):
                raise HTTPException(409, "Open this meeting in Calendly to manage the invitee.")
            client = CalendlyClient(token)
            event = client.scheduled_event(event_id)
            if event.get("status") == "canceled":
                _record_cancelled(session, row, reason="Cancelled or rescheduled in Calendly")
                return None
            invitees = client.event_invitees(event_id, str(lead.email or ""))
            matching = [
                item
                for item in invitees
                if str(item.get("email") or "").casefold() == str(lead.email or "").casefold()
            ]
            if (
                event.get("status") != "active"
                or event.get("uri", "").rsplit("/", 1)[-1] != event_id
                or len(matching) != 1
            ):
                raise HTTPException(409, "Calendly could not verify this lead’s active invitee.")
            start_at = _datetime(event.get("start_time"))
            end_at = _datetime(event.get("end_time"))
            reschedule_url = _safe_calendly_link(matching[0].get("reschedule_url"))
            cancel_url = _safe_calendly_link(matching[0].get("cancel_url"))
            can_cancel_here = len(client.event_invitees(event_id)) == 1
        if start_at is None or end_at is None:
            raise HTTPException(409, "The calendar returned no verified meeting time.")
        if (
            details.get("start_at") != start_at.astimezone(UTC).isoformat()
            or details.get("end_at") != end_at.astimezone(UTC).isoformat()
        ):
            row.details = {
                **details,
                "start_at": start_at.astimezone(UTC).isoformat(),
                "end_at": end_at.astimezone(UTC).isoformat(),
            }
            session.commit()
        return ManagedMeeting(
            id=row.id,
            provider=provider,
            title=str(details.get("title") or "Meeting"),
            start_at=start_at,
            end_at=end_at,
            time_zone=str(details.get("time_zone") or "UTC"),
            event_url=str(details.get("event_url") or "") or None,
            reschedule_url=reschedule_url,
            cancel_url=cancel_url,
            can_cancel_here=can_cancel_here,
        )
    except ProviderOperationError as error:
        raise HTTPException(
            503 if error.retryable else 409, f"Calendar verification failed: {error}"
        ) from error


@router.get("/{lead_id}/meeting-management", response_model=ManagedMeeting | None)
def get_meeting_management(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> ManagedMeeting | None:
    lead, row = _booking(session, lead_id, workspace.workspace_id)
    return _verified_meeting(session, lead, row, workspace) if row is not None else None


@router.get("/{lead_id}/meeting-status", response_model=SavedMeetingStatus | None)
def get_saved_meeting_status(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> SavedMeetingStatus | None:
    lead = session.scalar(
        select(LeadORM).where(
            LeadORM.id == lead_id,
            LeadORM.workspace_id == workspace.workspace_id,
        )
    )
    if lead is None:
        raise HTTPException(404, "Lead not found.")
    row = session.scalar(
        select(PipelineEventORM)
        .where(
            PipelineEventORM.lead_id == lead_id,
            PipelineEventORM.workspace_id == workspace.workspace_id,
            PipelineEventORM.stage == "meeting_scheduled",
            PipelineEventORM.status.in_(("scheduled", "cancelled")),
        )
        .order_by(PipelineEventORM.created_at.desc())
        .limit(1)
    )
    if row is None:
        return None
    details = row.details if isinstance(row.details, dict) else {}
    provider = details.get("provider")
    return SavedMeetingStatus(
        status=row.status,  # type: ignore[arg-type]
        start_at=_datetime(details.get("start_at")),
        title=str(details.get("title") or "Meeting"),
        provider=provider if provider in {"google_calendar", "calendly"} else None,
        cancellation_reason=(
            str(details.get("cancellation_reason") or "").strip() or None
        ),
    )


@router.post("/{lead_id}/meetings/{booking_id}/cancel", response_model=dict[str, str])
def cancel_lead_meeting(
    lead_id: UUID,
    booking_id: UUID,
    request: CancelMeetingRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> dict[str, str]:
    lead, row = _booking(session, lead_id, workspace.workspace_id, booking_id)
    if row is None:
        raise HTTPException(409, "This meeting is no longer active. Refresh the lead.")
    managed = _verified_meeting(session, lead, row, workspace)
    if managed is None:
        raise HTTPException(409, "The calendar already cancelled this meeting. Refresh the lead.")
    if not managed.can_cancel_here:
        raise HTTPException(
            409,
            "This is a group Calendly event. Use the invitee’s cancellation link "
            "to avoid cancelling other attendees.",
        )
    connection = _connection(session, row, workspace)
    try:
        token = _resolve_credentials(session, workspace, connection)["access_token"]
        event_id = str(row.details["provider_event_id"])
        if managed.provider == "google_calendar":
            GoogleCalendarClient(token).cancel_event(event_id)
        else:
            CalendlyClient(token).cancel_event(event_id, request.reason)
    except ProviderOperationError as error:
        raise HTTPException(
            503 if error.retryable else 409, f"Calendar cancellation was not confirmed: {error}"
        ) from error
    _record_cancelled(session, row, reason=request.reason)
    return {"status": "cancelled", "message": "The calendar confirmed the meeting cancellation."}


@router.patch("/{lead_id}/meetings/{booking_id}/reschedule", response_model=ManagedMeeting)
def reschedule_lead_meeting(
    lead_id: UUID,
    booking_id: UUID,
    request: RescheduleMeetingRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> ManagedMeeting:
    lead, row = _booking(session, lead_id, workspace.workspace_id, booking_id)
    if row is None:
        raise HTTPException(409, "This meeting is no longer active. Refresh the lead.")
    managed = _verified_meeting(session, lead, row, workspace)
    if managed is None:
        raise HTTPException(409, "This meeting was cancelled in the calendar.")
    if managed.provider != "google_calendar":
        raise HTTPException(409, "Use the invitee’s Calendly reschedule link for this meeting.")
    connection = _connection(session, row, workspace)
    try:
        token = _resolve_credentials(session, workspace, connection)["access_token"]
        event = GoogleCalendarClient(token).reschedule_event(
            str(row.details["provider_event_id"]),
            start_at=request.start_at,
            end_at=request.end_at,
            time_zone=request.time_zone,
        )
    except ProviderOperationError as error:
        raise HTTPException(
            503 if error.retryable else 409, f"Calendar rescheduling was not confirmed: {error}"
        ) from error
    start_at = _datetime((event.get("start") or {}).get("dateTime"))
    end_at = _datetime((event.get("end") or {}).get("dateTime"))
    if start_at != request.start_at or end_at != request.end_at:
        raise HTTPException(
            503, "Google Calendar did not confirm the new time. Refresh before retrying."
        )
    details = row.details if isinstance(row.details, dict) else {}
    row.details = {
        **details,
        "start_at": start_at.astimezone(UTC).isoformat(),
        "end_at": end_at.astimezone(UTC).isoformat(),
        "time_zone": request.time_zone,
        "rescheduled_at": datetime.now(UTC).isoformat(),
    }
    session.commit()
    return ManagedMeeting(
        **{
            **managed.model_dump(),
            "start_at": start_at,
            "end_at": end_at,
            "time_zone": request.time_zone,
        }
    )
