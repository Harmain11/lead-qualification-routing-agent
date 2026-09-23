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
    op.create_foreign_key(
        "fk_qualification_results_icp_version",
        "qualification_results",
        "workspace_icp_versions",
        ["icp_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column("qualification_results", "disqualifier_evidence", server_default=None)
    op.alter_column("qualification_results", "icp_source", server_default=None)
    op.alter_column("qualification_results", "icp_profile_version", server_default=None)
    op.alter_column("qualification_results", "icp_configuration_hash", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        "fk_qualification_results_icp_version", "qualification_results", type_="foreignkey"
    )
    op.drop_column("qualification_results", "icp_configuration_hash")
    op.drop_column("qualification_results", "icp_profile_version")
    op.drop_column("qualification_results", "icp_source")
    op.drop_column("qualification_results", "icp_version_id")
    op.drop_column("qualification_results", "disqualifier_evidence")
    op.drop_index("ix_workspace_icp_workspace_updated", table_name="workspace_icp_versions")
    op.drop_index("ix_workspace_icp_workspace_status", table_name="workspace_icp_versions")
    op.drop_table("workspace_icp_versions")
