"""Store calendar entries and durable manual-review completion."""

import sqlalchemy as sa
from alembic import op

revision = "0053_workspace_calendar_events"
down_revision = "0052_workspace_email_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("manual_review_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "workspace_calendar_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_workspace_calendar_events_start",
        "workspace_calendar_events",
        ["workspace_id", "start_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_calendar_events_start", table_name="workspace_calendar_events")
    op.drop_table("workspace_calendar_events")
    op.drop_column("leads", "manual_review_completed_at")
