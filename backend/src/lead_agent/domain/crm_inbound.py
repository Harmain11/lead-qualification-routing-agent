from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

InboundSyncMode = Literal["initial", "incremental", "manual"]
InboundCursor = dict[str, Any]


class ExternalCrmRecord(BaseModel):
    """Provider record kept in memory only until it is normalized."""

    model_config = ConfigDict(frozen=True)

    provider: str
    object_type: str
    record_id: str
    values: dict[str, Any]
    modified_at: datetime | None = None
    provider_version: str | None = None
    deleted: bool = False
    archived: bool = False


class CanonicalInboundLead(BaseModel):
    """Provider-neutral, source-owned fields passed to the canonical lead pipeline."""

    model_config = ConfigDict(frozen=True)

    provider: str
    object_type: str
    provider_record_id: str
    normalized_payload: dict[str, Any]
    source_fingerprint: str = Field(min_length=64, max_length=64)
    provider_modified_at: datetime | None = None
    provider_version: str | None = None
    provider_source: str | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    synchronization_origin: str | None = None
    enrichment_fingerprint: str | None = None
    deleted: bool = False
    archived: bool = False


class InboundCrmPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    records: list[ExternalCrmRecord]
    next_cursor: InboundCursor | None = None
    checkpoint: InboundCursor | None = None


class CrmInboundProvider(Protocol):
    provider_name: str

    @property
    def connection_id(self) -> UUID | None: ...

    def list_records(
        self,
        *,
        object_type: str,
        cursor: InboundCursor | None,
        limit: int,
    ) -> InboundCrmPage: ...

    def fetch_record(self, *, object_type: str, record_id: str) -> ExternalCrmRecord: ...

    def normalize_record(self, record: ExternalCrmRecord) -> CanonicalInboundLead: ...
