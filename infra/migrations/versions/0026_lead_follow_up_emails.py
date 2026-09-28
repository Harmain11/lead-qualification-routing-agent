"""Add idempotent lead confirmation email records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_follow_up_email"
down_revision: str | None = "0025_notification_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lead_follow_up_emails",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("provider_connection_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["provider_connection_id"],
            ["provider_connections.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lead_id", "kind", name="uq_lead_follow_up_email_kind"),
    )
    op.create_index(
        "ix_lead_follow_up_email_status_updated",
        "lead_follow_up_emails",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_lead_follow_up_email_status_updated", table_name="lead_follow_up_emails")
    op.drop_table("lead_follow_up_emails")
