"""Add actionable in-app notifications for meeting reply drafts."""

import sqlalchemy as sa
from alembic import op

revision = "0050_meeting_reply_notifications"
down_revision = "0049_deal_desk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assignment_notifications",
        sa.Column(
            "kind",
            sa.String(length=64),
            server_default="assignment",
            nullable=False,
        ),
    )
    op.add_column(
        "assignment_notifications",
        sa.Column("message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assignment_notifications", "message")
    op.drop_column("assignment_notifications", "kind")
