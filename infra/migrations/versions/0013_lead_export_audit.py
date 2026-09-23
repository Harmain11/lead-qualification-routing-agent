"""Add durable workspace-scoped lead export audit events.

Revision ID: 0013_lead_export_audit
Revises: 0012_workspace_icp_versions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_lead_export_audit"
down_revision: str | None = "0012_workspace_icp_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lead_export_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("format", sa.String(length=16), nullable=True),
        sa.Column("record_count", sa.Integer(), nullable=True),
        sa.Column("selection_mode", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("filter_summary", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_lead_export_audit_workspace_created",
        "lead_export_audit_events",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_lead_export_audit_outcome_created",
        "lead_export_audit_events",
        ["outcome", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_lead_export_audit_outcome_created", table_name="lead_export_audit_events")
    op.drop_index("ix_lead_export_audit_workspace_created", table_name="lead_export_audit_events")
    op.drop_table("lead_export_audit_events")
