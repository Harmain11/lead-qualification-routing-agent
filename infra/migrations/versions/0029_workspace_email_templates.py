"""Add editable workspace confirmation and follow-up email templates."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_workspace_email_templates"
down_revision: str | None = "0028_ai_follow_up_draft"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_email_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=180), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("generation_method", sa.String(length=32), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "kind", name="uq_workspace_email_template_kind"),
    )
    op.create_index(
        "ix_workspace_email_templates_workspace_kind",
        "workspace_email_templates",
        ["workspace_id", "kind"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_email_templates_workspace_kind", table_name="workspace_email_templates"
    )
    op.drop_table("workspace_email_templates")
