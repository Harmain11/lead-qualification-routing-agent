"""Add Google login identity mappings and isolated auth OAuth state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_google_auth_identities"
down_revision: str | None = "0020_outlook_mailbox_reconnect"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("users", "email_verified", server_default=None)
    op.create_table(
        "external_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "subject", name="uq_external_identity_provider_subject"),
    )
    op.create_index(
        "ix_external_identities_user_provider", "external_identities", ["user_id", "provider"]
    )
    op.create_table(
        "auth_oauth_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("code_verifier", sa.String(length=256), nullable=False),
        sa.Column("redirect_uri", sa.String(length=1024), nullable=False),
        sa.Column("invitation_token", sa.String(length=512), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash"),
    )
    op.create_index("ix_auth_oauth_states_expires", "auth_oauth_states", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_oauth_states_expires", table_name="auth_oauth_states")
    op.drop_table("auth_oauth_states")
    op.drop_index("ix_external_identities_user_provider", table_name="external_identities")
    op.drop_table("external_identities")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "email_verified")
