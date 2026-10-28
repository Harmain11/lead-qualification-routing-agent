"""Save private workspace email drafts before Gmail delivery."""

import sqlalchemy as sa
from alembic import op

revision = "0052_workspace_email_drafts"
down_revision = "0051_google_booking_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_email_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_workspace_email_drafts_owner_updated",
        "workspace_email_drafts",
        ["workspace_id", "created_by", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_email_drafts_owner_updated", table_name="workspace_email_drafts")
    op.drop_table("workspace_email_drafts")
