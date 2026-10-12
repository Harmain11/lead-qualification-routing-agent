"""Add durable, scoped call receipts and explicit dial-request deduplication."""

import sqlalchemy as sa
from alembic import op

revision = "0035_lead_calls"
down_revision = "0034_operations_leases"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lead_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lead_id",
            sa.Uuid(),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("provider_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("request_id", sa.Uuid()),
        sa.Column("request_fingerprint", sa.String(64)),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(64), nullable=False),
        sa.Column("customer_phone", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("transcript", sa.Text()),
        sa.Column("recording_url", sa.Text()),
        sa.Column("message", sa.Text()),
        sa.Column("actor_id", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "request_id", name="uq_call_request"),
        sa.UniqueConstraint(
            "connection_id", "external_id", name="uq_call_provider_receipt"
        ),
    )
    op.create_index("ix_lead_calls_workspace_id", "lead_calls", ["workspace_id"])
    op.create_index("ix_lead_calls_lead_id", "lead_calls", ["lead_id"])


def downgrade():
    op.drop_table("lead_calls")
