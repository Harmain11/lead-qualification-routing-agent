"""Add durable CRM synchronization history.

Revision ID: 0005_crm_syncs
Revises: 0004_routing_notifications
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_crm_syncs"
down_revision: str | None = "0004_routing_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crm_syncs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("routing_decision_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("provider_entity_ids", sa.JSON(), nullable=False),
        sa.Column("payload_version", sa.String(length=32), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["routing_decision_id"], ["routing_decisions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_crm_sync_idempotency"),
    )
    op.create_index(
        "ix_crm_sync_lead_current", "crm_syncs", ["lead_id", "provider", "is_current"]
    )
    op.create_index("ix_crm_sync_status_updated", "crm_syncs", ["status", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_crm_sync_status_updated", table_name="crm_syncs")
    op.drop_index("ix_crm_sync_lead_current", table_name="crm_syncs")
    op.drop_table("crm_syncs")
