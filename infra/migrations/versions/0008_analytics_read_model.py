"""Add analytics workspace mode and opportunity/revenue facts.

Revision ID: 0008_analytics_read_model
Revises: 0007_workspace_integrations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_analytics_read_model"
down_revision: str | None = "0007_workspace_integrations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "lead_outcomes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_stage", sa.String(length=64), nullable=False),
        sa.Column("opportunity_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("closed_revenue", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("data_source", sa.String(length=32), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lead_id", name="uq_lead_outcome_lead"),
    )
    op.create_index(
        "ix_lead_outcomes_workspace_stage",
        "lead_outcomes",
        ["workspace_id", "pipeline_stage"],
    )
    op.create_index(
        "ix_lead_outcomes_workspace_closed",
        "lead_outcomes",
        ["workspace_id", "closed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_lead_outcomes_workspace_closed", table_name="lead_outcomes")
    op.drop_index("ix_lead_outcomes_workspace_stage", table_name="lead_outcomes")
    op.drop_table("lead_outcomes")
    op.drop_column("workspaces", "is_demo")
