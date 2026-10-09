"""Serialize periodic agent work across worker processes."""

import sqlalchemy as sa
from alembic import op

revision = "0034_operations_leases"
down_revision = "0033_workspace_operations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workspace_operations_jobs",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_workspace_operations_jobs_workspace_id",
        "workspace_operations_jobs",
        ["workspace_id"],
    )


def downgrade():
    op.drop_table("workspace_operations_jobs")
