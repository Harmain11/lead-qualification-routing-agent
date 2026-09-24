"""Add provider-neutral inbound CRM synchronization state.

Revision ID: 0015_crm_inbound_sync
Revises: 0014_provider_lifecycle
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_crm_inbound_sync"
down_revision: str | None = "0014_provider_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crm_inbound_sync_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_successful_cursor", sa.JSON(), nullable=True),
        sa.Column("resume_cursor", sa.JSON(), nullable=True),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("records_processed", sa.Integer(), nullable=False),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["provider_connections.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "connection_id",
            "provider",
            "object_type",
            name="uq_crm_inbound_state_scope",
        ),
    )
    op.create_index(
        "ix_crm_inbound_state_workspace_provider",
        "crm_inbound_sync_states",
        ["workspace_id", "provider"],
    )
    op.create_index(
        "ix_crm_inbound_state_status_updated",
        "crm_inbound_sync_states",
        ["status", "updated_at"],
    )
    op.create_table(
        "crm_inbound_sync_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("state_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column("correlation_id", sa.String(length=255), nullable=False),
        sa.Column("cursor_start", sa.JSON(), nullable=True),
        sa.Column("cursor_current", sa.JSON(), nullable=True),
        sa.Column("checkpoint", sa.JSON(), nullable=True),
        sa.Column("records_fetched", sa.Integer(), nullable=False),
        sa.Column("records_created", sa.Integer(), nullable=False),
        sa.Column("records_updated", sa.Integer(), nullable=False),
        sa.Column("records_unchanged", sa.Integer(), nullable=False),
        sa.Column("records_skipped", sa.Integer(), nullable=False),
        sa.Column("records_failed", sa.Integer(), nullable=False),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["provider_connections.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["state_id"], ["crm_inbound_sync_states.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_crm_inbound_run_workspace_started",
        "crm_inbound_sync_runs",
        ["workspace_id", "started_at"],
    )
    op.create_index(
        "ix_crm_inbound_run_state_status",
        "crm_inbound_sync_runs",
        ["state_id", "status"],
    )
    op.create_index(
        "ix_crm_inbound_run_correlation",
        "crm_inbound_sync_runs",
        ["correlation_id"],
    )
    op.create_table(
        "crm_provider_record_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("provider_record_id", sa.String(length=255), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("matching_strategy", sa.String(length=64), nullable=False),
        sa.Column("provider_source", sa.String(length=255), nullable=True),
        sa.Column("provider_metadata", sa.JSON(), nullable=False),
        sa.Column("provider_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_version", sa.String(length=255), nullable=True),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("enrichment_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("last_writeback_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("synchronization_origin", sa.String(length=32), nullable=False),
        sa.Column("last_change_kind", sa.String(length=64), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_writeback_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["provider_connections.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "connection_id",
            "provider",
            "object_type",
            "provider_record_id",
            name="uq_crm_provider_record_scope",
        ),
    )
    op.create_index(
        "ix_crm_provider_link_workspace_lead",
        "crm_provider_record_links",
        ["workspace_id", "lead_id"],
    )
    op.create_index(
        "ix_crm_provider_link_workspace_provider",
        "crm_provider_record_links",
        ["workspace_id", "provider", "object_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_crm_provider_link_workspace_provider", table_name="crm_provider_record_links"
    )
    op.drop_index(
        "ix_crm_provider_link_workspace_lead", table_name="crm_provider_record_links"
    )
    op.drop_table("crm_provider_record_links")
    op.drop_index("ix_crm_inbound_run_correlation", table_name="crm_inbound_sync_runs")
    op.drop_index("ix_crm_inbound_run_state_status", table_name="crm_inbound_sync_runs")
    op.drop_index("ix_crm_inbound_run_workspace_started", table_name="crm_inbound_sync_runs")
    op.drop_table("crm_inbound_sync_runs")
    op.drop_index(
        "ix_crm_inbound_state_status_updated", table_name="crm_inbound_sync_states"
    )
    op.drop_index(
        "ix_crm_inbound_state_workspace_provider", table_name="crm_inbound_sync_states"
    )
    op.drop_table("crm_inbound_sync_states")
