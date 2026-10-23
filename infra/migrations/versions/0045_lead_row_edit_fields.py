"""Persist generated markers and manual representative assignments."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045_lead_row_edits"
down_revision = "0044_merge_email_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column(
            "is_generated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "routing_decisions",
        sa.Column(
            "is_manually_assigned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("routing_decisions", "is_manually_assigned")
    op.drop_column("leads", "is_generated")
