from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission
from lead_agent.application.governance import assert_outreach_allowed
from lead_agent.application.meetings import (
    appointment_event_exists,
    find_meeting_request,
    meeting_description,
    meeting_title,
    recommend_calendly_slots,
    recommend_google_calendar_slots,
    requested_meeting_date,
)
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.calendar_providers import (
    CalendlyClient,
    GoogleCalendarClient,
)
from lead_agent.infrastructure.gmail import GMAIL_SEND_SCOPE, GmailOutboundProvider
from lead_agent.infrastructure.db.models import LeadORM, PipelineEventORM, ProviderConnectionORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.oauth import get_oauth_broker
from lead_agent.infrastructure.provider_lifecycle import WorkspaceAuthorizedConnectionResolver
from lead_agent.infrastructure.secrets import get_secret_store

router = APIRouter(prefix="/v1/leads", tags=["lead-meetings"])
MeetingProvider = Literal["google_calendar", "calendly"]
MeetingOfferStatus = Literal[
    "draft",
    "sending",
    "awaiting_confirmation",
    "awaiting_booking",
    "booking",
    "booked",
    "needs_attention",
    "superseded",
]


class MeetingSuggestion(BaseModel):
    provider: MeetingProvider
    connection_id: UUID
    account_name: str
    event_type_uri: str | None = None
    event_type_name: str | None = None
    start_at: datetime
    end_at: datetime
    time_zone: str
    source_message_id: str
    source_gmail_connection_id: UUID | None = None


class MeetingIntegrationState(BaseModel):
    provider: MeetingProvider
    connected: bool
    account_name: str | None = None
    message: str | None = None
    booking_ready: bool = False
    booking_url: str | None = None
    booking_event_title: str | None = None


class MeetingCalendlyEventType(BaseModel):
    event_type_uri: str
    event_type_name: str
    status: Literal["available", "no_times", "calendar_unavailable"]
    scheduling_url: str | None = None


class ScheduledMeetingSummary(BaseModel):
    provider: MeetingProvider
    title: str
    start_at: datetime
    end_at: datetime
    time_zone: str
    event_url: str | None = None


class MeetingProviderError(BaseModel):
    provider: MeetingProvider
    message: str
    retryable: bool = False
    code: str


class MeetingRecommendationsResponse(BaseModel):
    lead_id: UUID
    detected: bool
    message: str
    source_message_id: str | None = None
    offer_source_message_id: str | None = None
    source_subject: str | None = None
    source_excerpt: str | None = None
    integrations: list[MeetingIntegrationState]
    suggestions: list[MeetingSuggestion]
    calendly_event_types: list[MeetingCalendlyEventType] = Field(default_factory=list)
    provider_errors: list[MeetingProviderError]
    offer_status: MeetingOfferStatus | None = None
    offer_booking_error: str | None = None
    offer_calendar_provider: MeetingProvider | None = None
    offer_has_confirmed_selection: bool = False
    offer_email_subject: str | None = None
    offer_email_body: str | None = None
    offer_booking_url: str | None = None
    offer_gmail_connection_id: str | None = None
    scheduled_meeting: ScheduledMeetingSummary | None = None


class ConfirmMeetingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: MeetingProvider
    connection_id: UUID
    source_message_id: str = Field(min_length=1, max_length=512)
    source_gmail_connection_id: UUID | None = None
    start_at: datetime
    end_at: datetime
    time_zone: str = Field(min_length=1, max_length=80)
    event_type_uri: str | None = Field(default=None, max_length=1000)
    event_type_name: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_times(self) -> ConfirmMeetingRequest:
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("Meeting times must include a timezone.")
        duration = self.end_at - self.start_at
        if duration < timedelta(minutes=5) or duration > timedelta(hours=3):
            raise ValueError("Meeting duration must be between 5 minutes and 3 hours.")
        if self.start_at <= datetime.now(UTC):
            raise ValueError("The selected meeting time must be in the future.")
        try:
            ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Meeting timezone must be a valid IANA timezone.") from error
        return self


class MeetingBookingDraftEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_message_id: str = Field(min_length=1, max_length=512)
    source_gmail_connection_id: UUID | None = None
    subject: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=10, max_length=5000)


class MeetingBookingDraftSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_message_id: str = Field(min_length=1, max_length=512)
    source_gmail_connection_id: UUID | None = None


class ConfirmMeetingResponse(BaseModel):
    lead_id: UUID
    provider: MeetingProvider
    provider_event_id: str
    provider_event_uri: str | None = None
    event_url: str | None = None
    title: str
    start_at: datetime
    end_at: datetime
    time_zone: str
    status: Literal["scheduled"]


def _lead_or_404(
    session: Session, lead_id: UUID, workspace_id: UUID
) -> LeadORM:
    lead = session.scalar(
        select(LeadORM).where(
            LeadORM.id == lead_id,
            LeadORM.workspace_id == workspace_id,
        )
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found.")
    return lead


def _offer_for_source(
    session: Session,
    lead_id: UUID,
    workspace_id: UUID,
    source_message_id: str,
    source_gmail_connection_id: UUID | None = None,
) -> PipelineEventORM | None:
    rows = session.scalars(
        select(PipelineEventORM)
        .where(
            PipelineEventORM.lead_id == lead_id,
            PipelineEventORM.workspace_id == workspace_id,
            PipelineEventORM.stage == "meeting_email_offer",
        )
        .order_by(PipelineEventORM.created_at.desc())
        .limit(50)
    )
    return next(
        (
            row
            for row in rows
            if isinstance(row.details, dict)
            and row.details.get("source_message_id") == source_message_id
            and (
                source_gmail_connection_id is None
                or str(
                    row.details.get("source_gmail_connection_id")
                    or row.details.get("gmail_connection_id")
                    or ""
                )
                == str(source_gmail_connection_id)
            )
        ),
        None,
    )


def _workspace_calendar_connections(
    session: Session, workspace_id: UUID
) -> dict[str, ProviderConnectionORM]:
    rows = list(
        session.scalars(
            select(ProviderConnectionORM)
            .where(
                ProviderConnectionORM.workspace_id == workspace_id,
                ProviderConnectionORM.provider_type.in_({"google_calendar", "calendly"}),
                ProviderConnectionORM.is_active.is_(True),
                ProviderConnectionORM.status == "verified",
            )
            .order_by(ProviderConnectionORM.updated_at.desc())
        )
    )
    selected: dict[str, ProviderConnectionORM] = {}
    for row in rows:
        selected.setdefault(row.provider_type, row)
    return selected


def _integration_states(
    connections: dict[str, ProviderConnectionORM],
) -> list[MeetingIntegrationState]:
    states: list[MeetingIntegrationState] = []
    for provider in ("google_calendar", "calendly"):
        connection = connections.get(provider)
        metadata = (
            connection.connection_metadata
            if connection is not None and isinstance(connection.connection_metadata, dict)
            else {}
        )
        booking_url = (
            str(metadata.get("google_appointment_schedule_url") or "").strip()
            if provider == "google_calendar"
            else ""
        )
        event_title = (
            str(metadata.get("google_appointment_schedule_title") or "").strip()
            if provider == "google_calendar"
            else ""
        )
        booking_ready = bool(connection and booking_url and event_title) if provider == "google_calendar" else False
        states.append(
            MeetingIntegrationState(
                provider=provider,  # type: ignore[arg-type]
                connected=connection is not None,
                account_name=connection.account_name if connection else None,
                message=(
                    "Add a Google Appointment Schedule link in Integrations."
                    if connection and provider == "google_calendar" and not booking_ready
                    else None
                    if connection
                    else "Connect this workspace’s account in Integrations."
                ),
                booking_ready=booking_ready,
                booking_url=booking_url if booking_ready else None,
                booking_event_title=event_title if booking_ready else None,
            )
        )
    return states


def _resolve_credentials(
    session: Session, workspace: WorkspaceContext, connection: ProviderConnectionORM
) -> dict[str, str]:
    resolver = WorkspaceAuthorizedConnectionResolver(
        get_secret_store(), get_oauth_broker()
    )
    resolved = resolver.resolve_connection(
        session,
        workspace.workspace_id,
        connection.id,
        actor_id=workspace.actor_id,
    )
    if resolved is None:
        raise ProviderOperationError(
            "The selected calendar connection is no longer active.",
            provider=connection.provider_type,
            code=f"{connection.provider_type}_connection_inactive",
            category="configuration",
            retryable=False,
        )
    if not resolved.credentials.get("access_token"):
        raise ProviderOperationError(
            (
                "The selected calendar connection has no usable access token. "
                "Reconnect it in Integrations."
            ),
            provider=connection.provider_type,
            code=f"{connection.provider_type}_access_token_unavailable",
            category="invalid_credentials",
            retryable=False,
        )
    return resolved.credentials


_CALENDLY_LOCATION_KINDS = {
    "ask_invitee",
    "custom",
    "google_conference",
    "gotomeeting_conference",
    "inbound_call",
    "microsoft_teams_conference",
    "outbound_call",
    "physical",
    "webex_conference",
    "zoom_conference",
}


def _calendly_invitee_location(
    event_type: dict[str, Any],
) -> dict[str, str] | None:
    if event_type.get("pooling_type") == "round_robin":
        return None
    locations = event_type.get("locations")
    if locations is None or locations == []:
        return None
    if not isinstance(locations, list) or any(
        not isinstance(location, dict) for location in locations
    ):
        raise HTTPException(
            status_code=409,
            detail="Calendly returned an invalid location configuration for this event type.",
        )
    if len(locations) != 1:
        raise HTTPException(
            status_code=409,
            detail=(
                "This Calendly event type has multiple location choices. "
                "Automatic booking requires one configured location."
            ),
        )

    configuration = locations[0]
    kind = str(configuration.get("kind") or "").strip()
    if kind not in _CALENDLY_LOCATION_KINDS:
        raise HTTPException(
            status_code=409,
            detail="This Calendly event type has no supported configured location.",
        )
    if kind == "ask_invitee":
        raise HTTPException(
            status_code=409,
            detail=(
                "This Calendly event type requires the invitee to provide a location. "
                "Use a fixed-location event type or collect the location before booking."
            ),
        )

    location = {"kind": kind}
    if kind in {"custom", "physical", "outbound_call"}:
        value = configuration.get("location") or configuration.get("phone_number")
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(
                status_code=409,
                detail=(
                    "This Calendly event type is missing its configured meeting "
                    "location. Update the event type in Calendly before booking."
                ),
            )
        location["location"] = value.strip()
    return location


@router.get(
    "/{lead_id}/meeting-recommendations",
    response_model=MeetingRecommendationsResponse,
)
def get_meeting_recommendations(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> MeetingRecommendationsResponse:
    lead = _lead_or_404(session, lead_id, workspace.workspace_id)
    connections = _workspace_calendar_connections(session, workspace.workspace_id)
    source = find_meeting_request(session, lead, workspace.workspace_id)
    if source is None:
        any_messages = session.scalar(
            select(PipelineEventORM.id)
            .where(
                PipelineEventORM.lead_id == lead_id,
                PipelineEventORM.workspace_id == workspace.workspace_id,
                PipelineEventORM.stage == "gmail_message",
            )
            .limit(1)
        )
        return MeetingRecommendationsResponse(
            lead_id=lead_id,
            detected=False,
            message=(
                "No meeting request found in the synced Gmail conversation."
                if any_messages
                else "Sync the Gmail conversation to check it for a meeting request."
            ),
            integrations=_integration_states(connections),
            suggestions=[],
            provider_errors=[],
        )

    offer_event = next(
        (
            event
            for event in session.scalars(
                select(PipelineEventORM)
                .where(
                    PipelineEventORM.lead_id == lead_id,
                    PipelineEventORM.workspace_id == workspace.workspace_id,
                    PipelineEventORM.stage == "meeting_email_offer",
                )
                .order_by(PipelineEventORM.created_at.desc())
                .limit(50)
            )
            if isinstance(event.details, dict)
            and event.details.get("thread_id") == source["thread_id"]
            and str(event.details.get("gmail_connection_id") or "")
            == source.get("gmail_connection_id", "")
        ),
        None,
    )
    automation_message = (
        "Date-only requests get open times for that day. If the lead gives an exact "
        "date and time, Corral books it only when the calendar confirms it is free; "
        "otherwise, Corral prepares a booking-link email draft for review."
    )
    requested_date = requested_meeting_date(source.get("excerpt", ""))
    offer_details = (
        offer_event.details
        if offer_event is not None and isinstance(offer_event.details, dict)
        else {}
    )
    offer_status = offer_event.status if offer_event is not None else None
    offer_booking_error = (
        str(offer_details["booking_error"])
        if offer_details.get("booking_error")
        else None
    )
    options = offer_details.get("options")
    selected_option = offer_details.get("selected_option")
    offer_has_confirmed_selection = (
        not offer_details.get("lead_requested_other_times")
        and isinstance(options, list)
        and isinstance(selected_option, int)
        and not isinstance(selected_option, bool)
        and 1 <= selected_option <= len(options)
        and bool(offer_details.get("reply_message_id"))
    )
    raw_offer_provider = offer_details.get("calendar_provider")
    offer_calendar_provider = (
        raw_offer_provider
        if raw_offer_provider in {"google_calendar", "calendly"}
        else None
    )
    if offer_event is not None:
        if offer_event.status == "draft":
            automation_message = (
                "A booking-link email draft is ready for review. The lead can choose a "
                "time directly on the connected provider’s booking page."
            )
        elif offer_event.status == "awaiting_booking":
            automation_message = (
                "The booking link was sent. Corral is checking the selected calendar "
                "for a confirmed appointment."
            )
        elif offer_event.status == "awaiting_confirmation":
            automation_message = (
                "Available times were emailed to the lead. Corral will book after "
                "they clearly reply with an option number."
            )
        elif offer_event.status == "booked":
            if offer_calendar_provider == "calendly":
                automation_message = (
                    "Calendly booking recorded. Calendly’s event-type notification "
                    "settings control whether the invitee receives an email."
                )
            else:
                automation_message = (
                    "The meeting is booked after Corral confirmed an exact requested "
                    "time was free, the lead selected an emailed option, or an admin confirmed it."
                )
        elif offer_event.status == "needs_attention":
            if offer_details.get("lead_requested_other_times"):
                automation_message = (
                    "The lead asked for different times. Review their reply and send "
                    "new options or follow up manually."
                )
            elif offer_details.get("error_code") in {
                "gmail_send_scope_missing",
                "gmail_insufficient_scope",
                "gmail_send_forbidden",
            }:
                automation_message = (
                    "Reconnect Gmail and grant permission to send email so Corral "
                    "can deliver meeting options."
                )
            elif offer_booking_error:
                automation_message = (
                    "Corral could not verify the booking. Check the calendar for an "
                    "existing event before trying again; a booking may have succeeded "
                    "without being recorded here."
                )
            elif offer_has_confirmed_selection:
                automation_message = (
                    "The lead’s numbered choice was saved, but Corral has not confirmed "
                    "the booking. Use “Retry confirmed booking” below to retry it safely."
                )
            else:
                automation_message = (
                    "Meeting email scheduling needs attention. Check the connected "
                    "Gmail and calendar accounts."
                )
        elif offer_event.status == "superseded":
            automation_message = (
                "The lead sent a newer meeting request; Corral will use the latest request."
            )

    suggestions: list[MeetingSuggestion] = []
    calendly_event_types: list[dict[str, str]] = []
    provider_errors: list[MeetingProviderError] = []
    for provider in ("google_calendar", "calendly"):
        connection = connections.get(provider)
        if connection is None:
            continue
        try:
            credentials = _resolve_credentials(session, workspace, connection)
            if provider == "google_calendar":
                slots = recommend_google_calendar_slots(
                    GoogleCalendarClient(credentials["access_token"]),
                    connection_id=str(connection.id),
                    account_name=connection.account_name,
                    source_message_id=source["message_id"],
                    source_gmail_connection_id=source.get("gmail_connection_id"),
                    requested_date=requested_date,
                )
            else:
                slots = recommend_calendly_slots(
                    CalendlyClient(credentials["access_token"]),
                    connection_id=str(connection.id),
                    source_message_id=source["message_id"],
                    source_gmail_connection_id=source.get("gmail_connection_id"),
                    requested_date=requested_date,
                    event_type_statuses=calendly_event_types,
                )
                for slot in slots:
                    slot["account_name"] = connection.account_name
            for slot in slots:
                if provider == "google_calendar":
                    slot["account_name"] = connection.account_name
                suggestions.append(MeetingSuggestion.model_validate(slot))
        except ProviderOperationError as error:
            provider_errors.append(
                MeetingProviderError(
                    provider=provider,  # type: ignore[arg-type]
                    message=str(error),
                    retryable=error.retryable,
                    code=error.code,
                )
            )
    suggestions.sort(key=lambda row: row.start_at)
    integration_states = _integration_states(connections)
    calendly_booking_type = next(
        (item for item in calendly_event_types if item.get("scheduling_url")),
        None,
    )
    if calendly_booking_type is not None:
        integration_states = [
            state.model_copy(
                update={
                    "booking_ready": True,
                    "booking_url": str(calendly_booking_type["scheduling_url"]),
                    "message": None,
                }
            )
            if state.provider == "calendly"
            else state
            for state in integration_states
        ]

    scheduled_summary: ScheduledMeetingSummary | None = None
    for event in session.scalars(
        select(PipelineEventORM).where(
            PipelineEventORM.lead_id == lead_id,
            PipelineEventORM.workspace_id == workspace.workspace_id,
            PipelineEventORM.stage == "meeting_scheduled",
        )
    ):
        details = event.details if isinstance(event.details, dict) else {}
        if (
            details.get("source_message_id") != source["message_id"]
            or str(details.get("source_gmail_connection_id") or "")
            != str(source.get("gmail_connection_id") or "")
        ):
            continue
        try:
            scheduled_summary = ScheduledMeetingSummary(
                provider=details["provider"],
                title=str(details.get("title") or f"Meeting with {lead.full_name}"),
                start_at=datetime.fromisoformat(str(details["start_at"])),
                end_at=datetime.fromisoformat(str(details["end_at"])),
                time_zone=str(details.get("time_zone") or "UTC"),
                event_url=str(details.get("event_url") or "") or None,
            )
        except (KeyError, TypeError, ValueError):
            scheduled_summary = None
        if scheduled_summary is not None:
            break

    return MeetingRecommendationsResponse(
        lead_id=lead_id,
        detected=True,
        message=automation_message,
        source_message_id=source["message_id"],
        offer_source_message_id=(
            str(offer_details["source_message_id"])
            if offer_details.get("source_message_id")
            else None
        ),
        source_subject=source["subject"] or None,
        source_excerpt=source["excerpt"] or None,
        integrations=integration_states,
        suggestions=suggestions,
        calendly_event_types=[
            MeetingCalendlyEventType.model_validate(event_type)
            for event_type in calendly_event_types
        ],
        provider_errors=provider_errors,
        offer_status=offer_status,
        offer_booking_error=offer_booking_error,
        offer_calendar_provider=offer_calendar_provider,
        offer_has_confirmed_selection=offer_has_confirmed_selection,
        offer_email_subject=str(offer_details.get("email_subject") or "") or None,
        offer_email_body=str(offer_details.get("email_body") or "") or None,
        offer_booking_url=str(offer_details.get("booking_url") or "") or None,
        offer_gmail_connection_id=str(offer_details.get("gmail_connection_id") or "") or None,
        scheduled_meeting=scheduled_summary,
    )


@router.patch("/{lead_id}/meeting-booking-draft")
def update_meeting_booking_draft(
    lead_id: UUID,
    request: MeetingBookingDraftEdit,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> dict[str, str]:
    _lead_or_404(session, lead_id, workspace.workspace_id)
    offer = _offer_for_source(
        session,
        lead_id,
        workspace.workspace_id,
        request.source_message_id,
        request.source_gmail_connection_id,
    )
    if offer is None or offer.status != "draft":
        raise HTTPException(status_code=409, detail="There is no editable booking email draft for this request.")
    details = dict(offer.details) if isinstance(offer.details, dict) else {}
    booking_url = str(details.get("booking_url") or "")
    if not booking_url or booking_url not in request.body:
        raise HTTPException(
            status_code=422,
            detail="Keep the booking link in the email so the lead can choose a time.",
        )
    details["email_subject"] = request.subject
    details["email_body"] = request.body
    details["draft_updated_at"] = datetime.now(UTC).isoformat()
    offer.details = details
    session.commit()
    return {"status": "draft", "subject": request.subject, "body": request.body}


@router.post("/{lead_id}/meeting-booking-draft/send")
def send_meeting_booking_draft(
    lead_id: UUID,
    request: MeetingBookingDraftSendRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> dict[str, str]:
    lead = _lead_or_404(session, lead_id, workspace.workspace_id)
    offer = _offer_for_source(
        session,
        lead_id,
        workspace.workspace_id,
        request.source_message_id,
        request.source_gmail_connection_id,
    )
    if offer is None or offer.status != "draft":
        raise HTTPException(status_code=409, detail="There is no unsent booking email draft.")
    details = dict(offer.details) if isinstance(offer.details, dict) else {}
    subject = str(details.get("email_subject") or "").strip()
    body = str(details.get("email_body") or "")
    booking_url = str(details.get("booking_url") or "")
    if not subject or not body or not booking_url or booking_url not in body:
        raise HTTPException(status_code=409, detail="Save a complete draft with its booking link before sending.")
    latest = find_meeting_request(
        session,
        lead,
        workspace.workspace_id,
        thread_id=str(details.get("thread_id") or "") or None,
        gmail_connection_id=str(details.get("gmail_connection_id") or "") or None,
    )
    if latest is None or latest.get("message_id") != request.source_message_id:
        raise HTTPException(status_code=409, detail="The conversation changed. Refresh meeting recommendations before sending.")
    same_request_meeting = any(
        isinstance(event.details, dict)
        and event.details.get("source_message_id") == request.source_message_id
        and str(event.details.get("source_gmail_connection_id") or "")
        == str(request.source_gmail_connection_id or details.get("gmail_connection_id") or "")
        for event in session.scalars(
            select(PipelineEventORM).where(
                PipelineEventORM.lead_id == lead_id,
                PipelineEventORM.workspace_id == workspace.workspace_id,
                PipelineEventORM.stage == "meeting_scheduled",
            )
        )
    )
    if same_request_meeting:
        raise HTTPException(status_code=409, detail="A meeting is already recorded for this request.")
    try:
        gmail_connection_id = UUID(str(details["gmail_connection_id"]))
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=409, detail="The Gmail connection for this draft is unavailable.") from error
    gmail_connection = session.scalar(
        select(ProviderConnectionORM).where(
            ProviderConnectionORM.id == gmail_connection_id,
            ProviderConnectionORM.workspace_id == workspace.workspace_id,
            ProviderConnectionORM.provider_type == "gmail",
            ProviderConnectionORM.is_active.is_(True),
            ProviderConnectionORM.status == "verified",
        )
    )
    if gmail_connection is None:
        raise HTTPException(status_code=409, detail="Reconnect Gmail before sending this booking email.")
    if GMAIL_SEND_SCOPE not in set(gmail_connection.scopes or []):
        raise HTTPException(status_code=409, detail="Reconnect Gmail and grant permission to send email.")
    try:
        assert_outreach_allowed(session, workspace.workspace_id, lead.email)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    try:
        credentials = _resolve_credentials(session, workspace, gmail_connection)
        metadata = (
            gmail_connection.connection_metadata
            if isinstance(gmail_connection.connection_metadata, dict)
            else {}
        )
        mailbox = str(metadata.get("gmail_email") or gmail_connection.account_name)
        offer.status = "sending"
        session.commit()
        message_id = GmailOutboundProvider(
            gmail_connection.id, credentials["access_token"], mailbox
        ).send_email(
            recipient=lead.email,
            subject=subject,
            body=body,
            thread_id=str(details.get("thread_id") or "") or None,
            in_reply_to=str(details.get("source_rfc_message_id") or "") or None,
        )
        if not message_id:
            raise ProviderOperationError(
                "Gmail did not confirm the booking email.",
                provider="gmail",
                code="gmail_meeting_booking_send_unconfirmed",
                category="malformed_response",
            )
    except ProviderOperationError as error:
        offer.status = "needs_attention"
        offer.details = {**details, "booking_error": str(error)[:500], "error_code": error.code}
        session.commit()
        raise HTTPException(status_code=502, detail="Gmail could not confirm delivery. Check Sent before sending again.") from error
    except Exception as error:
        offer.status = "needs_attention"
        offer.details = {**details, "booking_error": type(error).__name__}
        session.commit()
        raise HTTPException(status_code=502, detail="Delivery could not be confirmed. Check Gmail Sent before retrying.") from error

    offer.status = "awaiting_booking"
    offer.details = {
        **details,
        "outbound_message_id": message_id,
        "offered_at": datetime.now(UTC).isoformat(),
    }
    session.commit()
    return {"status": "awaiting_booking", "message": "Booking link email sent. Corral will watch for a confirmed meeting."}


def _existing_confirmation_response(
    lead_id: UUID, event: PipelineEventORM
) -> ConfirmMeetingResponse:
    details = event.details
    return ConfirmMeetingResponse(
        lead_id=lead_id,
        provider=details["provider"],
        provider_event_id=str(details["provider_event_id"]),
        provider_event_uri=details.get("provider_event_uri"),
        event_url=details.get("event_url"),
        title=str(details.get("title") or "Meeting"),
        start_at=datetime.fromisoformat(str(details["start_at"])),
        end_at=datetime.fromisoformat(str(details["end_at"])),
        time_zone=str(details.get("time_zone") or "UTC"),
        status="scheduled",
    )


@router.post(
    "/{lead_id}/meetings/confirm",
    response_model=ConfirmMeetingResponse,
)
def confirm_meeting(
    lead_id: UUID,
    request: ConfirmMeetingRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> ConfirmMeetingResponse:
    lead = _lead_or_404(session, lead_id, workspace.workspace_id)
    session.scalar(
        select(LeadORM.id)
        .where(
            LeadORM.id == lead_id,
            LeadORM.workspace_id == workspace.workspace_id,
        )
        .with_for_update()
    )
    matching_messages = [
        event
        for event in session.scalars(
            select(PipelineEventORM).where(
                PipelineEventORM.lead_id == lead_id,
                PipelineEventORM.workspace_id == workspace.workspace_id,
                PipelineEventORM.stage == "gmail_message",
            )
        )
        if isinstance(event.details, dict)
        and str(event.details.get("message_id") or "") == request.source_message_id
    ]
    if request.source_gmail_connection_id is not None:
        expected_gmail_connection_id = str(request.source_gmail_connection_id)
        matching_messages = [
            event
            for event in matching_messages
            if str(event.details.get("gmail_connection_id") or "")
            == expected_gmail_connection_id
        ]
    elif len(
        {
            str(event.details.get("gmail_connection_id") or "")
            for event in matching_messages
        }
    ) > 1:
        raise HTTPException(
            status_code=409,
            detail=(
                "This Gmail message ID exists in more than one connected mailbox. "
                "Refresh meeting recommendations before booking."
            ),
        )
    message_event = matching_messages[0] if matching_messages else None
    if message_event is None:
        raise HTTPException(status_code=404, detail="The source Gmail message was not found.")
    source_details = (
        message_event.details if isinstance(message_event.details, dict) else {}
    )
    source_gmail_connection_id = str(
        request.source_gmail_connection_id
        or source_details.get("gmail_connection_id")
        or ""
    )
    source_thread_id = str(source_details.get("thread_id") or "")
    latest_request = find_meeting_request(
        session,
        lead,
        workspace.workspace_id,
        thread_id=source_thread_id or None,
        gmail_connection_id=source_gmail_connection_id or None,
    )
    if latest_request is None or latest_request["message_id"] != request.source_message_id:
        raise HTTPException(
            status_code=409,
            detail=(
                "The conversation changed since these times were suggested. "
                "Refresh the recommendations."
            ),
        )
    existing_for_source = next(
        (
            event
            for event in session.scalars(
                select(PipelineEventORM).where(
                    PipelineEventORM.lead_id == lead_id,
                    PipelineEventORM.workspace_id == workspace.workspace_id,
                    PipelineEventORM.stage == "meeting_scheduled",
                )
            )
            if isinstance(event.details, dict)
            and event.details.get("source_message_id") == request.source_message_id
            and str(event.details.get("source_gmail_connection_id") or "")
            == source_gmail_connection_id
        ),
        None,
    )
    if existing_for_source is not None:
        offers = session.scalars(
            select(PipelineEventORM).where(
                PipelineEventORM.workspace_id == workspace.workspace_id,
                PipelineEventORM.lead_id == lead_id,
                PipelineEventORM.stage == "meeting_email_offer",
            )
        )
        for offer in offers:
            offer_details = offer.details if isinstance(offer.details, dict) else {}
            if (
                offer_details.get("source_message_id") == request.source_message_id
                and str(offer_details.get("gmail_connection_id") or "")
                == source_gmail_connection_id
                and offer.status
                in {
                    "draft",
                    "sending",
                    "awaiting_confirmation",
                    "awaiting_booking",
                    "booking",
                    "needs_attention",
                }
            ):
                offer.status = "booked"
                offer.details = {
                    **offer_details,
                    "booking_method": (
                        "automatic_lead_confirmation"
                        if workspace.actor_id == "system:meeting-email-automation"
                        else "manual"
                    ),
                    "provider_event_id": existing_for_source.details.get(
                        "provider_event_id", ""
                    ),
                }
        session.commit()
        return _existing_confirmation_response(lead_id, existing_for_source)
    existing = appointment_event_exists(
        session,
        workspace_id=workspace.workspace_id,
        lead_id=lead_id,
        provider=request.provider,
        connection_id=str(request.connection_id),
        source_message_id=request.source_message_id,
        source_gmail_connection_id=source_gmail_connection_id,
        start_at=request.start_at,
    )
    if existing is not None:
        return _existing_confirmation_response(lead_id, existing)

    connection = session.scalar(
        select(ProviderConnectionORM).where(
            ProviderConnectionORM.id == request.connection_id,
            ProviderConnectionORM.workspace_id == workspace.workspace_id,
            ProviderConnectionORM.provider_type == request.provider,
            ProviderConnectionORM.is_active.is_(True),
            ProviderConnectionORM.status == "verified",
        )
    )
    if connection is None:
        raise HTTPException(
            status_code=409,
            detail="The selected calendar connection is no longer available.",
        )

    try:
        credentials = _resolve_credentials(session, workspace, connection)
        title = meeting_title(lead)
        description = meeting_description(lead, latest_request)
        event_url: str | None = None
        provider_event_uri: str | None = None
        if request.provider == "google_calendar":
            client = GoogleCalendarClient(credentials["access_token"])
            calendar = client.primary_calendar()
            time_zone = str(calendar.get("timeZone") or request.time_zone)
            busy = client.busy_intervals(
                request.start_at - timedelta(minutes=1),
                request.end_at + timedelta(minutes=1),
            )
            for interval in busy:
                busy_start = datetime.fromisoformat(interval["start"].replace("Z", "+00:00"))
                busy_end = datetime.fromisoformat(interval["end"].replace("Z", "+00:00"))
                if request.start_at < busy_end and request.end_at > busy_start:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "That time is no longer free in Google Calendar. "
                            "Refresh the recommendations."
                        ),
                    )
            event_id = hashlib.sha256(
                f"{workspace.workspace_id}:{lead_id}:{source_gmail_connection_id}:"
                f"{request.source_message_id}:"
                f"{request.start_at.astimezone(UTC).isoformat()}".encode()
            ).hexdigest()[:40]
            try:
                provider_event = client.create_event(
                    event_id=event_id,
                    summary=title,
                    description=description,
                    start_at=request.start_at,
                    end_at=request.end_at,
                    time_zone=time_zone,
                    attendee_email=lead.email,
                    attendee_name=lead.full_name,
                    lead_id=str(lead_id),
                    source_message_id=request.source_message_id,
                )
            except ProviderOperationError as error:
                if error.category != "conflict":
                    raise
                provider_event = client.event(event_id)
                properties = provider_event.get("extendedProperties", {}).get("private", {})
                if (
                    not isinstance(properties, dict)
                    or properties.get("corralLeadId") != str(lead_id)
                ):
                    raise
            provider_event_id = str(provider_event.get("id") or event_id)
            provider_event_uri = str(provider_event.get("htmlLink") or "")
            event_url = provider_event_uri or None
        else:
            if not request.event_type_uri:
                raise HTTPException(status_code=422, detail="Select a Calendly event type.")
            client = CalendlyClient(credentials["access_token"])
            user = client.current_user()
            user_uri = str(user.get("uri") or "")
            event_types = client.event_types(user_uri)
            event_type = next(
                (
                    item
                    for item in event_types
                    if item.get("uri") == request.event_type_uri and item.get("active") is not False
                ),
                None,
            )
            if event_type is None:
                raise HTTPException(
                    status_code=409,
                    detail="The selected Calendly event type is no longer active.",
                )
            try:
                duration = int(event_type.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0
            if duration < 5 or request.end_at - request.start_at != timedelta(minutes=duration):
                raise HTTPException(
                    status_code=409,
                    detail="The Calendly event duration changed. Refresh the recommendations.",
                )
            selected_start = request.start_at.astimezone(UTC)
            existing_events = client.scheduled_events(
                user_uri=user_uri,
                invitee_email=lead.email,
                start_at=request.start_at - timedelta(seconds=1),
                end_at=request.start_at + timedelta(seconds=1),
            )
            already_booked = next(
                (
                    item
                    for item in existing_events
                    if item.get("event_type") == request.event_type_uri
                    and item.get("start_time")
                    and datetime.fromisoformat(str(item["start_time"]).replace("Z", "+00:00"))
                    .astimezone(UTC)
                    == selected_start
                ),
                None,
            )
            if already_booked is None:
                available = client.available_times(
                    request.event_type_uri,
                    request.start_at - timedelta(minutes=1),
                    request.start_at + timedelta(minutes=1),
                )
                selected_is_available = False
                for item in available:
                    raw_start = item.get("start_time")
                    if not isinstance(raw_start, str):
                        continue
                    offered_start = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
                    if offered_start.astimezone(UTC) == selected_start:
                        selected_is_available = True
                        break
                if not selected_is_available:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "That time is no longer available in Calendly. "
                            "Refresh the recommendations."
                        ),
                    )
                invitee = client.create_invitee(
                    event_type_uri=request.event_type_uri,
                    start_at=request.start_at,
                    name=lead.full_name,
                    email=lead.email,
                    time_zone=request.time_zone,
                    location=_calendly_invitee_location(event_type),
                )
                invitee_uri = str(invitee.get("uri") or "")
                event_uri = str(invitee.get("event") or "")
                if not invitee_uri:
                    raise ProviderOperationError(
                        "Calendly accepted the request but did not return an invitee ID.",
                        provider="calendly",
                        code="calendly_invitee_id_missing",
                        category="malformed_response",
                        retryable=False,
                    )
                provider_event_uri = event_uri or invitee_uri
                provider_event_id = provider_event_uri.rsplit("/", 1)[-1]
            else:
                provider_event_uri = str(already_booked.get("uri") or "")
                if not provider_event_uri:
                    raise ProviderOperationError(
                        "Calendly returned a scheduled event without an event ID.",
                        provider="calendly",
                        code="calendly_scheduled_event_id_missing",
                        category="malformed_response",
                        retryable=False,
                    )
                provider_event_id = provider_event_uri.rsplit("/", 1)[-1]

    except ProviderOperationError as error:
        raise HTTPException(
            status_code=503 if error.retryable else 409,
            detail=str(error),
        ) from error

    now = datetime.now(UTC)
    details: dict[str, str] = {
        "provider": request.provider,
        "connection_id": str(connection.id),
        "provider_event_id": provider_event_id,
        "start_at": request.start_at.astimezone(UTC).isoformat(),
        "end_at": request.end_at.astimezone(UTC).isoformat(),
        "time_zone": request.time_zone,
        "title": title,
        "source_message_id": request.source_message_id,
        "source_gmail_connection_id": source_gmail_connection_id,
        "source_subject": latest_request["subject"],
    }
    if provider_event_uri:
        details["provider_event_uri"] = provider_event_uri
    if event_url:
        details["event_url"] = event_url
    session.add(
        PipelineEventORM(
            id=uuid4(),
            workspace_id=workspace.workspace_id,
            lead_id=lead_id,
            stage="meeting_scheduled",
            status="scheduled",
            details=details,
            created_at=now,
        )
    )
    offers = session.scalars(
        select(PipelineEventORM).where(
            PipelineEventORM.workspace_id == workspace.workspace_id,
            PipelineEventORM.lead_id == lead_id,
            PipelineEventORM.stage == "meeting_email_offer",
        )
    )
    for offer in offers:
        offer_details = offer.details if isinstance(offer.details, dict) else {}
        if (
            offer_details.get("source_message_id") == request.source_message_id
            and str(offer_details.get("gmail_connection_id") or "")
            == source_gmail_connection_id
            and offer.status
            in {
                "draft",
                "sending",
                "awaiting_confirmation",
                "awaiting_booking",
                "booking",
                "needs_attention",
            }
        ):
            offer.status = "booked"
            offer.details = {
                **offer_details,
                "booking_method": (
                    "automatic_lead_confirmation"
                    if workspace.actor_id == "system:meeting-email-automation"
                    else "manual"
                ),
                "provider_event_id": provider_event_id,
            }
    session.commit()
    return ConfirmMeetingResponse(
        lead_id=lead_id,
        provider=request.provider,
        provider_event_id=provider_event_id,
        provider_event_uri=provider_event_uri,
        event_url=event_url,
        title=title,
        start_at=request.start_at,
        end_at=request.end_at,
        time_zone=request.time_zone,
        status="scheduled",
    )
