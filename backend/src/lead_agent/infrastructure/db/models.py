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

from lead_agent.domain.workspaces import DEFAULT_WORKSPACE_ID
from lead_agent.infrastructure.db.base import Base


class WorkspaceORM(Base):
    __tablename__ = "workspaces"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    is_demo: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserORM(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceMembershipORM(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_membership"),
        Index("ix_workspace_memberships_user_status", "user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    invited_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    permission_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceInvitationORM(Base):
    __tablename__ = "workspace_invitations"
    __table_args__ = (
        Index("ix_workspace_invitations_workspace_status", "workspace_id", "status"),
        Index("ix_workspace_invitations_email_status", "email", "status"),
        Index("ix_workspace_invitations_token_hash", "token_hash", unique=True),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    permission_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    invited_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    accepted_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceAdminAuditEventORM(Base):
    __tablename__ = "workspace_admin_audit_events"
    __table_args__ = (
        Index("ix_workspace_admin_audit_workspace_created", "workspace_id", "created_at"),
        Index("ix_workspace_admin_audit_operation", "operation", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    target_membership_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspace_memberships.id", ondelete="SET NULL"), nullable=True
    )
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    before: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceIcpVersionORM(Base):
    __tablename__ = "workspace_icp_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "version_number", name="uq_workspace_icp_version"),
        Index("ix_workspace_icp_workspace_status", "workspace_id", "status"),
        Index("ix_workspace_icp_workspace_updated", "workspace_id", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSessionORM(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_expires", "user_id", "expires_at"),
        Index("ix_auth_sessions_token_hash", "token_hash", unique=True),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    current_workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProviderConnectionORM(Base):
    __tablename__ = "provider_connections"
    __table_args__ = (
        Index("ix_provider_connections_workspace_provider", "workspace_id", "provider_type"),
        Index("ix_provider_connections_workspace_active", "workspace_id", "is_active"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    provider_type: Mapped[str] = mapped_column(String(64), nullable=False)
    external_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_name: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    secret_reference: Mapped[str | None] = mapped_column(String(512), nullable=True)
    credential_state: Mapped[str] = mapped_column(String(32), nullable=False, default="missing")
    last_validation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_validation_result: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    connection_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, default=False)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceEventORM(Base):
    __tablename__ = "source_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "external_event_id",
            name="uq_source_workspace_provider_external",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: DEFAULT_WORKSPACE_ID,
    )
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
        Index("ix_leads_workspace_created", "workspace_id", "created_at"),
        Index("ix_leads_workspace_received", "workspace_id", "received_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: DEFAULT_WORKSPACE_ID,
    )
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
    routing_decisions: Mapped[list[RoutingDecisionORM]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    crm_syncs: Mapped[list[CrmSyncORM]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    outcome: Mapped[LeadOutcomeORM | None] = relationship(
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
    disqualifier_evidence: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    rationale: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    score_breakdown: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    component_scores: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    icp_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspace_icp_versions.id", ondelete="RESTRICT"), nullable=True
    )
    icp_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="system_default"
    )
    icp_profile_version: Mapped[str] = mapped_column(String(64), nullable=False, default="legacy")
    icp_configuration_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="legacy"
    )
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    lead: Mapped[LeadORM] = relationship(back_populates="qualification")


class RoutingDecisionORM(Base):
    __tablename__ = "routing_decisions"
    __table_args__ = (
        Index("ix_routing_lead_current", "lead_id", "is_current"),
        Index("ix_routing_queue_created", "assigned_queue", "created_at"),
        UniqueConstraint("idempotency_key", name="uq_routing_idempotency"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: DEFAULT_WORKSPACE_ID,
    )
    lead_id: Mapped[UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    qualification_id: Mapped[UUID] = mapped_column(
        ForeignKey("qualification_results.id", ondelete="RESTRICT"), nullable=False
    )
    qualification_band: Mapped[str] = mapped_column(String(64), nullable=False)
    assigned_queue: Mapped[str] = mapped_column(String(128), nullable=False)
    assigned_rep_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    assigned_rep_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    routing_reason: Mapped[str] = mapped_column(Text, nullable=False)
    matched_rules: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    action_plan: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    priority: Mapped[str] = mapped_column(String(32), nullable=False)
    sla_target_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notification_state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    manual_review: Mapped[bool] = mapped_column(nullable=False, default=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    is_current: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    lead: Mapped[LeadORM] = relationship(back_populates="routing_decisions")
    notifications: Mapped[list[NotificationORM]] = relationship(
        back_populates="routing_decision", cascade="all, delete-orphan"
    )


class NotificationORM(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_status_updated", "status", "updated_at"),
        UniqueConstraint(
            "routing_decision_id", "provider", name="uq_notification_decision_provider"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: DEFAULT_WORKSPACE_ID,
    )
    routing_decision_id: Mapped[UUID] = mapped_column(
        ForeignKey("routing_decisions.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    routing_decision: Mapped[RoutingDecisionORM] = relationship(back_populates="notifications")


class PipelineEventORM(Base):
    __tablename__ = "pipeline_events"
    __table_args__ = (
        Index("ix_pipeline_lead_created", "lead_id", "created_at"),
        Index("ix_pipeline_events_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: DEFAULT_WORKSPACE_ID,
    )
    lead_id: Mapped[UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CrmSyncORM(Base):
    __tablename__ = "crm_syncs"
    __table_args__ = (
        Index("ix_crm_sync_lead_current", "lead_id", "provider", "is_current"),
        Index("ix_crm_sync_status_updated", "status", "updated_at"),
        UniqueConstraint("idempotency_key", name="uq_crm_sync_idempotency"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: DEFAULT_WORKSPACE_ID,
    )
    lead_id: Mapped[UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    routing_decision_id: Mapped[UUID] = mapped_column(
        ForeignKey("routing_decisions.id", ondelete="RESTRICT"), nullable=False
    )
    provider_connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False, default="upsert")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    provider_entity_ids: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(nullable=False, default=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_current: Mapped[bool] = mapped_column(nullable=False, default=True)

    lead: Mapped[LeadORM] = relationship(back_populates="crm_syncs")


class OutboxMessageORM(Base):
    __tablename__ = "outbox_messages"
    __table_args__ = (Index("ix_outbox_status_created", "status", "created_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        default=lambda: DEFAULT_WORKSPACE_ID,
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentToolAuditORM(Base):
    __tablename__ = "agent_tool_audit_events"
    __table_args__ = (
        Index("ix_agent_tool_audit_workspace_created", "workspace_id", "created_at"),
        UniqueConstraint(
            "workspace_id", "tool_name", "idempotency_key", name="uq_agent_tool_idempotency"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    required_permission: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LeadOutcomeORM(Base):
    __tablename__ = "lead_outcomes"
    __table_args__ = (
        Index("ix_lead_outcomes_workspace_stage", "workspace_id", "pipeline_stage"),
        Index("ix_lead_outcomes_workspace_closed", "workspace_id", "closed_at"),
        UniqueConstraint("lead_id", name="uq_lead_outcome_lead"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_stage: Mapped[str] = mapped_column(String(64), nullable=False)
    opportunity_value: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    closed_revenue: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    data_source: Mapped[str] = mapped_column(String(32), nullable=False, default="unavailable")
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    lead: Mapped[LeadORM] = relationship(back_populates="outcome")
