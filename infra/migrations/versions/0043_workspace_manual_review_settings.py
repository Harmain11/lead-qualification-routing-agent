"""Store workspace manual-review criteria alongside routing preferences."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0043_manual_review_settings"
down_revision = "0042_provider_lead_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_routing_configs",
        sa.Column(
            "manual_review_settings",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("workspace_routing_configs", "manual_review_settings")
