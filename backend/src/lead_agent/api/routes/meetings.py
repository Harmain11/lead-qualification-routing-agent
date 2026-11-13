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
from lead_agent.application.lead_access import assert_lead_action
from lead_agent.application.meeting_email import (
    can_replace_unbooked_offer,
    create_booking_link_offer_draft,
    create_preferred_meeting_offer_draft,
    create_selected_slot_offer_draft,
    reconcile_booking_link_offer,
)
from lead_agent.application.meetings import (
    MeetingIntentUnavailable,
    _parse_provider_time,
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
MeetingOfferStatus = Literal[
    "awaiting_send",
    "sending",
    "awaiting_confirmation",
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
    offer_booking_link_mode: bool = False
    offer_has_confirmed_selection: bool = False
    offer_can_be_replaced: bool = False
    booked_meeting_at: datetime | None = None
    cancelled_meeting_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancellation_reason: str | None = None


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


class MeetingOfferDraftResponse(BaseModel):
    draft_id: UUID
    status: Literal["draft"]


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
... (+934 lines) [see remaining: rtk proxy git show HEAD:backend/src/lead_agent/api/routes/meetings.py | tail -n +243]
