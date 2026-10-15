"""Persist CloudTalk Conversation Intelligence fields with call history."""

import sqlalchemy as sa
from alembic import op

revision = "0037_cloudtalk_call_insights"
down_revision = "0036_social_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lead_calls", sa.Column("insights", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("lead_calls", "insights")
