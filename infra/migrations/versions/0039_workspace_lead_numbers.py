"""Assign stable, sequential lead numbers within each workspace."""

import sqlalchemy as sa
from alembic import op

revision = "0039_workspace_lead_numbers"
down_revision = "0038_slack_win_dm_opt_in"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "last_lead_number",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column("leads", sa.Column("lead_number", sa.Integer(), nullable=True))
    op.execute(
        """
        WITH numbered_leads AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY workspace_id
                    ORDER BY created_at, id
                ) AS lead_number
            FROM leads
        )
        UPDATE leads AS lead
        SET lead_number = numbered_leads.lead_number
        FROM numbered_leads
        WHERE lead.id = numbered_leads.id
        """
    )
    op.execute(
        """
        UPDATE workspaces AS workspace
        SET last_lead_number = COALESCE(
            (
                SELECT MAX(lead.lead_number)
                FROM leads AS lead
                WHERE lead.workspace_id = workspace.id
            ),
            0
        )
        """
    )
    op.alter_column("leads", "lead_number", nullable=False)
    op.alter_column("workspaces", "last_lead_number", server_default=None)
    op.create_unique_constraint(
        "uq_leads_workspace_number", "leads", ["workspace_id", "lead_number"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_leads_workspace_number", "leads", type_="unique")
    op.drop_column("leads", "lead_number")
    op.drop_column("workspaces", "last_lead_number")
