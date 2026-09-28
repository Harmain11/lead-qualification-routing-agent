"""Enforce a single active Outlook connection per workspace mailbox.

Revision ID: 0020_outlook_mailbox_reconnect
Revises: 0019_unique_lead_source_event
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_outlook_mailbox_reconnect"
down_revision: str | None = "0019_unique_lead_source_event"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_outlook_active_workspace_mailbox",
        "provider_connections",
        ["workspace_id", "external_account_id"],
        unique=True,
        sqlite_where=sa.text(
            "provider_type = 'outlook' AND is_active = 1 AND external_account_id IS NOT NULL"
        ),
        postgresql_where=sa.text(
            "provider_type = 'outlook' AND is_active IS TRUE AND external_account_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_outlook_active_workspace_mailbox", table_name="provider_connections")
