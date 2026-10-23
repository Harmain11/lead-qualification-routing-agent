"""Add per-source overrides for automatic lead confirmations."""

import sqlalchemy as sa
from alembic import op

revision = "0041_email_source_overrides"
down_revision = "0040_email_automation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_email_automation_source_settings",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("lead_confirmation_enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id", "source"),
    )


def downgrade() -> None:
    op.drop_table("workspace_email_automation_source_settings")
