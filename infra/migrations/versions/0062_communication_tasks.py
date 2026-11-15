"""Add tasks linked to communication messages.

Revision ID: 0062_communication_tasks
Revises: 0061_workspace_communication
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062_communication_tasks"
down_revision: str | None = "0061_workspace_communication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "communication_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("communication_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_communication_tasks_message", "communication_tasks", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_communication_tasks_message", table_name="communication_tasks")
    op.drop_table("communication_tasks")
