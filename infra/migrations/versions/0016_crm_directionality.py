"""Add automatic inbound scheduling policy state.

Revision ID: 0016_crm_directionality
Revises: 0015_crm_inbound_sync
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_crm_directionality"
down_revision: str | None = "0015_crm_inbound_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "crm_syncs",
        sa.Column("requested_by", sa.String(length=255), server_default="system", nullable=False),
    )
    op.add_column(
        "crm_syncs",
        sa.Column("request_origin", sa.String(length=64), server_default="manual", nullable=False),
    )
    op.add_column(
        "crm_inbound_sync_states",
        sa.Column("initial_import_completed", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "crm_inbound_sync_states",
        sa.Column("sync_interval_seconds", sa.Integer(), server_default="300", nullable=False),
    )
    op.add_column(
        "crm_inbound_sync_states",
        sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_crm_inbound_state_due",
        "crm_inbound_sync_states",
        ["enabled", "next_sync_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_crm_inbound_state_due", table_name="crm_inbound_sync_states")
    op.drop_column("crm_inbound_sync_states", "next_sync_at")
    op.drop_column("crm_inbound_sync_states", "sync_interval_seconds")
    op.drop_column("crm_inbound_sync_states", "initial_import_completed")
    op.drop_column("crm_syncs", "request_origin")
    op.drop_column("crm_syncs", "requested_by")
