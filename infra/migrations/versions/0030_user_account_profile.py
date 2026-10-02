"""Add user-owned profile details and image data."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_user_account_profile"
down_revision: str | None = "0029_workspace_email_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("job_title", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("phone", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("bio", sa.String(length=1000), nullable=True))
    op.add_column("users", sa.Column("avatar_data_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_data_url")
    op.drop_column("users", "bio")
    op.drop_column("users", "phone")
    op.drop_column("users", "job_title")