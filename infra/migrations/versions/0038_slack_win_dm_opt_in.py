"""Make Slack win direct messages explicitly opt-in."""

import sqlalchemy as sa
from alembic import op

revision = "0038_slack_win_dm_opt_in"
down_revision = "0037_cloudtalk_call_insights"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lead_outcomes",
        sa.Column("send_direct_message", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("lead_outcomes", "send_direct_message", server_default=None)


def downgrade() -> None:
    op.drop_column("lead_outcomes", "send_direct_message")
