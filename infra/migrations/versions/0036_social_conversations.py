"""Add normalized social conversations and replay-safe message history."""

import sqlalchemy as sa
from alembic import op

revision = "0036_social_conversations"
down_revision = "0035_lead_calls"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "social_conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id", sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "connection_id", sa.Uuid(),
            sa.ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("external_account_id", sa.String(255), nullable=False),
        sa.Column("participant_id", sa.String(255), nullable=False),
        sa.Column("conversation_identity", sa.String(255), nullable=False),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "provider", "connection_id", "external_account_id",
            "participant_id", "conversation_identity",
            name="uq_social_conversation_identity",
        ),
    )
    op.create_table(
        "social_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id", sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "connection_id", sa.Uuid(),
            sa.ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "conversation_id", sa.Uuid(),
            sa.ForeignKey("social_conversations.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("provider_event_id", sa.String(255), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="SET NULL")),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "connection_id", "provider_event_id", name="uq_social_message_replay"
        ),
    )
    op.create_index(
        "ix_social_messages_conversation_event",
        "social_messages",
        ["conversation_id", "event_at"],
    )


def downgrade():
    op.drop_table("social_messages")
    op.drop_table("social_conversations")
