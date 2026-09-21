"""Add workspace analytics query indexes.

Revision ID: 0009_analytics_query_indexes
Revises: 0008_analytics_read_model
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_analytics_query_indexes"
down_revision: str | None = "0008_analytics_read_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_leads_workspace_received",
        "leads",
        ["workspace_id", "received_at"],
    )
    op.create_index(
        "ix_pipeline_events_workspace_created",
        "pipeline_events",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_events_workspace_created", table_name="pipeline_events")
    op.drop_index("ix_leads_workspace_received", table_name="leads")
