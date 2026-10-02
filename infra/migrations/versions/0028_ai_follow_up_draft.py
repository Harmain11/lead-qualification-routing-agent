"""Persist editable AI follow-up drafts and human approval metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_ai_follow_up_draft"
down_revision: str | None = "0027_lead_suppressions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lead_follow_up_emails", sa.Column("body", sa.Text(), nullable=True))
    op.add_column("lead_follow_up_emails", sa.Column("rationale", sa.Text(), nullable=True))
    op.add_column(
        "lead_follow_up_emails", sa.Column("generation_method", sa.String(32), nullable=True)
    )
    op.add_column("lead_follow_up_emails", sa.Column("generation_note", sa.Text(), nullable=True))
    op.add_column("lead_follow_up_emails", sa.Column("approved_by", sa.String(255), nullable=True))
    op.add_column(
        "lead_follow_up_emails",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("lead_follow_up_emails", "approved_at")
    op.drop_column("lead_follow_up_emails", "approved_by")
    op.drop_column("lead_follow_up_emails", "generation_note")
    op.drop_column("lead_follow_up_emails", "generation_method")
    op.drop_column("lead_follow_up_emails", "rationale")
    op.drop_column("lead_follow_up_emails", "body")
