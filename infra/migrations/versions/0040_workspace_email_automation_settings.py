"""Add an opt-in switch for automatic new-lead confirmation emails."""

import sqlalchemy as sa
from alembic import op

revision = "0040_email_automation"
down_revision = "0039_workspace_lead_numbers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_email_automation_settings",
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "lead_confirmation_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.alter_column(
        "workspace_email_automation_settings",
        "lead_confirmation_enabled",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_table("workspace_email_automation_settings")
