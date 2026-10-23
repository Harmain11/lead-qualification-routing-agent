"""Persist private, workspace-scoped Corral Agent conversations."""

import sqlalchemy as sa
from alembic import op

revision = "0048_agent_chat_history"
down_revision = "0047_reasoning_cache_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_agent_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column(
            "title",
            sa.String(length=120),
            nullable=False,
            server_default="New conversation",
        ),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_agent_conversations_owner_updated",
        "workspace_agent_conversations",
        ["workspace_id", "actor_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_agent_conversations_owner_updated",
        table_name="workspace_agent_conversations",
    )
    op.drop_table("workspace_agent_conversations")
