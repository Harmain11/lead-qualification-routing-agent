"""Persist lead assignment notifications for workspace members."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_assignment_notifications"
down_revision: str | None = "0022_email_verification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assignment_notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assignment_notifications_recipient_created",
        "assignment_notifications",
        ["workspace_id", "recipient_user_id", "created_at"],
    )
    op.create_index(
        "ix_assignment_notifications_recipient_unread",
        "assignment_notifications",
        ["workspace_id", "recipient_user_id", "read_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assignment_notifications_recipient_unread",
        table_name="assignment_notifications",
    )
    op.drop_index(
        "ix_assignment_notifications_recipient_created",
        table_name="assignment_notifications",
    )
    op.drop_table("assignment_notifications")
