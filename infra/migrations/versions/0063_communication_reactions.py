"""Add reactions to workspace messages.

Revision ID: 0063_communication_reactions
Revises: 0062_communication_tasks
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0063_communication_reactions"
down_revision: str | None = "0062_communication_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("communication_attachments", sa.Column("client_upload_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint("uq_communication_attachment_upload", "communication_attachments", ["message_id", "client_upload_id"])
    op.create_table(
        "communication_preferences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sound_enabled", sa.Boolean(), nullable=False),
        sa.Column("muted_conversation_ids", sa.JSON(), nullable=False),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_communication_preference"),
    )
    op.create_table(
        "communication_saved_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("communication_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("message_id", "user_id", name="uq_communication_saved_message"),
    )
    op.create_index("ix_communication_saved_user", "communication_saved_messages", ["user_id", "created_at"])
    op.create_table(
        "communication_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("communication_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("conversation_id", "user_id", name="uq_communication_draft"),
    )
    op.create_table(
        "communication_message_reactions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("communication_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reaction", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("message_id", "user_id", "reaction", name="uq_communication_reaction"),
    )
    op.create_index("ix_communication_reactions_message", "communication_message_reactions", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_communication_reactions_message", table_name="communication_message_reactions")
    op.drop_table("communication_message_reactions")
    op.drop_table("communication_drafts")
    op.drop_index("ix_communication_saved_user", table_name="communication_saved_messages")
    op.drop_table("communication_saved_messages")
    op.drop_table("communication_preferences")
    op.drop_constraint("uq_communication_attachment_upload", "communication_attachments", type_="unique")
    op.drop_column("communication_attachments", "client_upload_id")
