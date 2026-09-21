from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

SOURCE_EVENT_STATUSES = {"received", "processing", "processed", "failed", "manual_review"}


@dataclass(frozen=True)
class SourceEvent:
    """Immutable provider input plus the adapter's canonical mapping."""

    id: UUID
    channel: str
    external_id: str
    received_at: datetime
    raw_payload: dict[str, Any]
    normalized_payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        channel: str,
        external_id: str,
        raw_payload: Mapping[str, Any],
        normalized_payload: Mapping[str, Any],
        received_at: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SourceEvent:
        timestamp = received_at or datetime.now(UTC)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return cls(
            id=uuid4(),
            channel=channel,
            external_id=external_id,
            received_at=timestamp,
            raw_payload=dict(raw_payload),
            normalized_payload=dict(normalized_payload),
            metadata=dict(metadata or {}),
        )


class ChannelAdapter(Protocol):
    channel: str

    def adapt(self, payload: Mapping[str, Any]) -> SourceEvent:
        """Convert one provider payload into the common source-event contract."""
