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
    # SQLite cannot alter a column's nullability/default in place. Batch mode
    # recreates the table on SQLite while preserving the equivalent ALTER
    # behavior on PostgreSQL.
    with op.batch_alter_table("source_events") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=32),
            existing_nullable=True,
            nullable=False,
            server_default="received",
        )
        batch_op.alter_column(
            "processing_attempts",
            existing_type=sa.Integer(),
            existing_nullable=True,
            nullable=False,
            server_default="0",
        )
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=True,
            nullable=False,
        )


def downgrade() -> None:
    op.drop_column("source_events", "updated_at")
    op.drop_column("source_events", "processed_at")
    op.drop_column("source_events", "error_message")
    op.drop_column("source_events", "processing_attempts")
    op.drop_column("source_events", "status")

