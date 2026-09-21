"""Add workspace-scoped connections and agent tool audit boundary.

Revision ID: 0007_workspace_integrations
Revises: 0006_crm_attempt_history
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "0007_workspace_integrations"
down_revision: str | None = "0006_crm_attempt_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"
OWNED_TABLES = (
    "source_events",
    "leads",
    "routing_decisions",
    "notifications",
    "pipeline_events",
    "crm_syncs",
    "outbox_messages",
)


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.execute(
        sa.text(
            "INSERT INTO workspaces (id, name, slug, status, created_at, updated_at) "
            "VALUES (:id, 'Default Workspace', 'default', 'active', CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP)"
        ).bindparams(
            sa.bindparam("id", value=UUID(DEFAULT_WORKSPACE_ID), type_=sa.Uuid())
        )
    )

    for table_name in OWNED_TABLES:
        op.add_column(
            table_name,
            sa.Column(
                "workspace_id",
                sa.Uuid(),
                nullable=False,
                server_default=sa.text(f"'{DEFAULT_WORKSPACE_ID}'"),
            ),
        )
        op.create_foreign_key(
            f"fk_{table_name}_workspace",
            table_name,
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="CASCADE",
        )

    op.drop_constraint("uq_source_provider_external", "source_events", type_="unique")
    op.create_unique_constraint(
        "uq_source_workspace_provider_external",
        "source_events",
        ["workspace_id", "provider", "external_event_id"],
    )
    op.create_index("ix_leads_workspace_created", "leads", ["workspace_id", "created_at"])

    op.create_table(
        "provider_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("provider_type", sa.String(length=64), nullable=False),
        sa.Column("external_account_id", sa.String(length=255), nullable=True),
        sa.Column("account_name", sa.String(length=255), nullable=False),
        sa.Column("auth_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("secret_reference", sa.String(length=512), nullable=True),
        sa.Column("credential_state", sa.String(length=32), nullable=False),
        sa.Column("last_validation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_validation_result", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_provider_connections_workspace_provider",
        "provider_connections",
        ["workspace_id", "provider_type"],
    )
    op.create_index(
        "ix_provider_connections_workspace_active",
        "provider_connections",
        ["workspace_id", "is_active"],
    )
    op.add_column(
        "crm_syncs", sa.Column("provider_connection_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_crm_syncs_provider_connection",
        "crm_syncs",
        "provider_connections",
        ["provider_connection_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "agent_tool_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("required_permission", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_summary", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "tool_name",
            "idempotency_key",
            name="uq_agent_tool_idempotency",
        ),
    )
    op.create_index(
        "ix_agent_tool_audit_workspace_created",
        "agent_tool_audit_events",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_tool_audit_workspace_created", table_name="agent_tool_audit_events"
    )
    op.drop_table("agent_tool_audit_events")
    op.drop_constraint(
        "fk_crm_syncs_provider_connection", "crm_syncs", type_="foreignkey"
    )
    op.drop_column("crm_syncs", "provider_connection_id")
    op.drop_index(
        "ix_provider_connections_workspace_active", table_name="provider_connections"
    )
    op.drop_index(
        "ix_provider_connections_workspace_provider", table_name="provider_connections"
    )
    op.drop_table("provider_connections")
    op.drop_index("ix_leads_workspace_created", table_name="leads")
    op.drop_constraint(
        "uq_source_workspace_provider_external", "source_events", type_="unique"
    )
    op.create_unique_constraint(
        "uq_source_provider_external", "source_events", ["provider", "external_event_id"]
    )
    for table_name in reversed(OWNED_TABLES):
        op.drop_constraint(f"fk_{table_name}_workspace", table_name, type_="foreignkey")
        op.drop_column(table_name, "workspace_id")
    op.drop_table("workspaces")
