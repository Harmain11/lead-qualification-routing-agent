from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from lead_agent.api.main import app
from lead_agent.application.crm import sync_lead_to_crm
from lead_agent.application.integrations import create_manual_connection, disconnect_connection
from lead_agent.application.services import process_lead
from lead_agent.application.tools import (
    CrmCreateOrUpdateLeadTool,
    ToolPermissionError,
    ToolRegistry,
)
from lead_agent.domain.integrations import ConnectionValidation, ManualConnectionInput
from lead_agent.domain.tools import ToolContext
from lead_agent.infrastructure.crm.factory import (
    WorkspaceCrmProviderResolver,
    build_connected_crm_provider,
)
from lead_agent.infrastructure.crm.mock import MockCrmProvider
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    AgentToolAuditORM,
    CrmSyncORM,
    ProviderConnectionORM,
    SourceEventORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.oauth import DevelopmentOAuthBroker, get_oauth_broker
from lead_agent.infrastructure.provider_lifecycle import get_provider_connection_validator
from lead_agent.infrastructure.repositories import (
    get_current_crm_sync,
    get_lead,
    get_provider_connection,
    list_crm_sync_history,
    list_provider_connections,
)
from lead_agent.infrastructure.secrets import DevelopmentSecretStore, get_secret_store
from lead_agent.settings.config import Settings


def session_for(tmp_path, *, threads: bool = False) -> Session:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workspace.db'}",
        connect_args={"check_same_thread": False} if threads else {},
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def payload(external_id: str) -> dict[str, object]:
    return {
        "external_id": external_id,
        "source": "website",
        "source_detail": "workspace-test",
        "full_name": "Workspace Buyer",
        "email": f"{external_id}@example.test",
        "job_title": "VP Operations",
        "company_name": "Tenant Safe Systems",
        "industry": "SaaS",
        "employee_count": 150,
        "geography": "United States",
        "budget": 25000,
        "product_or_service": "Lead routing",
        "message": "We need a proposal this month for an API-connected routing workflow.",
    }


def manual_request(provider: str, credentials: dict[str, str]) -> ManualConnectionInput:
    return ManualConnectionInput(
        account_name=f"{provider.title()} test account",
        external_account_id=f"{provider}-account",
        auth_type="development" if provider == "mock" else "private_token",
        scopes=["crm.write"],
        credentials=credentials,
    )


def test_source_and_lead_identity_are_scoped_by_workspace(icp, routing, tmp_path) -> None:
    workspace_a, workspace_b = uuid4(), uuid4()
    with session_for(tmp_path) as session:
        _, _, lead_a = process_lead(
            session,
            payload("shared-event"),
            icp,
            routing,
            workspace_id=workspace_a,
        )
        _, _, lead_b = process_lead(
            session,
            payload("shared-event"),
            icp,
            routing,
            workspace_id=workspace_b,
        )
        assert lead_a.id != lead_b.id
        assert session.scalar(select(func.count()).select_from(SourceEventORM)) == 2
        assert get_lead(session, lead_a.id, workspace_b) is None
        assert get_current_crm_sync(session, lead_a.id, workspace_id=workspace_b) is None
        assert list_crm_sync_history(session, lead_a.id, workspace_b) == []


def test_provider_connection_repository_enforces_workspace_ownership(tmp_path) -> None:
    workspace_a, workspace_b = uuid4(), uuid4()
    store = DevelopmentSecretStore()
    with session_for(tmp_path) as session:
        connection = create_manual_connection(
            session,
            workspace_a,
            "mock",
            manual_request("mock", {"mode": "deterministic"}),
            store,
        )
        assert get_provider_connection(session, workspace_a, connection.id) is not None
        assert get_provider_connection(session, workspace_b, connection.id) is None
        assert list_provider_connections(session, workspace_b) == []


def test_disconnect_stays_local_when_remote_revocation_fails(tmp_path) -> None:
    workspace = uuid4()
    store = DevelopmentSecretStore()

    class FailingRevocationBroker:
        def revoke(self, provider, values):
            raise RuntimeError("remote provider unavailable")

    with session_for(tmp_path) as session:
        connection = create_manual_connection(
            session,
            workspace,
            "hubspot",
            manual_request("hubspot", {"access_token": "disconnect-me"}),
            store,
        )
        reference = connection.secret_reference
        disconnected = disconnect_connection(
            session,
            workspace,
            connection.id,
            store,
            FailingRevocationBroker(),  # type: ignore[arg-type]
        )
        assert disconnected.status == "disconnected"
        assert disconnected.is_active is False
        assert disconnected.secret_reference is None
        assert disconnected.last_validation_result["remote_revocation"] == "failed"
        assert store.retrieve(reference) is None


def test_workspace_resolver_selects_each_workspaces_active_crm(tmp_path, repository_root) -> None:
    workspace_a, workspace_b = uuid4(), uuid4()
    store = DevelopmentSecretStore()
    settings = Settings(crm_config_path=str(repository_root / "config" / "crm.example.yaml"))
    resolver = WorkspaceCrmProviderResolver(settings, store)
    with session_for(tmp_path) as session:
        hubspot = create_manual_connection(
            session,
            workspace_a,
            "hubspot",
            manual_request("hubspot", {"access_token": "test-hubspot-token"}),
            store,
        )
        salesforce = create_manual_connection(
            session,
            workspace_b,
            "salesforce",
            manual_request(
                "salesforce",
                {
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                    "refresh_token": "test-refresh",
                },
            ),
            store,
        )
        provider_a = resolver.resolve(session, workspace_a)
        provider_b = resolver.resolve(session, workspace_b)
        assert provider_a.provider_name == "hubspot"
        assert provider_a.connection_id == hubspot.id
        assert provider_b.provider_name == "salesforce"
        assert provider_b.connection_id == salesforce.id


def test_crm_connections_remain_active_across_providers(tmp_path, repository_root) -> None:
    workspace = uuid4()
    store = DevelopmentSecretStore()
    resolver = WorkspaceCrmProviderResolver(
        Settings(crm_config_path=str(repository_root / "config" / "crm.example.yaml")),
        store,
    )
    with session_for(tmp_path) as session:
        salesforce = create_manual_connection(
            session,
            workspace,
            "salesforce",
            manual_request(
                "salesforce",
                {
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                    "refresh_token": "test-refresh",
                },
            ),
            store,
        )
        hubspot = create_manual_connection(
            session,
            workspace,
            "hubspot",
            manual_request("hubspot", {"access_token": "test-hubspot-token"}),
            store,
        )

        session.refresh(salesforce)
        assert salesforce.is_active is True
        assert hubspot.is_active is True
        assert (
            resolver.resolve(session, workspace, provider_type="salesforce").connection_id
            == salesforce.id
        )
        assert (
            resolver.resolve(session, workspace, provider_type="hubspot").connection_id
            == hubspot.id
        )
        unavailable = resolver.resolve(session, uuid4(), provider_type="hubspot")
        assert unavailable.provider_name == "hubspot"
        assert unavailable.connection_id == UUID(int=0)


def test_production_resolver_never_selects_workspace_mock_connection(
    tmp_path, repository_root
) -> None:
    workspace = uuid4()
    store = DevelopmentSecretStore()
    settings = Settings(
        app_env="development",
        crm_config_path=str(repository_root / "config" / "crm.example.yaml"),
        _env_file=None,
    )
    with session_for(tmp_path) as session:
        create_manual_connection(
            session,
            workspace,
            "mock",
            manual_request("mock", {"mode": "deterministic"}),
            store,
        )
        fallback = MockCrmProvider()
        settings.app_env = "production"
        resolver = WorkspaceCrmProviderResolver(settings, store)
        assert resolver.resolve(session, workspace, fallback) is not fallback


def test_connected_crm_endpoints_cannot_be_overridden_by_workspace_credentials(
    repository_root,
) -> None:
    settings = Settings(
        crm_config_path=str(repository_root / "config" / "crm.example.yaml"),
        salesforce_client_id="operator-client",
        salesforce_client_secret="operator-secret",
        salesforce_login_base_url="https://login.salesforce.com",
        hubspot_api_base_url="https://api.hubapi.com",
        _env_file=None,
    )
    salesforce = build_connected_crm_provider(
        settings,
        "salesforce",
        {
            "refresh_token": "workspace-refresh",
            "login_base_url": "http://127.0.0.1",
        },
    )
    hubspot = build_connected_crm_provider(
        settings,
        "hubspot",
        {"access_token": "workspace-token", "api_base_url": "http://127.0.0.1"},
    )
    assert salesforce.login_base_url == "https://login.salesforce.com"
    assert salesforce.client_id == "operator-client"
    assert salesforce.client_secret == "operator-secret"
    assert hubspot.api_base_url == "https://api.hubapi.com"


def test_workspace_connection_drives_crm_sync_and_mapping_scope(
    icp, routing, tmp_path, repository_root
) -> None:
    workspace_a, workspace_b = uuid4(), uuid4()
    store = DevelopmentSecretStore()
    settings = Settings(crm_config_path=str(repository_root / "config" / "crm.example.yaml"))
    resolver = WorkspaceCrmProviderResolver(settings, store)
    with session_for(tmp_path) as session:
        connection_a = create_manual_connection(
            session,
            workspace_a,
            "mock",
            manual_request("mock", {"mode": "a"}),
            store,
        )
        connection_b = create_manual_connection(
            session,
            workspace_b,
            "mock",
            manual_request("mock", {"mode": "b"}),
            store,
        )
        _, _, lead_a = process_lead(
            session,
            payload("workspace-a-sync"),
            icp,
            routing,
            workspace_id=workspace_a,
        )
        _, _, lead_b = process_lead(
            session,
            payload("workspace-b-sync"),
            icp,
            routing,
            workspace_id=workspace_b,
        )
        sync_lead_to_crm(session, lead_a, resolver.resolve(session, workspace_a))
        sync_lead_to_crm(session, lead_b, resolver.resolve(session, workspace_b))
        sync_a = get_current_crm_sync(session, lead_a.id, workspace_id=workspace_a)
        sync_b = get_current_crm_sync(session, lead_b.id, workspace_id=workspace_b)
        assert sync_a is not None and sync_a.provider_connection_id == connection_a.id
        assert sync_b is not None and sync_b.provider_connection_id == connection_b.id
        assert sync_a.workspace_id != sync_b.workspace_id
        assert get_current_crm_sync(session, lead_b.id, workspace_id=workspace_a) is None


def test_connection_api_never_exposes_secret_and_blocks_cross_workspace_access(
    tmp_path,
) -> None:
    workspace_a, workspace_b = uuid4(), uuid4()
    store = DevelopmentSecretStore()
    session = session_for(tmp_path, threads=True)

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_secret_store] = lambda: store
    app.dependency_overrides[get_provider_connection_validator] = lambda: type(
        "Validator",
        (),
        {
            "validate": lambda self, provider, credentials, scopes: ConnectionValidation(
                valid=True,
                verified=True,
                status="connected",
                credential_state="configured",
                message="Provider accepted the credentials.",
            )
        },
    )()
    try:
        with TestClient(app) as client:
            created = client.post(
                "/v1/integrations/hubspot/credentials",
                headers={"X-Workspace-ID": str(workspace_a)},
                json={
                    "account_name": "Workspace A HubSpot",
                    "external_account_id": "portal-a",
                    "auth_type": "private_token",
                    "scopes": ["crm.objects.contacts.write"],
                    "credentials": {"access_token": "test-private-token"},
                    "metadata": {"region": "us"},
                    "activate": True,
                },
            )
            rejected_manual_oauth_provider = client.post(
                "/v1/integrations/gmail/credentials",
                headers={"X-Workspace-ID": str(workspace_a)},
                json={
                    "account_name": "Mailbox",
                    "auth_type": "private_token",
                    "scopes": [],
                    "credentials": {"access_token": "must-use-oauth"},
                },
            )
            rejected_nested_secret_metadata = client.post(
                "/v1/integrations/hubspot/credentials",
                headers={"X-Workspace-ID": str(workspace_a)},
                json={
                    "account_name": "Unsafe metadata",
                    "auth_type": "private_token",
                    "scopes": [],
                    "credentials": {"access_token": "stored-safely"},
                    "metadata": {"profile": {"api_key": "must-not-be-returned"}},
                },
            )
            rejected_endpoint_override = client.post(
                "/v1/integrations/hubspot/credentials",
                headers={"X-Workspace-ID": str(workspace_a)},
                json={
                    "account_name": "Unsafe endpoint override",
                    "auth_type": "private_token",
                    "scopes": [],
                    "credentials": {
                        "access_token": "stored-safely",
                        "api_base_url": "http://127.0.0.1",
                    },
                },
            )
            body = created.json()
            foreign = client.get(
                f"/v1/integrations/connections/{body['id']}",
                headers={"X-Workspace-ID": str(workspace_b)},
            )
            foreign_test = client.post(
                f"/v1/integrations/connections/{body['id']}/test",
                headers={"X-Workspace-ID": str(workspace_b)},
            )
            foreign_disconnect = client.post(
                f"/v1/integrations/connections/{body['id']}/disconnect",
                headers={"X-Workspace-ID": str(workspace_b)},
            )
            tested = client.post(
                f"/v1/integrations/connections/{body['id']}/test",
                headers={"X-Workspace-ID": str(workspace_a)},
            )
            disconnected = client.post(
                f"/v1/integrations/connections/{body['id']}/disconnect",
                headers={"X-Workspace-ID": str(workspace_a)},
            )
            reconnected = client.post(
                f"/v1/integrations/connections/{body['id']}/reconnect",
                headers={"X-Workspace-ID": str(workspace_a)},
                json={"credentials": {"access_token": "replacement-test-token"}},
            )
        assert created.status_code == 201
        assert rejected_manual_oauth_provider.status_code == 422
        assert rejected_nested_secret_metadata.status_code == 422
        assert rejected_endpoint_override.status_code == 422
        assert foreign.status_code == 404
        assert foreign_test.status_code == 404
        assert foreign_disconnect.status_code == 404
        assert tested.status_code == 200 and tested.json()["valid"] is True
        assert disconnected.json()["credential_state"] == "missing"
        assert reconnected.json()["credential_state"] == "configured"
        serialized = str(body).lower()
        assert "test-private-token" not in serialized
        assert "secret_reference" not in body
        assert "credentials" not in body
        persisted = session.get(ProviderConnectionORM, UUID(body["id"]))
        assert persisted is not None
        assert persisted.secret_reference.startswith("devsecret://")
        assert "replacement-test-token" not in persisted.secret_reference
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_oauth_contract_stores_reference_without_returning_authorization_code(tmp_path) -> None:
    workspace = uuid4()
    store = DevelopmentSecretStore()
    session = session_for(tmp_path, threads=True)

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_secret_store] = lambda: store
    app.dependency_overrides[get_oauth_broker] = DevelopmentOAuthBroker
    try:
        with TestClient(app) as client:
            started = client.post(
                "/v1/integrations/hubspot/connect",
                headers={"X-Workspace-ID": str(workspace)},
                json={"account_name": "OAuth HubSpot", "scopes": []},
            )
            started_body = started.json()
            assert started.status_code == 200
            assert started_body["connection"]["scopes"] == [
                "oauth",
                "crm.objects.contacts.read",
                "crm.objects.contacts.write",
                "crm.schemas.contacts.read",
                "crm.schemas.contacts.write",
            ]
            assert (
                "scope=oauth+crm.objects.contacts.read+crm.objects.contacts.write"
                "+crm.schemas.contacts.read+crm.schemas.contacts.write"
                in started_body["authorization_url"]
            )
            completed = client.post(
                "/v1/integrations/hubspot/callback",
                headers={"X-Workspace-ID": str(workspace)},
                json={
                    "connection_id": started_body["connection"]["id"],
                    "state": started_body["state"],
                    "authorization_code": "one-time-test-code",
                },
            )
        assert started.status_code == 200
        assert completed.status_code == 200
        assert completed.json()["status"] == "connected"
        assert "one-time-test-code" not in str(completed.json())
        assert "secret_reference" not in completed.json()
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_cross_workspace_lead_crm_and_retry_apis_return_not_found(icp, routing, tmp_path) -> None:
    workspace_a, workspace_b = uuid4(), uuid4()
    session = session_for(tmp_path, threads=True)
    _, _, lead_b = process_lead(
        session,
        payload("workspace-b-private"),
        icp,
        routing,
        workspace_id=workspace_b,
    )

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            headers = {"X-Workspace-ID": str(workspace_a)}
            read_paths = [
                f"/v1/leads/{lead_b.id}",
                f"/v1/leads/{lead_b.id}/qualification",
                f"/v1/leads/{lead_b.id}/routing",
                f"/v1/leads/{lead_b.id}/routing/action-plan",
                f"/v1/leads/{lead_b.id}/routing/history",
                f"/v1/leads/{lead_b.id}/notifications",
                f"/v1/leads/{lead_b.id}/pipeline-history",
                f"/v1/leads/{lead_b.id}/crm-sync",
                f"/v1/leads/{lead_b.id}/crm-sync/history",
            ]
            for path in read_paths:
                assert client.get(path, headers=headers).status_code == 404
            mutation_paths = [
                f"/v1/leads/{lead_b.id}/notifications/retry",
                f"/v1/leads/{lead_b.id}/crm-sync",
                f"/v1/leads/{lead_b.id}/crm-sync/retry",
            ]
            for path in mutation_paths:
                assert client.post(path, headers=headers).status_code == 404
            assert (
                client.post(
                    f"/v1/leads/{lead_b.id}/routing/reroute",
                    headers=headers,
                    json={"reason": "authorization boundary test"},
                ).status_code
                == 404
            )
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_tool_requires_permission_audits_and_is_idempotent(
    icp, routing, tmp_path, repository_root
) -> None:
    workspace = uuid4()
    store = DevelopmentSecretStore()
    settings = Settings(crm_config_path=str(repository_root / "config" / "crm.example.yaml"))
    resolver = WorkspaceCrmProviderResolver(settings, store)
    fallback = MockCrmProvider()
    with session_for(tmp_path) as session:
        _, _, lead = process_lead(
            session,
            payload("agent-tool-lead"),
            icp,
            routing,
            workspace_id=workspace,
        )
        registry = ToolRegistry(session)
        registry.register(CrmCreateOrUpdateLeadTool(session, resolver, fallback))
        denied = ToolContext(workspace, "viewer", frozenset())
        with pytest.raises(ToolPermissionError):
            registry.invoke(
                "crm.create_or_update_lead",
                denied,
                {"lead_id": str(lead.id)},
                idempotency_key="denied-1",
            )
        agents_execute_only = ToolContext(
            workspace, "agent-without-crm", frozenset({"agents.execute"})
        )
        with pytest.raises(ToolPermissionError):
            registry.invoke(
                "crm.create_or_update_lead",
                agents_execute_only,
                {"lead_id": str(lead.id)},
                idempotency_key="agents-execute-only",
            )
        allowed = ToolContext(workspace, "agent-1", frozenset({"crm.sync"}))
        first = registry.invoke(
            "crm.create_or_update_lead",
            allowed,
            {"lead_id": str(lead.id)},
            idempotency_key="sync-1",
        )
        replay = registry.invoke(
            "crm.create_or_update_lead",
            allowed,
            {"lead_id": str(lead.id)},
            idempotency_key="sync-1",
        )
        with pytest.raises(ToolPermissionError):
            registry.invoke(
                "crm.create_or_update_lead",
                denied,
                {"lead_id": str(lead.id)},
                idempotency_key="sync-1",
            )
        assert first.status == "completed"
        assert replay.reused is True
        assert replay.audit_event_id == first.audit_event_id
        statuses = list(
            session.scalars(select(AgentToolAuditORM.status).order_by(AgentToolAuditORM.created_at))
        )
        assert statuses == ["denied", "denied", "completed"]
        assert session.scalar(select(func.count()).select_from(CrmSyncORM)) == 1

        with pytest.raises(ValueError):
            registry.invoke(
                "crm.create_or_update_lead",
                allowed,
                {"lead_id": str(lead.id), "access_token": "must-not-be-audited"},
                idempotency_key="invalid-extra-input",
            )
        failed = session.scalar(
            select(AgentToolAuditORM).where(
                AgentToolAuditORM.idempotency_key == "invalid-extra-input"
            )
        )
        assert failed is not None
        assert failed.status == "failed"
        assert "access_token" not in failed.input_summary
