"""Store lead auto-approval per workspace and inbound provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0042_provider_lead_approval"
down_revision = "0041_direct_calls"
branch_labels = None
depends_on = None

_INBOUND_PROVIDERS = (
    "gmail",
    "outlook",
    "salesforce",
    "hubspot",
    "gohighlevel",
    "linkedin",
    "instagram",
    "messenger",
    "whatsapp_business",
    "clutch",
)


def upgrade() -> None:
    op.create_table(
        "workspace_provider_lead_approval_policies",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("auto_approve_leads", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="user"),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id", "provider"),
    )
    op.create_index(
        "ix_provider_lead_approval_workspace",
        "workspace_provider_lead_approval_policies",
        ["workspace_id"],
        unique=False,
    )
    _migrate_legacy_workspace_setting()


def _migrate_legacy_workspace_setting() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    required = {
        "workspace_operations_policies",
        "provider_connections",
        "workspace_admin_audit_events",
    }
    if not required.issubset(tables):
        return

    metadata = sa.MetaData()
    operations = sa.Table("workspace_operations_policies", metadata, autoload_with=bind)
    connections = sa.Table("provider_connections", metadata, autoload_with=bind)
    audit = sa.Table("workspace_admin_audit_events", metadata, autoload_with=bind)
    policy = sa.Table(
        "workspace_provider_lead_approval_policies", metadata, autoload_with=bind
    )

    for row in bind.execute(sa.select(operations.c.workspace_id, operations.c.configuration)):
        raw_configuration = row.configuration
        if isinstance(raw_configuration, str):
            try:
                configuration = json.loads(raw_configuration)
            except ValueError:
                continue
        else:
            configuration = raw_configuration
        if (
            not isinstance(configuration, dict)
            or type(configuration.get("auto_approve_leads")) is not bool
        ):
            continue

        legacy_value = configuration.pop("auto_approve_leads")
        workspace_id = row.workspace_id
        connected = bind.execute(
            sa.select(connections.c.provider_type)
            .where(
                connections.c.workspace_id == workspace_id,
                connections.c.provider_type.in_(_INBOUND_PROVIDERS),
                connections.c.is_active.is_(True),
                connections.c.status == "verified",
            )
            .distinct()
        )
        for provider_row in connected:
            provider = provider_row.provider_type
            bind.execute(
                policy.insert().values(
                    workspace_id=workspace_id,
                    provider=provider,
                    auto_approve_leads=legacy_value,
                    source="legacy_migration",
                    updated_by=None,
                    updated_at=datetime.now(UTC),
                )
            )
            audit_id: object = uuid4()
            if not isinstance(audit.c.id.type, sa.Uuid):
                audit_id = str(audit_id)
            bind.execute(
                audit.insert().values(
                    id=audit_id,
                    workspace_id=workspace_id,
                    actor_id=None,
                    target_membership_id=None,
                    operation="integration_lead_approval_policy_migrated",
                    before={
                        "scope": "workspace",
                        "auto_approve_leads": legacy_value,
                    },
                    after={
                        "scope": "provider",
                        "provider": provider,
                        "auto_approve_leads": legacy_value,
                        "source": "legacy_migration",
                    },
                    created_at=datetime.now(UTC),
                )
            )

        bind.execute(
            operations.update()
            .where(operations.c.workspace_id == workspace_id)
            .values(configuration=configuration)
        )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_lead_approval_workspace",
        table_name="workspace_provider_lead_approval_policies",
    )
    op.drop_table("workspace_provider_lead_approval_policies")
