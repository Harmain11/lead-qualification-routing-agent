"""Add workspace-owned Slack notification routing configuration."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_notification_config"
down_revision: str | None = "0024_public_website_intake"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_notification_configs",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("default_slack_connection_id", sa.Uuid(), nullable=True),
        sa.Column("slack_rules", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["default_slack_connection_id"],
            ["provider_connections.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("workspace_id"),
    )


def downgrade() -> None:
    op.drop_table("workspace_notification_configs")
