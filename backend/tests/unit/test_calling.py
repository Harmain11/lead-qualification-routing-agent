from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.main import app
from lead_agent.api.routes import calling
from lead_agent.application.services import process_lead
from lead_agent.domain.provider_errors import provider_timeout_error
from lead_agent.domain.rbac import role_permissions
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.communications import (
    CallSnapshot,
    CommunicationsClient,
    CommunicationsValidator,
    phone,
    timestamp,
)
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.calling import CallRecordORM
from lead_agent.infrastructure.db.models import ProviderConnectionORM
from lead_agent.infrastructure.db.operations import ConsentEventORM, OperationsPolicyORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.repositories import ensure_workspace

SID = "AC" + "1" * 32
TARGET = "+14155550111"
REP = "+14155550222"
FROM = "+14155550333"
CREDENTIALS = {
    "gohighlevel": {"access_token": "secret", "location_id": "loc1"},
    "twilio": {"account_sid": SID, "auth_token": "secret", "from_number": FROM},
    "aircall": {"api_id": "id", "api_token": "secret", "user_id": "1", "number_id": "2"},
    "ringcentral": {
        "client_id": "id",
        "client_secret": "secret",
        "jwt": "secret",
        "from_number": REP,
    },
    "vapi": {"api_key": "secret", "assistant_id": "assistant1", "phone_number_id": "number1"},
    "retell": {"api_key": "secret", "agent_id": "agent1", "from_number": FROM},
}


@pytest.mark.parametrize("provider", CREDENTIALS)
def test_connection_verification_never_calls_customer(provider, calls):
    seen = []

    def transport(request):
        seen.append(request)
        path = request.url.path
        body = {}
        if provider == "gohighlevel":
            body = (
                {"contacts": []}
                if path == "/contacts/search"
                else {"location": {"id": "loc1", "name": "Acme"}}
            )
        elif provider == "twilio":
            body = (
                {"sid": SID, "status": "active"}
                if path.endswith(SID + ".json")
                else {
                    "incoming_phone_numbers": [
                        {"phone_number": FROM, "capabilities": {"voice": True}}
                    ]
                }
            )
        elif provider == "aircall":
            body = (
                {"user": {"id": 1, "name": "Rep"}} if "/users/" in path else {"number": {"id": 2}}
            )
        elif provider == "ringcentral":
            body = (
                {"access_token": "token"}
                if path.endswith("/token")
                else {"id": "extension", "status": "Enabled"}
            )
        elif provider == "vapi":
            body = (
                {"id": "assistant1", "orgId": "org"}
                if "/assistant/" in path
                else {"id": "number1", "orgId": "org"}
            )
        else:
            body = {"agent_id": "agent1"} if "/get-agent/" in path else {"phone_number": FROM}
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = CommunicationsValidator(client=client).validate(
            provider, CREDENTIALS[provider], []
        )
    from lead_agent.application.integrations import create_manual_connection, validate_connection
    from lead_agent.domain.integrations import PROVIDER_SETUP, ManualConnectionInput
    from lead_agent.infrastructure.oauth import DevelopmentOAuthBroker
    from lead_agent.infrastructure.secrets import DevelopmentSecretStore

    session, _, workspace, *_ = calls
    store = DevelopmentSecretStore()
    setup = PROVIDER_SETUP[provider]
    row = create_manual_connection(
        session,
        workspace.workspace_id,
        provider,
        ManualConnectionInput(
            account_name="New connection",
            auth_type=setup.preferred_auth,
            scopes=setup.required_scopes,
            credentials=CREDENTIALS[provider],
        ),
        store,
    )
    with httpx.Client(transport=httpx.MockTransport(transport)) as lifecycle_http:
        row, verdict = validate_connection(
            session,
            workspace.workspace_id,
            row.id,
            secret_store=store,
            broker=DevelopmentOAuthBroker(),
            validator=CommunicationsValidator(client=lifecycle_http),
        )
    assert verdict.verified and row.status == "verified" and row.is_active
    assert row.external_account_id == verdict.metadata["verified_account_id"]
    assert "secret" not in str(row.connection_metadata)
    assert result.verified and result.metadata["verified_account_id"]
    assert all(
        r.method == "GET" or r.url.path.endswith("/oauth/token") or r.url.path == "/contacts/search"
        for r in seen
    )
    assert "secret" not in result.model_dump_json()


@pytest.mark.parametrize("provider", ["twilio", "aircall", "ringcentral", "vapi", "retell"])
def test_explicit_call_payload_and_provider_auth(provider):
    seen = []

    def transport(request):
        seen.append(request)
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(200, json={"access_token": "token"})
        if provider == "aircall":
            return httpx.Response(204)
        return httpx.Response(201, json={"sid": "CA123", "id": "call123", "call_id": "call123"})

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        adapter = CommunicationsClient(provider, CREDENTIALS[provider], client=http)
        result, status = adapter.start_call(TARGET, REP, "request1")
    request = seen[-1]
    assert request.method == "POST"
    assert (
        TARGET in request.content.decode() or TARGET.replace("+", "%2B") in request.content.decode()
    )
    assert status == ("requested" if provider == "aircall" else "queued")
    if provider in {"aircall", "twilio"}:
        assert request.headers["authorization"].startswith("Basic ")
    else:
        assert request.headers["authorization"].startswith("Bearer ")
    if provider == "twilio":
        assert (
            REP.replace("+", "%2B") in request.content.decode()
            and "Twiml=" in request.content.decode()
        )
    if provider == "aircall":
        assert result is None


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_bad_credentials_and_errors_are_secret_safe(status):
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(status, json={"error": "secret"}))
    ) as client:
        result = CommunicationsValidator(client=client).validate("vapi", CREDENTIALS["vapi"], [])
    assert not result.verified
    assert "secret" not in result.model_dump_json()


def test_call_history_filters_exact_phone_and_limits_transcripts():
    items = [
        {
            "id": "matching",
            "customer": {"number": TARGET},
            "status": "ended",
            "artifact": {
                "transcript": "a" * 100001,
                "recordingUrl": "https://cdn.example.com/recording.wav",
            },
        },
        {"id": "other", "customer": {"number": REP}, "transcript": "private other contact"},
        {"id": "bad", "customer": None},
    ]
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=items))
    ) as client:
        records = CommunicationsClient("vapi", CREDENTIALS["vapi"], client=client).recent_calls(
            TARGET
        )
    assert [r.external_id for r in records] == ["matching"]
    assert len(records[0].transcript) == 100000


def test_phone_and_timestamp_validation():
    assert phone("+1 (415) 555-0111") == TARGET
    for value in ["4155550111", "http://evil", "+123", "+14155550111<xml>"]:
        with pytest.raises(ValueError):
            phone(value)
    assert timestamp("Mon, 12 Oct 2026 10:00:00 +0000").tzinfo is not None
    assert timestamp(1730000000000).year == 2024


@pytest.fixture
def calls(icp, routing, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    event.listen(engine, "connect", lambda db, _: db.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        workspace_id, other_id = uuid4(), uuid4()
        ensure_workspace(session, workspace_id)
        ensure_workspace(session, other_id)
        _, _, lead = process_lead(
            session,
            {
                "source": "website",
                "full_name": "Alex Smith",
                "email": "alex@acme.test",
                "phone": TARGET,
                "company_name": "Acme",
                "product_or_service": "Consulting",
                "message": "Request a demo",
            },
            icp,
            routing,
            workspace_id=workspace_id,
        )
        now = datetime.now(UTC)
        connection = ProviderConnectionORM(
            workspace_id=workspace_id,
            provider_type="twilio",
            account_name="Test",
            auth_type="private_token",
            status="verified",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        other = ProviderConnectionORM(
            workspace_id=other_id,
            provider_type="twilio",
            account_name="Private other account",
            auth_type="private_token",
            status="verified",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add_all([connection, other])
        session.commit()
        workspace = WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="test-rep",
            role="owner",
            permissions=role_permissions("owner"),
        )

        class FakeClient:
            credentials = CREDENTIALS["twilio"]
            count = 0
            timeout = False

            def close(self):
                pass

            def start_call(self, target, rep, request_id):
                self.count += 1
                if self.timeout:
                    raise provider_timeout_error("twilio")
                return "CA123", "queued"

            def recent_calls(self, target):
                return [
                    CallSnapshot(
                        external_id="CA123",
                        customer_phone=target,
                        status="completed",
                        transcript="Lead requested a proposal.",
                        duration_seconds=10,
                    )
                ]

        adapter = FakeClient()
        monkeypatch.setattr(calling, "_client", lambda *a: adapter)
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_workspace_context] = lambda: workspace
        with TestClient(app) as client:
            yield session, client, workspace, lead, connection, other, adapter
        app.dependency_overrides.clear()
    engine.dispose()


def request(connection):
    return {
        "connection_id": str(connection.id),
        "request_id": str(uuid4()),
        "confirmed": True,
        "rep_phone": REP,
    }


def test_call_is_confirmed_and_duplicate_request_never_redials(calls):
    session, client, _, lead, connection, _, adapter = calls
    payload = request(connection)
    url = f"/v1/calling/leads/{lead.id}/start"
    assert client.post(url, json={**payload, "confirmed": False}).status_code == 422
    first = client.post(url, json=payload)
    assert first.status_code == 200, first.text
    assert client.post(url, json=payload).json()["id"] == first.json()["id"]
    assert client.post(url, json={**payload, "rep_phone": FROM}).status_code == 409
    assert adapter.count == 1
    assert session.scalar(select(func.count()).select_from(CallRecordORM)) == 1


def test_uncertain_provider_timeout_is_durable_and_never_retried(calls):
    _, client, _, lead, connection, _, adapter = calls
    adapter.timeout = True
    payload = request(connection)
    url = f"/v1/calling/leads/{lead.id}/start"
    assert client.post(url, json=payload).json()["status"] == "uncertain"
    assert client.post(url, json=payload).json()["status"] == "uncertain"
    assert adapter.count == 1


@pytest.mark.parametrize("channel,status", [("all", "denied"), ("call", "withdrawn")])
def test_do_not_contact_blocks_before_provider_boundary(calls, channel, status):
    session, client, workspace, lead, connection, _, adapter = calls
    session.add(
        ConsentEventORM(
            workspace_id=workspace.workspace_id,
            email=lead.email,
            channel=channel,
            status=status,
            basis="request",
            purpose="Opt out",
            evidence="Do not contact me",
            actor_id="test",
        )
    )
    session.commit()
    assert not client.get(f"/v1/calling/leads/{lead.id}").json()["outreach_allowed"]
    assert (
        client.post(f"/v1/calling/leads/{lead.id}/start", json=request(connection)).status_code
        == 409
    )
    assert adapter.count == 0


def test_strict_consent_and_residency_prevent_calls(calls):
    session, client, workspace, lead, connection, _, adapter = calls
    policy = OperationsPolicyORM(
        workspace_id=workspace.workspace_id,
        configuration={"require_recorded_consent": True},
        version=0,
        updated_at=datetime.now(UTC),
        updated_by="test",
    )
    session.add(policy)
    session.commit()
    assert (
        client.post(f"/v1/calling/leads/{lead.id}/start", json=request(connection)).status_code
        == 409
    )
    policy.configuration = {"required_region": "eu"}
    session.commit()
    assert (
        client.post(f"/v1/calling/leads/{lead.id}/start", json=request(connection)).status_code
        == 409
    )
    assert adapter.count == 0


def test_tenant_permissions_and_unverified_connections_block(calls):
    session, client, workspace, lead, connection, other, adapter = calls
    url = f"/v1/calling/leads/{lead.id}/start"
    assert client.post(url, json=request(other)).status_code == 404
    assert all(
        c["account_name"] != "Private other account"
        for c in client.get(f"/v1/calling/leads/{lead.id}").json()["connections"]
    )
    connection.status = "configured"
    session.commit()
    assert client.post(url, json=request(connection)).status_code == 409
    app.dependency_overrides[get_workspace_context] = lambda: replace(
        workspace, role="viewer", permissions=role_permissions("viewer")
    )
    assert client.post(url, json=request(connection)).status_code == 403
    assert adapter.count == 0


def test_history_is_deduplicated_and_compliance_export_contains_calls(calls):
    session, client, _, lead, connection, _, adapter = calls
    assert (
        client.post(f"/v1/calling/leads/{lead.id}/start", json=request(connection)).status_code
        == 200
    )
    url = f"/v1/calling/leads/{lead.id}/sync"
    assert client.post(url, json={"connection_id": str(connection.id)}).status_code == 200
    assert client.post(url, json={"connection_id": str(connection.id)}).status_code == 200
    data = client.get(f"/v1/calling/leads/{lead.id}").json()
    assert len(data["records"]) == 1 and data["records"][0]["status"] == "completed"
    assert data["records"][0]["transcript"] == "Lead requested a proposal."
    export = client.get(f"/v1/ops/leads/{lead.id}/audit-export")
    assert export.status_code == 200, export.text
    assert export.json()["calls"][0]["transcript"] == "Lead requested a proposal."
    assert session.scalar(select(func.count()).select_from(CallRecordORM)) == 1


def test_history_orders_by_actual_call_time(calls):
    _, client, _, lead, connection, _, adapter = calls
    adapter.recent_calls = lambda target: [
        CallSnapshot(
            external_id="recent",
            customer_phone=target,
            started_at=datetime(2026, 10, 12, tzinfo=UTC),
        ),
        CallSnapshot(
            external_id="older", customer_phone=target, started_at=datetime(2026, 10, 1, tzinfo=UTC)
        ),
    ]
    assert (
        client.post(
            f"/v1/calling/leads/{lead.id}/sync", json={"connection_id": str(connection.id)}
        ).status_code
        == 200
    )
    records = client.get(f"/v1/calling/leads/{lead.id}").json()["records"]
    assert [row["external_id"] for row in records] == ["recent", "older"]


def test_retention_cascade_removes_call_data_with_deleted_lead(calls):
    session, client, _, lead, connection, _, _ = calls
    assert (
        client.post(f"/v1/calling/leads/{lead.id}/start", json=request(connection)).status_code
        == 200
    )
    assert client.delete(f"/v1/leads/{lead.id}").status_code == 204
    assert session.scalar(select(func.count()).select_from(CallRecordORM)) == 0
