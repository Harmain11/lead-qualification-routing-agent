"""Make source-event to lead ownership unique.

Revision ID: 0019_unique_lead_source_event
Revises: 0018_resilience_leases
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_unique_lead_source_event"
down_revision: str | None = "0018_resilience_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_leads_source_event",
        "leads",
        ["source_event_id"],
        unique=True,
        sqlite_where=sa.text("source_event_id IS NOT NULL"),
        postgresql_where=sa.text("source_event_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_leads_source_event", table_name="leads")
