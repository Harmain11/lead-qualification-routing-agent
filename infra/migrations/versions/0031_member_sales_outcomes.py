"""Member-attributed project/sale outcomes and independent win delivery receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0031_member_sales_outcomes"
down_revision = "0030_user_account_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspace_memberships", sa.Column("slack_member_id", sa.String(64)))
    op.add_column("workspace_memberships", sa.Column("slack_team_id", sa.String(64)))
    op.create_index("uq_member_slack_link", "workspace_memberships",
                    ["workspace_id", "slack_team_id", "slack_member_id"], unique=True)
    op.add_column("lead_outcomes", sa.Column("conversion_type", sa.String(16)))
    with op.batch_alter_table("lead_outcomes") as batch:
        batch.add_column(sa.Column("credited_user_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_outcome_credited_user", "users", ["credited_user_id"], ["id"], ondelete="SET NULL"
        )
    op.create_table(
        "lead_win_notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("outcome_id", sa.Uuid(), sa.ForeignKey("lead_outcomes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("destination", sa.String(32), nullable=False),
        sa.Column("recipient_id", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("external_message_id", sa.String(255)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("outcome_id", "destination", name="uq_win_notification_destination"),
    )


def downgrade() -> None:
    op.drop_table("lead_win_notifications")
    with op.batch_alter_table("lead_outcomes") as batch:
        batch.drop_constraint("fk_outcome_credited_user", type_="foreignkey")
        batch.drop_column("credited_user_id")
        batch.drop_column("conversion_type")
    op.drop_index("uq_member_slack_link", table_name="workspace_memberships")
    op.drop_column("workspace_memberships", "slack_team_id")
    op.drop_column("workspace_memberships", "slack_member_id")
