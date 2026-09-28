"""Add workspace-specific public website intake credentials."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_public_website_intake"
down_revision: str | None = "0023_workspace_routing_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_public_intake",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("public_token", sa.String(length=128), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("workspace_id"),
        sa.UniqueConstraint("public_token"),
    )


def downgrade() -> None:
    op.drop_table("workspace_public_intake")
