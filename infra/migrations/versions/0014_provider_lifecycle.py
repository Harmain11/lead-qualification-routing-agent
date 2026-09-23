"""Add encrypted provider secrets and provider lifecycle audit events.

Revision ID: 0014_provider_lifecycle
Revises: 0013_lead_export_audit
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_provider_lifecycle"
down_revision: str | None = "0013_lead_export_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "encrypted_provider_secrets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("key_version", sa.String(length=64), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_encrypted_provider_secrets_workspace_provider",
        "encrypted_provider_secrets",
        ["workspace_id", "provider"],
    )
    op.create_table(
        "provider_lifecycle_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.String(length=255), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["provider_connections.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_provider_lifecycle_audit_workspace_created",
        "provider_lifecycle_audit_events",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_provider_lifecycle_audit_connection_created",
        "provider_lifecycle_audit_events",
        ["connection_id", "created_at"],
    )
    op.create_index(
        "ix_provider_lifecycle_audit_correlation",
        "provider_lifecycle_audit_events",
        ["correlation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_lifecycle_audit_correlation",
        table_name="provider_lifecycle_audit_events",
    )
    op.drop_index(
        "ix_provider_lifecycle_audit_connection_created",
        table_name="provider_lifecycle_audit_events",
    )
    op.drop_index(
        "ix_provider_lifecycle_audit_workspace_created",
        table_name="provider_lifecycle_audit_events",
    )
    op.drop_table("provider_lifecycle_audit_events")
    op.drop_index(
        "ix_encrypted_provider_secrets_workspace_provider",
        table_name="encrypted_provider_secrets",
    )
    op.drop_table("encrypted_provider_secrets")
