"""Keep explicitly deleted provider records out of future imports.

Revision ID: 0027_lead_suppressions
Revises: 0026_follow_up_email
"""

from alembic import op
import sqlalchemy as sa


revision = "0027_lead_suppressions"
down_revision = "0026_follow_up_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_suppressions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("lead_email", sa.String(length=320), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "provider", "external_id", name="uq_lead_suppression_source"
        ),
    )
    op.create_index(
        "ix_lead_suppression_workspace_created",
        "lead_suppressions",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_lead_suppression_workspace_created", table_name="lead_suppressions")
    op.drop_table("lead_suppressions")
