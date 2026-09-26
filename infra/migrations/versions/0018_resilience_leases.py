"""Add durable ownership leases for inbound synchronization.

Revision ID: 0018_resilience_leases
Revises: 0017_gmail_mailbox_reconnect
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_resilience_leases"
down_revision: str | None = "0017_gmail_mailbox_reconnect"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "crm_inbound_sync_states",
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "crm_inbound_sync_states",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_crm_inbound_state_lease_expires",
        "crm_inbound_sync_states",
        ["lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_crm_inbound_state_lease_expires", table_name="crm_inbound_sync_states")
    op.drop_column("crm_inbound_sync_states", "lease_expires_at")
    op.drop_column("crm_inbound_sync_states", "lease_owner")
