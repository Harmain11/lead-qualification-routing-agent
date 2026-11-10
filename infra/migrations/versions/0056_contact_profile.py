"""Optional extra details on contacts (LinkedIn, department, notes, tags and similar)."""

import sqlalchemy as sa
from alembic import op

revision = "0056_contact_profile"
down_revision = "0055_password_reset_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column("profile", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("contacts", "profile")
