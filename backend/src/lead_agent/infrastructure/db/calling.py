"""Workspace-owned call receipts; secrets and raw provider payloads are never stored here."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lead_agent.infrastructure.db.base import Base


class CallRecordORM(Base):
    __tablename__ = "lead_calls"
    __table_args__ = (
        UniqueConstraint("workspace_id", "request_id", name="uq_call_request"),
        UniqueConstraint("connection_id", "external_id", name="uq_call_provider_receipt"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    lead_id: Mapped[UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(255))
    request_id: Mapped[UUID | None] = mapped_column()
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(64), default="outbound")
    customer_phone: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    transcript: Mapped[str | None] = mapped_column(Text)
    recording_url: Mapped[str | None] = mapped_column(Text)
    insights: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    message: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
