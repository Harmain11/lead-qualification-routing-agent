"""Multiple follow-ups, user company details, groups, and workspace messages."""

import sqlalchemy as sa
from alembic import op

revision = "0031_workspace_collaboration"
down_revision = "0030_user_account_profile"
branch_labels = None
depends_on = None


def upgrade():
    for name, size in [
        ("company_name", 255),
        ("company_website", 500),
        ("location", 255),
    ]:
        op.add_column("users", sa.Column(name, sa.String(size), nullable=True))
    with op.batch_alter_table("lead_follow_up_emails") as batch:
        batch.drop_constraint("uq_lead_follow_up_email_kind", type_="unique")
    op.create_index(
        "uq_lead_confirmation_kind",
        "lead_follow_up_emails",
        ["lead_id", "kind"],
        unique=True,
        sqlite_where=sa.text("kind != 'ai_follow_up'"),
        postgresql_where=sa.text("kind != 'ai_follow_up'"),
    )
    op.create_index(
        "uq_lead_active_follow_up",
        "lead_follow_up_emails",
        ["lead_id"],
        unique=True,
        sqlite_where=sa.text("kind = 'ai_follow_up' AND status = 'draft'"),
        postgresql_where=sa.text("kind = 'ai_follow_up' AND status = 'draft'"),
    )
    op.create_table(
        "workspace_groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("member_ids", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "name", name="uq_workspace_group_name"),
    )
    op.create_table(
        "workspace_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sender_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "recipient_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "lead_id",
            sa.Uuid(),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_message_workspace_recipient_created",
        "workspace_messages",
        ["workspace_id", "recipient_id", "created_at"],
    )


def downgrade():
    # Refuse lossy downgrade: the former schema cannot preserve repeated follow-ups.
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            "SELECT lead_id FROM lead_follow_up_emails GROUP BY lead_id, kind HAVING COUNT(*) > 1"
        )
    ).first()
    if duplicates:
        raise RuntimeError("Repeated follow-ups exist; downgrade would lose history.")
    op.drop_table("workspace_messages")
    op.drop_table("workspace_groups")
    op.drop_index("uq_lead_active_follow_up", table_name="lead_follow_up_emails")
    op.drop_index("uq_lead_confirmation_kind", table_name="lead_follow_up_emails")
    with op.batch_alter_table("lead_follow_up_emails") as batch:
        batch.create_unique_constraint(
            "uq_lead_follow_up_email_kind", ["lead_id", "kind"]
        )
    for name in ["location", "company_website", "company_name"]:
        op.drop_column("users", name)
