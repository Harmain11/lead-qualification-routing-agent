"""Preserve CRM provider attempt history.

Revision ID: 0006_crm_attempt_history
Revises: 0005_crm_syncs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_crm_attempt_history"
down_revision: str | None = "0005_crm_syncs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "crm_syncs",
        sa.Column(
            "attempt_history",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("crm_syncs", "attempt_history")
