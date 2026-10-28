"""Store a workspace Google Calendar appointment-schedule URL."""

import sqlalchemy as sa
from alembic import op

revision = "0051_google_booking_url"
down_revision = "0050_meeting_reply_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_email_automation_settings",
        sa.Column("google_calendar_booking_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspace_email_automation_settings", "google_calendar_booking_url")
