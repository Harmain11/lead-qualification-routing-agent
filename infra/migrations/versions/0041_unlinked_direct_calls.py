"""Allow confirmed direct-call receipts without a lead record."""

import sqlalchemy as sa
from alembic import op

revision = "0041_direct_calls"
down_revision = "0040_email_automation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "lead_calls",
        "lead_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM lead_calls WHERE lead_id IS NULL"))
    op.alter_column(
        "lead_calls",
        "lead_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
