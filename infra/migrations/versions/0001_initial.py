"""Create the Phase 1 lead processing schema.

Revision ID: 0001_initial
Revises:
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    uuid_type = sa.Uuid(as_uuid=True)
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "source_events",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("received_at", timestamp, nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.UniqueConstraint("provider", "external_event_id", name="uq_source_provider_external"),
    )
    op.create_table(
        "leads",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("source_event_id", uuid_type, sa.ForeignKey("source_events.id"), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_detail", sa.String(length=255), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("job_title", sa.String(length=255), nullable=True),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("industry", sa.String(length=255), nullable=True),
        sa.Column("employee_count", sa.Integer(), nullable=True),
        sa.Column("annual_revenue", sa.Numeric(14, 2), nullable=True),
        sa.Column("geography", sa.String(length=255), nullable=True),
        sa.Column("budget", sa.Numeric(14, 2), nullable=True),
        sa.Column("product_or_service", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("received_at", timestamp, nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("normalization_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
    )
    op.create_index("ix_leads_source_created", "leads", ["source", "created_at"])
    op.create_index("ix_leads_email", "leads", ["email"])
    op.create_table(
        "qualification_results",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("lead_id", uuid_type, sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("total_score", sa.Integer(), nullable=False),
        sa.Column("classification", sa.String(length=64), nullable=False),
        sa.Column("routing_queue", sa.String(length=128), nullable=False),
        sa.Column("routing_action", sa.String(length=128), nullable=False),
        sa.Column("hard_disqualified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("disqualifier_code", sa.String(length=128), nullable=True),
        sa.Column("rationale", sa.JSON(), nullable=False),
        sa.Column("score_breakdown", sa.JSON(), nullable=False),
        sa.Column("component_scores", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default="1.0"),
        sa.Column("created_at", timestamp, nullable=False),
        sa.UniqueConstraint("lead_id", name="uq_qualification_lead"),
    )
    op.create_index("ix_qualification_classification", "qualification_results", ["classification"])
    op.create_table(
        "outbox_messages",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("aggregate_id", uuid_type, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("delivered_at", timestamp, nullable=True),
    )
    op.create_index("ix_outbox_status_created", "outbox_messages", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_outbox_status_created", table_name="outbox_messages")
    op.drop_table("outbox_messages")
    op.drop_index("ix_qualification_classification", table_name="qualification_results")
    op.drop_table("qualification_results")
    op.drop_index("ix_leads_email", table_name="leads")
    op.drop_index("ix_leads_source_created", table_name="leads")
    op.drop_table("leads")
    op.drop_table("source_events")
