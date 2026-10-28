from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lead_agent.domain.workspaces import DEFAULT_WORKSPACE_ID
from lead_agent.infrastructure.db.base import Base

# Register commercial tables in the same metadata used by migrations and tests.
from lead_agent.infrastructure.db.deals import CommercialSettingsORM, DealORM  # noqa: F401


class WorkspaceORM(Base):
    __tablename__ = "workspaces"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    is_demo: Mapped[bool] = mapped_column(nullable=False, default=False)
    last_lead_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceEmailDraftORM(Base):
    """Private, workspace-scoped drafts saved before Gmail delivery."""

    __tablename__ = "workspace_email_drafts"
    __table_args__ = (
        Index("ix_workspace_email_drafts_owner_updated", "workspace_id", "created_by", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceCalendarEventORM(Base):
    """Workspace-owned manual calendar entries, optionally linked to a lead."""

    __tablename__ = "workspace_calendar_events"
    __table_args__ = (
        Index("ix_workspace_calendar_events_start", "workspace_id", "start_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceEmailTemplateORM(Base):
    """Workspace-owned confirmation and follow-up email templates."""

    __tablename__ = "workspace_email_templates"
    __table_args__ = (
        UniqueConstraint("workspace_id", "kind", name="uq_workspace_email_template_kind"),
        Index("ix_workspace_email_templates_workspace_kind", "workspace_id", "kind"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(180), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    generation_method: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceEmailAutomationSettingORM(Base):
    """Workspace-level switch for automatic new-lead confirmation emails."""

    __tablename__ = "workspace_email_automation_settings"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    lead_confirmation_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    google_calendar_booking_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceEmailAutomationSourceSettingORM(Base):
    """Platform-specific override for automatic lead confirmation emails."""

    __tablename__ = "workspace_email_automation_source_settings"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    lead_confirmation_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceRoutingConfigORM(Base):
    """Workspace-owned routing weights and manual-review choices."""

    __tablename__ = "workspace_routing_configs"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    member_weights: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    manual_review_settings: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceNotificationConfigORM(Base):
    """Workspace-owned notification destinations and matching rules."""

    __tablename__ = "workspace_notification_configs"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    default_slack_connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="SET NULL"), nullable=True
    )
    slack_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspacePublicIntakeORM(Base):
    """Bearer URL credential for a workspace's public website form."""

    __tablename__ = "workspace_public_intake"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    public_token: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserORM(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bio: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    avatar_data_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    email_verified: Mapped[bool] = mapped_column(nullable=False, default=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceAgentConversationORM(Base):
    """A user's private, workspace-scoped transcript with Corral Agent."""

    __tablename__ = "workspace_agent_conversations"
    __table_args__ = (
        Index(
            "ix_workspace_agent_conversations_owner_updated",
            "workspace_id",
            "actor_id",
            "updated_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="New conversation")
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExternalIdentityORM(Base):
    """Stable login identity mapping; deliberately separate from provider connections."""

    __tablename__ = "external_identities"
    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_external_identity_provider_subject"),
        Index("ix_external_identities_user_provider", "user_id", "provider"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_verified: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthOAuthStateORM(Base):
    """Short-lived, single-use auth state; never shared with integration OAuth state."""

    __tablename__ = "auth_oauth_states"
    __table_args__ = (Index("ix_auth_oauth_states_expires", "expires_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    code_verifier: Mapped[str] = mapped_column(String(256), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    invitation_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EmailVerificationTokenORM(Base):
    """Single-use password-signup verification tokens; only their hashes are stored."""

    __tablename__ = "email_verification_tokens"
    __table_args__ = (Index("ix_email_verification_tokens_user_created", "user_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    workspace_name: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceMembershipORM(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_membership"),
        Index("ix_workspace_memberships_user_status", "user_id", "status"),
        Index(
            "uq_member_slack_link", "workspace_id", "slack_team_id", "slack_member_id", unique=True
        ),
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
    permission_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    slack_member_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    slack_team_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    permission_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
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


class LeadExportAuditEventORM(Base):
    __tablename__ = "lead_export_audit_events"
    __table_args__ = (
        Index("ix_lead_export_audit_workspace_created", "workspace_id", "created_at"),
        Index("ix_lead_export_audit_outcome_created", "outcome", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    format: Mapped[str | None] = mapped_column(String(16), nullable=True)
    record_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selection_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    filter_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
        Index(
            "uq_gmail_active_workspace_mailbox",
            "workspace_id",
            "external_account_id",
            unique=True,
            sqlite_where=text(
                "provider_type = 'gmail' AND is_active = 1 AND external_account_id IS NOT NULL"
            ),
            postgresql_where=text(
                "provider_type = 'gmail' AND is_active IS TRUE AND external_account_id IS NOT NULL"
            ),
        ),
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


class EncryptedProviderSecretORM(Base):
    __tablename__ = "encrypted_provider_secrets"
    __table_args__ = (
        Index("ix_encrypted_provider_secrets_workspace_provider", "workspace_id", "provider"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProviderLifecycleAuditORM(Base):
    __tablename__ = "provider_lifecycle_audit_events"
    __table_args__ = (
        Index("ix_provider_lifecycle_audit_workspace_created", "workspace_id", "created_at"),
        Index("ix_provider_lifecycle_audit_connection_created", "connection_id", "created_at"),
        Index("ix_provider_lifecycle_audit_correlation", "correlation_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CrmInboundSyncStateORM(Base):
    __tablename__ = "crm_inbound_sync_states"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "connection_id",
            "provider",
            "object_type",
            name="uq_crm_inbound_state_scope",
        ),
        Index("ix_crm_inbound_state_workspace_provider", "workspace_id", "provider"),
        Index("ix_crm_inbound_state_status_updated", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    initial_import_completed: Mapped[bool] = mapped_column(nullable=False, default=False)
    sync_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    last_successful_cursor: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    resume_cursor: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    records_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CrmInboundSyncRunORM(Base):
    __tablename__ = "crm_inbound_sync_runs"
    __table_args__ = (
        Index("ix_crm_inbound_run_workspace_started", "workspace_id", "started_at"),
        Index("ix_crm_inbound_run_state_status", "state_id", "status"),
        Index("ix_crm_inbound_run_correlation", "correlation_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=False
    )
    state_id: Mapped[UUID] = mapped_column(
        ForeignKey("crm_inbound_sync_states.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    cursor_start: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    cursor_current: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    checkpoint: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    records_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CrmProviderRecordLinkORM(Base):
    __tablename__ = "crm_provider_record_links"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "connection_id",
            "provider",
            "object_type",
            "provider_record_id",
            name="uq_crm_provider_record_scope",
        ),
        Index("ix_crm_provider_link_workspace_lead", "workspace_id", "lead_id"),
        Index(
            "ix_crm_provider_link_workspace_provider",
            "workspace_id",
            "provider",
            "object_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    lead_id: Mapped[UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    matching_strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    provider_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    enrichment_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_writeback_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    synchronization_origin: Mapped[str] = mapped_column(
        String(32), nullable=False, default="provider"
    )
    last_change_kind: Mapped[str] = mapped_column(
        String(64), nullable=False, default="source_changed"
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_writeback_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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


class SocialConversationORM(Base):
    __tablename__ = "social_conversations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "provider", "connection_id", "external_account_id",
            "participant_id", "conversation_identity", name="uq_social_conversation_identity",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    participant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    conversation_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    lead_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SocialMessageORM(Base):
    __tablename__ = "social_messages"
    __table_args__ = (
        UniqueConstraint("connection_id", "provider_event_id", name="uq_social_message_replay"),
        Index("ix_social_messages_conversation_event", "conversation_id", "event_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("social_conversations.id", ondelete="CASCADE"), nullable=False
    )
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    lead_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LeadORM(Base):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "lead_number", name="uq_leads_workspace_number"
        ),
        Index(
            "uq_leads_source_event",
            "source_event_id",
            unique=True,
            sqlite_where=text("source_event_id IS NOT NULL"),
            postgresql_where=text("source_event_id IS NOT NULL"),
        ),
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
    lead_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_events.id"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
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
    manual_review_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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


class LeadSuppressionORM(Base):
    """Source records explicitly deleted by a workspace user.

    Provider syncs consult this table before creating a lead so a deleted CRM
    or mailbox record does not immediately reappear on the next sync.
    """

    __tablename__ = "lead_suppressions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "provider", "external_id", name="uq_lead_suppression_source"
        ),
        Index("ix_lead_suppression_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    lead_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    reason: Mapped[str] = mapped_column(String(64), nullable=False, default="user_deleted")
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LeadFollowUpEmailORM(Base):
    """Workspace-scoped confirmation email or human-approved follow-up draft."""

    __tablename__ = "lead_follow_up_emails"
    __table_args__ = (
        UniqueConstraint("lead_id", "kind", name="uq_lead_follow_up_email_kind"),
        Index("ix_lead_follow_up_email_status_updated", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    provider_connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False, default="lead_confirmation")
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    cc: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    bcc: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    html_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachments: Mapped[list[dict[str, str]] | None] = mapped_column(JSON, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    generation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnalysisRunORM(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        Index("ix_analysis_status_created", "status", "created_at"),
        Index("ix_analysis_request_fingerprint", "analysis_request_fingerprint"),
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
    analysis_request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    icp_source: Mapped[str] = mapped_column(String(32), nullable=False, default="system_default")
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
    is_manually_assigned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
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


class AssignmentNotificationORM(Base):
    __tablename__ = "assignment_notifications"
    __table_args__ = (
        Index(
            "ix_assignment_notifications_recipient_created",
            "workspace_id",
            "recipient_user_id",
            "created_at",
        ),
        Index(
            "ix_assignment_notifications_recipient_unread",
            "workspace_id",
            "recipient_user_id",
            "read_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    recipient_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    assigned_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(
        String(64), nullable=False, default="assignment", server_default="assignment"
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False, default="system")
    request_origin: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
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
    conversion_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    send_direct_message: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    credited_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    opportunity_value: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    closed_revenue: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    data_source: Mapped[str] = mapped_column(String(32), nullable=False, default="unavailable")
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    lead: Mapped[LeadORM] = relationship(back_populates="outcome")
    credited_user: Mapped[UserORM | None] = relationship()
    win_notifications: Mapped[list[LeadWinNotificationORM]] = relationship(
        back_populates="outcome", cascade="all, delete-orphan"
    )


class LeadWinNotificationORM(Base):
    """Independent, durable channel and member delivery receipts for a won lead."""

    __tablename__ = "lead_win_notifications"
    __table_args__ = (
        UniqueConstraint("outcome_id", "destination", name="uq_win_notification_destination"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    outcome_id: Mapped[UUID] = mapped_column(
        ForeignKey("lead_outcomes.id", ondelete="CASCADE"), nullable=False
    )
    destination: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome: Mapped[LeadOutcomeORM] = relationship(back_populates="win_notifications")


from lead_agent.infrastructure.db import calling as calling_models  # noqa: E402,F401
from lead_agent.infrastructure.db import operations as operations_models  # noqa: E402,F401
