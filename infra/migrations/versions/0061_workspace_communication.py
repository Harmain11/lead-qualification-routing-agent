"""Add native workspace communication foundation.

Revision ID: 0061_workspace_communication
Revises: 0060_lead_read_receipts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061_workspace_communication"
down_revision: str | None = "0060_lead_read_receipts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "communication_projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_communication_projects_workspace_updated",
        "communication_projects",
        ["workspace_id", "updated_at"],
    )
    op.create_table(
        "communication_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("primary_lead_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["communication_projects.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["primary_lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_communication_conversations_workspace_updated",
        "communication_conversations",
        ["workspace_id", "updated_at"],
    )
    op.create_index(
        "ix_communication_conversations_project",
        "communication_conversations",
        ["project_id", "updated_at"],
    )
    op.create_table(
        "communication_conversation_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["communication_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id", "user_id", name="uq_communication_conversation_member"
        ),
    )
    op.create_index(
        "ix_communication_members_user_conversation",
        "communication_conversation_members",
        ["user_id", "conversation_id"],
    )
    op.create_table(
        "communication_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("parent_message_id", sa.Uuid(), nullable=True),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("plaintext", sa.Text(), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["communication_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_message_id"], ["communication_messages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_communication_messages_conversation_created",
        "communication_messages",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_communication_messages_parent_created",
        "communication_messages",
        ["parent_message_id", "created_at"],
    )
    op.create_index(
        "ix_communication_messages_workspace_created",
        "communication_messages",
        ["workspace_id", "created_at"],
    )
    op.create_table(
        "communication_message_mentions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"], ["communication_messages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id", "user_id", name="uq_communication_message_mention"
        ),
    )
    op.create_index(
        "ix_communication_mentions_user_message",
        "communication_message_mentions",
        ["user_id", "message_id"],
    )
    op.create_table(
        "communication_message_leads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"], ["communication_messages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id", "lead_id", name="uq_communication_message_lead"
        ),
    )
    op.create_index(
        "ix_communication_message_leads_lead",
        "communication_message_leads",
        ["lead_id", "message_id"],
    )
    op.create_table(
        "communication_message_pins",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("pinned_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["communication_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["communication_messages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["pinned_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", name="uq_communication_message_pin"),
    )
    op.create_index(
        "ix_communication_pins_conversation_created",
        "communication_message_pins",
        ["conversation_id", "created_at"],
    )
    op.create_table(
        "communication_notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["recipient_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["message_id"], ["communication_messages.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recipient_id", "message_id", "kind", name="uq_communication_notification"
        ),
    )
    op.create_index(
        "ix_communication_notifications_recipient_created",
        "communication_notifications",
        ["recipient_id", "created_at"],
    )
    op.create_table(
        "communication_reminders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["communication_messages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["recipient_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_communication_reminders_recipient_due",
        "communication_reminders",
        ["recipient_id", "due_at"],
    )
    op.create_table(
        "communication_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["communication_messages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_communication_attachment_storage_key"),
    )
    op.create_index(
        "ix_communication_attachments_message",
        "communication_attachments",
        ["message_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_communication_attachments_message", table_name="communication_attachments"
    )
    op.drop_table("communication_attachments")
    op.drop_index(
        "ix_communication_reminders_recipient_due", table_name="communication_reminders"
    )
    op.drop_table("communication_reminders")
    op.drop_index(
        "ix_communication_notifications_recipient_created",
        table_name="communication_notifications",
    )
    op.drop_table("communication_notifications")
    for index, table in (
        ("ix_communication_pins_conversation_created", "communication_message_pins"),
    ):
        op.drop_index(index, table_name=table)
    op.drop_table("communication_message_pins")
    op.drop_index(
        "ix_communication_message_leads_lead", table_name="communication_message_leads"
    )
    op.drop_table("communication_message_leads")
    op.drop_index(
        "ix_communication_mentions_user_message",
        table_name="communication_message_mentions",
    )
    op.drop_table("communication_message_mentions")
    for index in (
        "ix_communication_messages_workspace_created",
        "ix_communication_messages_parent_created",
        "ix_communication_messages_conversation_created",
    ):
        op.drop_index(index, table_name="communication_messages")
    op.drop_table("communication_messages")
    op.drop_index(
        "ix_communication_members_user_conversation",
        table_name="communication_conversation_members",
    )
    op.drop_table("communication_conversation_members")
    for index in (
        "ix_communication_conversations_project",
        "ix_communication_conversations_workspace_updated",
    ):
        op.drop_index(index, table_name="communication_conversations")
    op.drop_table("communication_conversations")
    op.drop_index(
        "ix_communication_projects_workspace_updated",
        table_name="communication_projects",
    )
    op.drop_table("communication_projects")
