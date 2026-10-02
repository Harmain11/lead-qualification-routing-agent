"""Add workspace roles, invitations, permission overrides, and admin audit events.

Revision ID: 0011_workspace_rbac
Revises: 0010_production_authentication
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_workspace_rbac"
down_revision: str | None = "0010_production_authentication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_memberships",
        sa.Column("invited_by", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "workspace_memberships",
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workspace_memberships",
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workspace_memberships",
        sa.Column(
            "permission_overrides",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    with op.batch_alter_table("workspace_memberships") as batch_op:
        batch_op.create_foreign_key(
            "fk_workspace_memberships_invited_by_users",
            "users",
            ["invited_by"],
            ["id"],
            ondelete="SET NULL",
        )
    op.execute(
        sa.text(
            "UPDATE workspace_memberships SET role = 'viewer' "
            "WHERE role IS NULL OR role = 'member'"
        )
    )

    op.create_table(
        "workspace_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("permission_overrides", sa.JSON(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("invited_by", sa.Uuid(), nullable=False),
        sa.Column("accepted_user_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["accepted_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_invitations_workspace_status",
        "workspace_invitations",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_workspace_invitations_email_status",
        "workspace_invitations",
        ["email", "status"],
    )
    op.create_index(
        "ix_workspace_invitations_token_hash",
        "workspace_invitations",
        ["token_hash"],
        unique=True,
    )

    op.create_table(
        "workspace_admin_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("target_membership_id", sa.Uuid(), nullable=True),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["target_membership_id"],
            ["workspace_memberships.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_admin_audit_workspace_created",
        "workspace_admin_audit_events",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_workspace_admin_audit_operation",
        "workspace_admin_audit_events",
        ["operation", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_admin_audit_operation", table_name="workspace_admin_audit_events")
    op.drop_index(
        "ix_workspace_admin_audit_workspace_created", table_name="workspace_admin_audit_events"
    )
    op.drop_table("workspace_admin_audit_events")
    op.drop_index("ix_workspace_invitations_token_hash", table_name="workspace_invitations")
    op.drop_index("ix_workspace_invitations_email_status", table_name="workspace_invitations")
    op.drop_index(
        "ix_workspace_invitations_workspace_status", table_name="workspace_invitations"
    )
    op.drop_table("workspace_invitations")
    with op.batch_alter_table("workspace_memberships") as batch_op:
        batch_op.drop_constraint(
            "fk_workspace_memberships_invited_by_users", type_="foreignkey"
        )
    op.drop_column("workspace_memberships", "permission_overrides")
    op.drop_column("workspace_memberships", "accepted_at")
    op.drop_column("workspace_memberships", "invited_at")
    op.drop_column("workspace_memberships", "invited_by")
