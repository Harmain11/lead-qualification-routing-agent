"""Persist rich appointment draft content for reviewed Gmail replies."""

import sqlalchemy as sa
from alembic import op

revision = "0054_follow_up_email_composer"
down_revision = "0053_workspace_calendar_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lead_follow_up_emails", sa.Column("cc", sa.String(length=1024), nullable=True))
    op.add_column("lead_follow_up_emails", sa.Column("bcc", sa.String(length=1024), nullable=True))
    op.add_column("lead_follow_up_emails", sa.Column("html_body", sa.Text(), nullable=True))
    op.add_column("lead_follow_up_emails", sa.Column("attachments", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("lead_follow_up_emails", "attachments")
    op.drop_column("lead_follow_up_emails", "html_body")
    op.drop_column("lead_follow_up_emails", "bcc")
    op.drop_column("lead_follow_up_emails", "cc")
