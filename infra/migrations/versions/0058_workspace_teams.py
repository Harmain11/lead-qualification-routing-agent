"""Explicit team membership and lead scope, with one compatibility team per workspace."""

import sqlalchemy as sa
from alembic import op

revision = "0058_workspace_teams"
down_revision = "0057_lossless_lead_trash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_teams",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("name_key", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_team_workspace_name", "workspace_teams",
                    ["workspace_id", "name_key"], unique=True)
    op.create_index("uq_team_workspace_id", "workspace_teams", ["workspace_id", "id"], unique=True)
    op.create_index(
        "uq_team_workspace_default", "workspace_teams", ["workspace_id"], unique=True,
        sqlite_where=sa.text("is_default = 1"), postgresql_where=sa.text("is_default IS TRUE"),
    )
    op.create_table(
        "workspace_team_members",
        sa.Column("team_id", sa.Uuid(), primary_key=True),
        sa.Column("membership_id", sa.Uuid(),
                  sa.ForeignKey("workspace_memberships.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "team_id"], ["workspace_teams.workspace_id", "workspace_teams.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_team_members_membership", "workspace_team_members",
                    ["membership_id", "team_id"])
    op.create_table(
        "workspace_team_leads",
        sa.Column("team_id", sa.Uuid(), primary_key=True),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "team_id"], ["workspace_teams.workspace_id", "workspace_teams.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_team_leads_lead", "workspace_team_leads", ["lead_id", "team_id"])

    # The default team's ID equals its workspace ID in a separate UUID namespace
    # (table). Deterministic INSERT SELECT works in both offline SQL and SQLite,
    # avoids loading customer records into Python, and preserves original rows.
    workspaces = sa.table("workspaces", sa.column("id", sa.Uuid()))
    teams = sa.table("workspace_teams", *(
        sa.column(name) for name in (
            "id", "workspace_id", "name", "name_key", "description", "is_default",
            "created_at", "updated_at",
        )
    ))
    op.execute(teams.insert().from_select(list(teams.c.keys()), sa.select(
        workspaces.c.id, workspaces.c.id, sa.literal("Default team"), sa.literal("default team"),
        sa.literal("Compatibility scope for existing workspace data"), sa.true(),
        sa.func.now(), sa.func.now(),
    )))
    for table_name, target_name, id_column in (
        ("workspace_memberships", "workspace_team_members", "membership_id"),
        ("leads", "workspace_team_leads", "lead_id"),
    ):
        source = sa.table(table_name, sa.column("id"), sa.column("workspace_id"), sa.column("status"))
        target = sa.table(target_name, *(
            sa.column(name) for name in ("team_id", id_column, "workspace_id", "created_at")
        ))
        query = sa.select(source.c.workspace_id, source.c.id, source.c.workspace_id, sa.func.now())
        if table_name == "workspace_memberships":
            query = query.where(source.c.status == "active")
        op.execute(target.insert().from_select(list(target.c.keys()), query))


def downgrade() -> None:
    op.drop_table("workspace_team_leads")
    op.drop_table("workspace_team_members")
    op.drop_table("workspace_teams")
