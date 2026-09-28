"""Add single-use email verification tokens for password signup."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_email_verification"
down_revision: str | None = "0021_google_auth_identities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing accounts predate email verification. Preserve their established access;
    # all newly created password accounts are explicitly pending until verified.
    op.execute(
        "UPDATE users SET email_verified = TRUE "
        "WHERE email_verified = FALSE AND status = 'active'"
    )
    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("workspace_name", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_email_verification_tokens_user_created",
        "email_verification_tokens",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_verification_tokens_user_created",
        table_name="email_verification_tokens",
    )
    op.drop_table("email_verification_tokens")
