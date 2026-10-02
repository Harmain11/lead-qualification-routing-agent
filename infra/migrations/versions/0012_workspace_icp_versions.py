"""Add workspace-scoped versioned ICP configuration and qualification traceability.

Revision ID: 0012_workspace_icp_versions
Revises: 0011_workspace_rbac
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_workspace_icp_versions"
down_revision: str | None = "0011_workspace_rbac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_icp_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_by", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "version_number", name="uq_workspace_icp_version"),
    )
    op.create_index(
        "ix_workspace_icp_workspace_status",
        "workspace_icp_versions",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_workspace_icp_workspace_updated",
        "workspace_icp_versions",
        ["workspace_id", "updated_at"],
    )
    op.add_column(
        "qualification_results",
        sa.Column(
            "disqualifier_evidence", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
    )
    op.add_column(
        "qualification_results", sa.Column("icp_version_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "qualification_results",
        sa.Column(
            "icp_source",
            sa.String(length=32),
            nullable=False,
            server_default="system_default",
        ),
    )
    op.add_column(
        "qualification_results",
        sa.Column(
            "icp_profile_version",
            sa.String(length=64),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.add_column(
        "qualification_results",
        sa.Column(
            "icp_configuration_hash",
            sa.String(length=64),
            nullable=False,
            server_default="legacy",
        ),
    )
    with op.batch_alter_table("qualification_results") as batch_op:
        batch_op.create_foreign_key(
            "fk_qualification_results_icp_version",
            "workspace_icp_versions",
            ["icp_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.alter_column("disqualifier_evidence", server_default=None)
        batch_op.alter_column("icp_source", server_default=None)
        batch_op.alter_column("icp_profile_version", server_default=None)
        batch_op.alter_column("icp_configuration_hash", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("qualification_results") as batch_op:
        batch_op.drop_constraint("fk_qualification_results_icp_version", type_="foreignkey")
        batch_op.drop_column("icp_configuration_hash")
        batch_op.drop_column("icp_profile_version")
        batch_op.drop_column("icp_source")
        batch_op.drop_column("icp_version_id")
        batch_op.drop_column("disqualifier_evidence")
    op.drop_index("ix_workspace_icp_workspace_updated", table_name="workspace_icp_versions")
    op.drop_index("ix_workspace_icp_workspace_status", table_name="workspace_icp_versions")
    op.drop_table("workspace_icp_versions")
