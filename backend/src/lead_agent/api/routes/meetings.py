from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission
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
from lead_agent.infrastructure.db.models import LeadORM, PipelineEventORM, ProviderConnectionORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.oauth import get_oauth_broker
from lead_agent.infrastructure.provider_lifecycle import WorkspaceAuthorizedConnectionResolver
from lead_agent.infrastructure.secrets import get_secret_store

router = APIRouter(prefix="/v1/leads", tags=["lead-meetings"])
MeetingProvider = Literal["google_calendar", "calendly"]


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


class MeetingCalendlyEventType(BaseModel):
    event_type_uri: str
    event_type_name: str
    status: Literal["available", "no_times", "calendar_unavailable"]


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
    source_subject: str | None = None
    source_excerpt: str | None = None
    integrations: list[MeetingIntegrationState]
    suggestions: list[MeetingSuggestion]
    calendly_event_types: list[MeetingCalendlyEventType] = Field(default_factory=list)
    provider_errors: list[MeetingProviderError]


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
        states.append(
            MeetingIntegrationState(
                provider=provider,  # type: ignore[arg-type]
                connected=connection is not None,
                account_name=connection.account_name if connection else None,
                message=(
                    None
                    if connection
                    else "Connect this workspace’s account in Integrations."
                ),
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
        "otherwise, Corral emails open options and books after the lead selects one."
    )
    requested_date = requested_meeting_date(source.get("excerpt", ""))
    if offer_event is not None:
        if offer_event.status == "awaiting_confirmation":
            automation_message = (
                "Available times were emailed to the lead. Corral will book after "
                "they clearly reply with an option number."
            )
        elif offer_event.status == "booked":
            automation_message = (
                "The meeting is booked after Corral confirmed an exact requested "
                "time was free, the lead selected an emailed option, or an admin confirmed it."
            )
        elif offer_event.status == "needs_attention":
            offer_details = (
                offer_event.details if isinstance(offer_event.details, dict) else {}
            )
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
    return MeetingRecommendationsResponse(
        lead_id=lead_id,
        detected=True,
        message=automation_message,
        source_message_id=source["message_id"],
        source_subject=source["subject"] or None,
        source_excerpt=source["excerpt"] or None,
        integrations=_integration_states(connections),
        suggestions=suggestions,
        calendly_event_types=[
            MeetingCalendlyEventType.model_validate(event_type)
            for event_type in calendly_event_types
        ],
        provider_errors=provider_errors,
    )


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
                in {"sending", "awaiting_confirmation", "booking", "needs_attention"}
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
            available = client.available_times(
                request.event_type_uri,
                request.start_at - timedelta(minutes=1),
                request.start_at + timedelta(minutes=1),
            )
            selected_start = request.start_at.astimezone(UTC)
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
                invitee = client.create_invitee(
                    event_type_uri=request.event_type_uri,
                    start_at=request.start_at,
                    name=lead.full_name,
                    email=lead.email,
                    time_zone=request.time_zone,
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
            in {"sending", "awaiting_confirmation", "booking", "needs_attention"}
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
