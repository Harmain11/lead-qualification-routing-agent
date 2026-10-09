"""Explicit public response fields for workspace operations."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from lead_agent.domain.operations import AutomationInput


class RecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_validator("created_at", "updated_at", "received_at", "due_at", check_fields=False)
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        # SQLite removes timezone metadata; browser dates still need an explicit offset.
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ContactRecord(RecordResponse):
    id: UUID
    name: str
    email: str | None
    company_id: UUID | None
    phone: str | None
    job_title: str | None
    timezone: str
    updated_at: datetime


class CompanyRecord(RecordResponse):
    id: UUID
    name: str
    domain: str | None
    website: str | None
    website_reviewed: bool
    industry: str | None
    profile: dict[str, Any]


class ContactPage(BaseModel):
    items: list[ContactRecord]
    total: int


class CompanyPage(BaseModel):
    items: list[CompanyRecord]
    total: int


class LeadTouch(RecordResponse):
    id: UUID
    source: str
    received_at: datetime
    name: str | None = None
    score: int | None = None
    stage: str | None = None


class ContactDetail(BaseModel):
    contact: ContactRecord
    company: CompanyRecord | None
    touches: list[LeadTouch]


class CompanyDetail(BaseModel):
    company: CompanyRecord
    contacts: list[ContactRecord]
    lead_count: int
    truncated: bool
    won: int
    revenue_by_currency: dict[str, float]
    touches: list[LeadTouch]


class ConsentRecord(RecordResponse):
    id: UUID
    email: str
    channel: str
    status: str
    basis: str
    purpose: str
    evidence: str
    created_at: datetime


class ConsentPage(BaseModel):
    items: list[ConsentRecord]
    total: int


class NoteRecord(RecordResponse):
    id: UUID
    body: str
    version: int
    author_id: str
    updated_by: str
    updated_at: datetime


class PresenceRecord(BaseModel):
    user_id: UUID
    name: str


class CollaborationData(BaseModel):
    notes: list[NoteRecord]
    presence: list[PresenceRecord]


class InsightRecord(RecordResponse):
    id: UUID
    kind: str
    lead_id: UUID | None
    company_id: UUID | None
    status: str
    content: dict[str, Any]
    created_at: datetime


class InsightPage(BaseModel):
    items: list[InsightRecord]


class AutomationRecord(RecordResponse):
    id: UUID
    name: str
    kind: str
    active: bool
    version: int
    specification: AutomationInput


class AutomationPage(BaseModel):
    items: list[AutomationRecord]


class RunRecord(RecordResponse):
    id: UUID
    lead_id: UUID
    definition_id: UUID
    cursor: str | None
    status: str
    due_at: datetime
    last_error: str | None
    state: dict[str, Any]


class RunPage(BaseModel):
    items: list[RunRecord]
