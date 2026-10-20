from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Uuid,
    create_engine,
    select,
)
from sqlalchemy.orm import Session

from lead_agent.api.routes.integrations import (
    LeadApprovalPolicyUpdate,
    set_lead_approval_policy,
)
from lead_agent.application.lead_approval import (
    lead_approval_policy,
    update_lead_approval_policy,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    ProviderConnectionORM,
    WorkspaceAdminAuditEventORM,
)
from lead_agent.infrastructure.db.operations import (
    WorkspaceProviderLeadApprovalPolicyORM,
)
from lead_agent.infrastructure.repositories import ensure_workspace


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def _verified_connection(session: Session, workspace_id, provider: str) -> None:
    ensure_workspace(session, workspace_id, name="Approval policy tests")
    now = datetime.now(UTC)
    session.add(
        ProviderConnectionORM(
            id=uuid4(),
            workspace_id=workspace_id,
            provider_type=provider,
            external_account_id=f"{provider}-account",
            account_name=f"{provider} account",
            auth_type="oauth2",
            status="verified",
            scopes=[],
            secret_reference="test-reference",
            credential_state="configured",
            last_validation_result={"verified": True},
            connection_metadata={},
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )
    session.commit()


def test_provider_policies_are_independent_and_default_off() -> None:
    with _session() as session:
        workspace_id = uuid4()
        ensure_workspace(session, workspace_id, name="Approval policy tests")

        assert lead_approval_policy(session, workspace_id, "gmail") == (False, "default", None)
        update_lead_approval_policy(
            session, workspace_id, "gmail", True, str(uuid4())
        )
        session.commit()

        assert lead_approval_policy(session, workspace_id, "gmail")[0] is True
        assert lead_approval_policy(session, workspace_id, "hubspot")[0] is False
        assert lead_approval_policy(session, workspace_id, "salesforce")[0] is False
        audit = session.scalar(
            select(WorkspaceAdminAuditEventORM).where(
                WorkspaceAdminAuditEventORM.workspace_id == workspace_id
            )
        )
        assert audit is not None
        assert audit.operation == "integration_lead_approval_policy_updated"
        assert audit.before["auto_approve_leads"] is False
        assert audit.after == {
            "provider": "gmail",
            "auto_approve_leads": True,
            "source": "user",
        }


def test_enabling_requires_permissions_and_an_active_verified_provider() -> None:
    with _session() as session:
        workspace_id = uuid4()
        ensure_workspace(session, workspace_id, name="Approval policy tests")
        request = LeadApprovalPolicyUpdate(auto_approve_leads=True)

        with pytest.raises(HTTPException) as permission_error:
            set_lead_approval_policy(
                "salesforce",
                request,
                session,
                WorkspaceContext(
                    workspace_id=workspace_id,
                    actor_id=str(uuid4()),
                    role="admin",
                    permissions=frozenset({"integrations.manage"}),
                ),
            )
        assert permission_error.value.status_code == 403

        with pytest.raises(HTTPException) as connection_error:
            set_lead_approval_policy(
                "salesforce",
                request,
                session,
                WorkspaceContext(
                    workspace_id=workspace_id,
                    actor_id=str(uuid4()),
                    role="admin",
                    permissions=frozenset(
                        {"integrations.manage", "manual_review.manage"}
                    ),
                ),
            )
        assert connection_error.value.status_code == 409

        _verified_connection(session, workspace_id, "salesforce")
        response = set_lead_approval_policy(
            "salesforce",
            request,
            session,
            WorkspaceContext(
                workspace_id=workspace_id,
                actor_id=str(uuid4()),
                role="admin",
                permissions=frozenset(
                    {"integrations.manage", "manual_review.manage"}
                ),
            ),
        )
        assert response.auto_approve_leads is True
        assert response.verified_connection is True


def test_legacy_workspace_setting_migrates_only_to_verified_configured_providers() -> None:
    migration_path = (
        Path(__file__).resolve().parents[3]
        / "infra"
        / "migrations"
        / "versions"
        / "0042_provider_lead_approval.py"
    )
    spec = importlib.util.spec_from_file_location(
        "provider_lead_approval_migration", migration_path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine("sqlite://")
    metadata = MetaData()
    Table("workspaces", metadata, Column("id", Uuid(), primary_key=True))
    Table(
        "workspace_operations_policies",
        metadata,
        Column("workspace_id", Uuid(), primary_key=True),
        Column("configuration", JSON(), nullable=False),
    )
    Table(
        "provider_connections",
        metadata,
        Column("id", Uuid(), primary_key=True),
        Column("workspace_id", Uuid(), nullable=False),
        Column("provider_type", String(64), nullable=False),
        Column("is_active", Boolean(), nullable=False),
        Column("status", String(32), nullable=False),
    )
    Table(
        "workspace_admin_audit_events",
        metadata,
        Column("id", Uuid(), primary_key=True),
        Column("workspace_id", Uuid(), nullable=False),
        Column("actor_id", Uuid(), nullable=True),
        Column("target_membership_id", Uuid(), nullable=True),
        Column("operation", String(64), nullable=False),
        Column("before", JSON(), nullable=False),
        Column("after", JSON(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)
    workspace_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            metadata.tables["workspaces"].insert().values(id=workspace_id)
        )
        connection.execute(
            metadata.tables["workspace_operations_policies"].insert().values(
                workspace_id=workspace_id,
                configuration={"auto_approve_leads": True, "required_region": "any"},
            )
        )
        connection.execute(
            metadata.tables["provider_connections"].insert(),
            [
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "provider_type": "gmail",
                    "is_active": True,
                    "status": "verified",
                },
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "provider_type": "hubspot",
                    "is_active": True,
                    "status": "verified",
                },
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "provider_type": "outlook",
                    "is_active": False,
                    "status": "disconnected",
                },
            ],
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

        migrated_policies = Table(
            "workspace_provider_lead_approval_policies",
            MetaData(),
            autoload_with=connection,
        )
        rows = connection.execute(
            select(
                migrated_policies.c.provider,
                migrated_policies.c.auto_approve_leads,
            )
        ).all()
        assert {provider: enabled for provider, enabled in rows} == {
            "gmail": True,
            "hubspot": True,
        }
        config = connection.execute(
            select(metadata.tables["workspace_operations_policies"].c.configuration)
        ).scalar_one()
        assert config == {"required_region": "any"}
        assert "outlook" not in {
            provider
            for (provider,) in connection.execute(
                select(WorkspaceProviderLeadApprovalPolicyORM.provider)
            )
        }
