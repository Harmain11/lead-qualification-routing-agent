"""Add durable source-event lifecycle fields."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_source_event_lifecycle"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("source_events", sa.Column("status", sa.String(length=32), nullable=True))
    op.add_column("source_events", sa.Column("processing_attempts", sa.Integer(), nullable=True))
    op.add_column("source_events", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column("source_events", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("source_events", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE source_events SET status = 'processed', processing_attempts = 1, updated_at = created_at")
    op.alter_column("source_events", "status", nullable=False, server_default="received")
    op.alter_column("source_events", "processing_attempts", nullable=False, server_default="0")
    op.alter_column("source_events", "updated_at", nullable=False)


def downgrade() -> None:
    op.drop_column("source_events", "updated_at")
    op.drop_column("source_events", "processed_at")
    op.drop_column("source_events", "error_message")
    op.drop_column("source_events", "processing_attempts")
    op.drop_column("source_events", "status")

