"""Track per-member lead update acknowledgement.

Revision ID: 0060_lead_read_receipts
Revises: 0059_enterprise_access
"""

import sqlalchemy as sa
from alembic import op

revision = "0060_lead_read_receipts"
down_revision = "0059_enterprise_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_read_receipts",
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_lead_read_receipts_workspace_user",
        "lead_read_receipts",
        ["workspace_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_table("lead_read_receipts")
