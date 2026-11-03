"""Preserve complete lead records while they are in trash."""

from alembic import op
import sqlalchemy as sa


revision = "0057_lossless_lead_trash"
down_revision = "0056_contact_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column("deleted_by", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_leads_workspace_deleted",
        "leads",
        ["workspace_id", "deleted_at"],
    )
    op.add_column(
        "lead_suppressions",
        sa.Column("lead_id", sa.Uuid(), nullable=True),
    )
    with op.batch_alter_table("lead_suppressions") as batch_op:
        batch_op.create_foreign_key(
            "fk_lead_suppressions_lead_id",
            "leads",
            ["lead_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("lead_suppressions") as batch_op:
        batch_op.drop_constraint(
            "fk_lead_suppressions_lead_id",
            type_="foreignkey",
        )
    op.drop_column("lead_suppressions", "lead_id")
    op.drop_index("ix_leads_workspace_deleted", table_name="leads")
    op.drop_column("leads", "deleted_by")
    op.drop_column("leads", "deleted_at")
