"""Persist structured Phase 3 context analysis.

Revision ID: 0003_context_analysis
Revises: 0002_source_event_lifecycle
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_context_analysis"
down_revision: Union[str, None] = "0002_source_event_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "lead_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=48), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("requires_manual_review", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("structured_output", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("lead_id", name="uq_analysis_lead"),
    )
    op.create_index("ix_analysis_status_created", "analysis_runs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_analysis_status_created", table_name="analysis_runs")
    op.drop_table("analysis_runs")
