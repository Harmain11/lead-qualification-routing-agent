"""Canonical record ownership and delegated connection access.

Revision ID: 0059_enterprise_access
Revises: 0058_workspace_teams
"""

import sqlalchemy as sa
from alembic import op

revision = "0059_enterprise_access"
down_revision = "0058_workspace_teams"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workspace_team_members") as batch:
        batch.add_column(sa.Column("is_manager", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
    op.execute(sa.text(
        "UPDATE workspace_team_members SET is_manager = 1 WHERE membership_id IN ("
        " SELECT id FROM workspace_memberships WHERE role = 'sales_manager'"
        ")"
    ))
    with op.batch_alter_table("lead_collaboration_notes") as batch:
        batch.add_column(sa.Column("is_private", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
    with op.batch_alter_table("workspace_memberships") as batch:
        batch.add_column(sa.Column("access_policy", sa.JSON(), nullable=False,
                                   server_default=sa.text("'{}'")))
    with op.batch_alter_table("workspace_invitations") as batch:
        batch.add_column(sa.Column("access_policy", sa.JSON(), nullable=False,
                                   server_default=sa.text("'{}'")))

    with op.batch_alter_table("provider_connections") as batch:
        batch.add_column(sa.Column("owner_user_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("connection_type", sa.String(32), nullable=False,
                                   server_default="workspace_managed"))
        batch.create_foreign_key(
            "fk_provider_connections_owner_user", "users", ["owner_user_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_provider_connections_owner", ["workspace_id", "owner_user_id"])
    op.execute(sa.text(
        "UPDATE provider_connections SET connection_type = 'personal' "
        "WHERE provider_type IN ('gmail', 'outlook')"
    ))

    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("current_owner_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("primary_team_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("original_importer_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("source_connection_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_leads_current_owner", "users", ["current_owner_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_leads_primary_team", "workspace_teams", ["primary_team_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_leads_original_importer", "users", ["original_importer_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_leads_source_connection", "provider_connections", ["source_connection_id"],
            ["id"], ondelete="SET NULL",
        )
        batch.create_index("ix_leads_workspace_owner", ["workspace_id", "current_owner_id"])
        batch.create_index("ix_leads_workspace_primary_team", ["workspace_id", "primary_team_id"])

    # The legacy team link remains the source of truth during this staged migration.
    # Seed a deterministic primary team while current owner backfill is performed lazily
    # from the current routing decision by the application.
    op.execute(sa.text(
        "UPDATE leads SET primary_team_id = ("
        " SELECT wtl.team_id FROM workspace_team_leads wtl"
        " WHERE wtl.workspace_id = leads.workspace_id AND wtl.lead_id = leads.id"
        " ORDER BY wtl.created_at ASC LIMIT 1"
        ") WHERE primary_team_id IS NULL"
    ))

    op.create_table(
        "lead_access_grants",
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("membership_id", sa.Uuid(),
                  sa.ForeignKey("workspace_memberships.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("can_edit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("granted_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reason", sa.String(255), nullable=False, server_default="collaboration"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lead_access_grants_member", "lead_access_grants",
                    ["workspace_id", "membership_id"])
    op.create_index("ix_lead_access_grants_lead", "lead_access_grants",
                    ["workspace_id", "lead_id"])

    op.create_table(
        "lead_assignment_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("previous_owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("new_owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("previous_team_id", sa.Uuid(),
                  sa.ForeignKey("workspace_teams.id", ondelete="SET NULL")),
        sa.Column("new_team_id", sa.Uuid(),
                  sa.ForeignKey("workspace_teams.id", ondelete="SET NULL")),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reason", sa.String(255), nullable=False, server_default="assignment"),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lead_assignment_history_lead", "lead_assignment_history",
                    ["workspace_id", "lead_id", "created_at"])
    op.create_index("ix_lead_assignment_history_owner", "lead_assignment_history",
                    ["workspace_id", "new_owner_id", "created_at"])

    op.create_table(
        "connection_access_grants",
        sa.Column("connection_id", sa.Uuid(),
                  sa.ForeignKey("provider_connections.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("membership_id", sa.Uuid(),
                  sa.ForeignKey("workspace_memberships.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("granted_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_connection_access_grants_member", "connection_access_grants",
                    ["workspace_id", "membership_id"])


def downgrade() -> None:
    op.drop_table("connection_access_grants")
    op.drop_table("lead_assignment_history")
    op.drop_table("lead_access_grants")
    with op.batch_alter_table("leads") as batch:
        batch.drop_index("ix_leads_workspace_primary_team")
        batch.drop_index("ix_leads_workspace_owner")
        batch.drop_constraint("fk_leads_source_connection", type_="foreignkey")
        batch.drop_constraint("fk_leads_original_importer", type_="foreignkey")
        batch.drop_constraint("fk_leads_primary_team", type_="foreignkey")
        batch.drop_constraint("fk_leads_current_owner", type_="foreignkey")
        batch.drop_column("source_connection_id")
        batch.drop_column("original_importer_id")
        batch.drop_column("primary_team_id")
        batch.drop_column("current_owner_id")
    with op.batch_alter_table("provider_connections") as batch:
        batch.drop_index("ix_provider_connections_owner")
        batch.drop_constraint("fk_provider_connections_owner_user", type_="foreignkey")
        batch.drop_column("connection_type")
        batch.drop_column("owner_user_id")
    with op.batch_alter_table("workspace_invitations") as batch:
        batch.drop_column("access_policy")
    with op.batch_alter_table("workspace_memberships") as batch:
        batch.drop_column("access_policy")
    with op.batch_alter_table("workspace_team_members") as batch:
        batch.drop_column("is_manager")
    with op.batch_alter_table("lead_collaboration_notes") as batch:
        batch.drop_column("is_private")
