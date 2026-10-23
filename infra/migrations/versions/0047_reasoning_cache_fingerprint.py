"""Store exact-input fingerprints alongside lead analyses."""

import sqlalchemy as sa
from alembic import op

revision = "0047_reasoning_cache_fingerprint"
down_revision = "0046_merge_assign_notif"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("analysis_request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_analysis_request_fingerprint",
        "analysis_runs",
        ["analysis_request_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_request_fingerprint", table_name="analysis_runs")
    op.drop_column("analysis_runs", "analysis_request_fingerprint")
