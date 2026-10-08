from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.main import app
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import ProviderConnectionORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.repositories import ensure_workspace


@pytest.fixture
def slack_config_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    workspace_id = uuid4()
    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as session:
        ensure_workspace(session, workspace_id, name="Rule test workspace")
        connection = ProviderConnectionORM(
            id=uuid4(), workspace_id=workspace_id, provider_type="slack",
            account_name="Test Slack", auth_type="oauth2", status="verified",
            scopes=[], secret_reference="test-only", credential_state="configured",
            connection_metadata={"slack_team_id": "TTEST", "slack_channel_id": "CTEST"},
            is_active=True, created_at=now, updated_at=now,
        )
        session.add(connection)
        session.commit()
        context = WorkspaceContext(
            workspace_id=workspace_id,
            permissions=frozenset({"integrations.view", "integrations.manage"}),
        )
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_workspace_context] = lambda: context
        try:
            with TestClient(app) as client:
                yield client, session, connection, context
        finally:
            app.dependency_overrides.clear()
    engine.dispose()


def rule(connection, **changes):
    result = {
        "id": str(uuid4()), "name": "Website wins",
        "connection_id": str(connection.id), "destination_type": "channel",
        "channel_id": "CTEST", "qualification_bands": ["warm", "hot"],
        "sources": ["website", "email"], "queues": [], "weight": 1,
    }
    result.update(changes)
    return result


def test_rules_save_reload_edit_and_remove(slack_config_client):
    client, _, connection, _ = slack_config_client
    path = "/v1/integrations/slack/notification-config"
    assert client.get(path).json()["rules"] == []
    channel_rule = rule(connection)
    direct_rule = rule(
        connection, name="Personal notification", destination_type="direct_message",
        channel_id="U12345678",
    )
    payload = {"default_connection_id": str(connection.id), "rules": [channel_rule, direct_rule]}
    response = client.put(path, json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["rules"][0]["qualification_bands"] == ["hot", "warm"]
    assert saved["rules"][0]["sources"] == ["email", "website"]
    reloaded = client.get(path).json()
    assert reloaded["rules"] == saved["rules"]
    assert reloaded["default_connection_id"] == saved["default_connection_id"]
    assert reloaded["workspace_id"] == saved["workspace_id"]
    assert reloaded["updated_at"]
    direct_rule["name"] = "Updated rule"
    payload["rules"] = [direct_rule]
    response = client.put(path, json=payload)
    assert response.status_code == 200, response.text
    assert client.get(path).json()["rules"][0]["name"] == "Updated rule"
    payload["rules"] = []
    assert client.put(path, json=payload).status_code == 200
    assert client.get(path).json()["rules"] == []


def test_unavailable_connections_cannot_be_saved(slack_config_client):
    client, session, connection, _ = slack_config_client
    connection.is_active = False
    session.commit()
    response = client.put("/v1/integrations/slack/notification-config", json={
        "default_connection_id": None, "rules": [rule(connection)],
    })
    assert response.status_code == 422
    assert "unavailable connection" in response.json()["detail"]


def test_duplicate_rule_ids_are_rejected(slack_config_client):
    client, _, connection, _ = slack_config_client
    original = rule(connection)
    response = client.put("/v1/integrations/slack/notification-config", json={
        "default_connection_id": str(connection.id), "rules": [original, original],
    })
    assert response.status_code == 422
    assert "unique" in response.json()["detail"]


def test_read_only_members_cannot_save_rules(slack_config_client):
    client, _, connection, context = slack_config_client
    readonly = WorkspaceContext(
        workspace_id=context.workspace_id, permissions=frozenset({"integrations.view"}),
    )
    app.dependency_overrides[get_workspace_context] = lambda: readonly
    assert client.get("/v1/integrations/slack/notification-config").status_code == 200
    response = client.put("/v1/integrations/slack/notification-config", json={
        "default_connection_id": str(connection.id), "rules": [rule(connection)],
    })
    assert response.status_code == 403
