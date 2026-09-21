from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lead_agent.infrastructure.db.base import Base


class SourceEventORM(Base):
    __tablename__ = "source_events"
    __table_args__ = (
        UniqueConstraint("provider", "external_event_id", name="uq_source_provider_external"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    processing_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lead: Mapped[LeadORM | None] = relationship(back_populates="source_event", uselist=False)


class LeadORM(Base):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_source_created", "source", "created_at"),
        Index("ix_leads_email", "email"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_events.id"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annual_revenue: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    geography: Mapped[str | None] = mapped_column(String(255), nullable=True)
    budget: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    product_or_service: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    source_event: Mapped[SourceEventORM | None] = relationship(back_populates="lead")
    qualification: Mapped[QualificationResultORM | None] = relationship(
        back_populates="lead", uselist=False, cascade="all, delete-orphan"
    )
    analysis: Mapped[AnalysisRunORM | None] = relationship(
        back_populates="lead", uselist=False, cascade="all, delete-orphan"
    )


class AnalysisRunORM(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        Index("ix_analysis_status_created", "status", "created_at"),
        UniqueConstraint("lead_id", name="uq_analysis_lead"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    lead_id: Mapped[UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(48), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    requires_manual_review: Mapped[bool] = mapped_column(nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_output: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    lead: Mapped[LeadORM] = relationship(back_populates="analysis")


class QualificationResultORM(Base):
    __tablename__ = "qualification_results"
    __table_args__ = (
        Index("ix_qualification_classification", "classification"),
        UniqueConstraint("lead_id", name="uq_qualification_lead"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    lead_id: Mapped[UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    total_score: Mapped[int] = mapped_column(Integer, nullable=False)
    classification: Mapped[str] = mapped_column(String(64), nullable=False)
    routing_queue: Mapped[str] = mapped_column(String(128), nullable=False)
    routing_action: Mapped[str] = mapped_column(String(128), nullable=False)
    hard_disqualified: Mapped[bool] = mapped_column(nullable=False, default=False)
    disqualifier_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rationale: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    score_breakdown: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    component_scores: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    lead: Mapped[LeadORM] = relationship(back_populates="qualification")


class OutboxMessageORM(Base):
    __tablename__ = "outbox_messages"
    __table_args__ = (Index("ix_outbox_status_created", "status", "created_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
