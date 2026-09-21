"""Add durable routing, action-plan, notification, and pipeline history records.

Revision ID: 0004_routing_notifications
Revises: 0003_context_analysis
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_routing_notifications"
down_revision: Union[str, None] = "0003_context_analysis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    uuid_type = sa.Uuid(as_uuid=True)
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "routing_decisions",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("lead_id", uuid_type, sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "qualification_id",
            uuid_type,
            sa.ForeignKey("qualification_results.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("qualification_band", sa.String(length=64), nullable=False),
        sa.Column("assigned_queue", sa.String(length=128), nullable=False),
        sa.Column("assigned_rep_id", sa.String(length=128), nullable=True),
        sa.Column("assigned_rep_name", sa.String(length=255), nullable=True),
        sa.Column("routing_reason", sa.Text(), nullable=False),
        sa.Column("matched_rules", sa.JSON(), nullable=False),
        sa.Column("action_plan", sa.JSON(), nullable=False),
        sa.Column("priority", sa.String(length=32), nullable=False),
        sa.Column("sla_target_at", timestamp, nullable=True),
        sa.Column("notification_state", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("manual_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", timestamp, nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_routing_idempotency"),
    )
    op.create_index("ix_routing_lead_current", "routing_decisions", ["lead_id", "is_current"])
    op.create_index("ix_routing_queue_created", "routing_decisions", ["assigned_queue", "created_at"])

    op.create_table(
        "notifications",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "routing_decision_id",
            uuid_type,
            sa.ForeignKey("routing_decisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("sent_at", timestamp, nullable=True),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.UniqueConstraint(
            "routing_decision_id", "provider", name="uq_notification_decision_provider"
        ),
    )
    op.create_index("ix_notifications_status_updated", "notifications", ["status", "updated_at"])

    op.create_table(
        "pipeline_events",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("lead_id", uuid_type, sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
    )
    op.create_index("ix_pipeline_lead_created", "pipeline_events", ["lead_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_lead_created", table_name="pipeline_events")
    op.drop_table("pipeline_events")
    op.drop_index("ix_notifications_status_updated", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_routing_queue_created", table_name="routing_decisions")
    op.drop_index("ix_routing_lead_current", table_name="routing_decisions")
    op.drop_table("routing_decisions")
