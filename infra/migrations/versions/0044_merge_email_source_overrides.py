"""Merge the email source override and workspace review settings branches."""

revision = "0044_merge_email_sources"
down_revision = ("0041_email_source_overrides", "0043_manual_review_settings")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
