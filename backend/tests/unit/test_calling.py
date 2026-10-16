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
from lead_agent.api.routes import integrations as integration_routes
from lead_agent.application.integrations import _validate_manual_credentials
from lead_agent.application.services import process_lead
from lead_agent.domain.provider_errors import ProviderOperationError, provider_timeout_error
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
    "cloudtalk": {
        "access_key_id": "key-id",
        "access_key_secret": "secret",
        "agent_id": "7",
    },
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
        elif provider == "cloudtalk":
            body = {"responseData": {"data": [{"id": 7, "name": "Rep"}]}}
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


@pytest.mark.parametrize(
    "provider", ["twilio", "aircall", "ringcentral", "vapi", "retell", "cloudtalk"]
)
def test_explicit_call_payload_and_provider_auth(provider):
    seen = []

    def transport(request):
        seen.append(request)
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(200, json={"access_token": "token"})
        if provider == "aircall":
            return httpx.Response(204)
        if provider == "cloudtalk":
            return httpx.Response(200, json={"responseData": {"status": 200}})
        return httpx.Response(201, json={"sid": "CA123", "id": "call123", "call_id": "call123"})

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        adapter = CommunicationsClient(provider, CREDENTIALS[provider], client=http)
        result, status = adapter.start_call(TARGET, REP, "request1")
    request = seen[-1]
    assert request.method == "POST"
    assert (
        TARGET in request.content.decode() or TARGET.replace("+", "%2B") in request.content.decode()
    )
    assert status == ("requested" if provider in {"aircall", "cloudtalk"} else "queued")
    if provider in {"aircall", "twilio", "cloudtalk"}:
        assert request.headers["authorization"].startswith("Basic ")
    else:
        assert request.headers["authorization"].startswith("Bearer ")
    if provider == "twilio":
        assert (
            REP.replace("+", "%2B") in request.content.decode()
            and "Twiml=" in request.content.decode()
        )
    if provider in {"aircall", "cloudtalk"}:
        assert result is None
    if provider == "cloudtalk":
        assert request.url.host == "my.cloudtalk.io"
        assert request.url.path == "/api/calls/create.json"
        assert request.content == b'{"agent_id":7,"callee_number":"+14155550111"}'
        assert result is None


def test_cloudtalk_call_creation_maps_response_envelope_errors():
    def transport(request):
        return httpx.Response(
            200, json={"responseData": {"status": 403, "message": "Agent is not online."}}
        )

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        adapter = CommunicationsClient("cloudtalk", CREDENTIALS["cloudtalk"], client=client)
        with pytest.raises(ProviderOperationError, match="offline"):
            adapter.start_call(TARGET, REP, "stable-request-id")


def test_cloudtalk_call_creation_keeps_provider_request_id_if_returned():
    def transport(request):
        return httpx.Response(
            200, json={"responseData": {"status": 200, "request_id": "provider-request-123"}}
        )

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result, status = CommunicationsClient(
            "cloudtalk", CREDENTIALS["cloudtalk"], client=client
        ).start_call(TARGET, REP, "corral-request-id")
    assert result == "provider-request-123"
    assert status == "requested"


def test_cloudtalk_recent_history_can_be_loaded_without_a_lead_number():
    seen = []

    def transport(request):
        seen.append(request)
        if request.url.host == "my.cloudtalk.io":
            assert "public_external" not in request.url.params
            return httpx.Response(
                200,
                json={
                    "responseData": {
                        "data": [
                            {
                                "Cdr": {
                                    "id": 12345,
                                    "public_external": "14155550111",
                                    "type": "outgoing",
                                    "started_at": "2026-10-15 12:00:00",
                                    "talking_time": 42,
                                }
                            }
                        ],
                        "pageCount": 1,
                    }
                },
            )
        if request.url.path.endswith("/transcription"):
            return httpx.Response(
                200,
                json={"data": {"segments": []}, "pagination": {"total": 0}},
            )
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        communications = CommunicationsClient(
            "cloudtalk", CREDENTIALS["cloudtalk"], client=client
        )
        records = communications.recent_calls(None)
        list_request_count = len(seen)
        detailed = communications.recent_calls(None, details_for_call_id="12345")

    assert len(records) == 1
    assert records[0].customer_phone == TARGET
    assert records[0].external_id == "12345"
    assert seen[0].url.params["limit"] == "100"
    assert list_request_count == 1
    assert len(detailed) == 1
    assert any(request.url.path.endswith("/transcription") for request in seen[1:])


def test_cloudtalk_history_uses_ci_transcript_and_never_fetches_recording_audio():
    seen = []

    def transport(request):
        seen.append(request)
        if request.url.host == "my.cloudtalk.io":
            return httpx.Response(
                200,
                json={
                    "responseData": {
                        "data": [
                            {
                                "Cdr": {
                                    "id": 12345,
                                    "public_external": "14155550111",
                                    "type": "outgoing",
                                    "started_at": "2026-10-15 12:00:00",
                                    "talking_time": 42,
                                    "recording_link": "https://my.cloudtalk.io/calls/12345",
                                }
                            }
                        ]
                    }
                },
            )
        if request.url.path.endswith("/transcription"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "callId": 12345,
                        "segments": [{"text": "Hello"}, {"text": "I am interested."}],
                    },
                    "pagination": {"limit": 1000, "offset": 0, "total": 2},
                },
            )
        if request.url.path.endswith("/summary"):
            return httpx.Response(
                200, json={"callId": 12345, "summary": "Buyer asked for pricing."}
            )
        if request.url.path.endswith("/overall-sentiment"):
            return httpx.Response(200, json={"callId": 12345, "overallSentiment": "positive"})
        if request.url.path.endswith("/topics"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "topics": [{"topic": "Pricing", "weight": 0.8}],
                        "callers": [],
                    },
                    "pagination": {"limit": 1, "offset": 0, "total": 1},
                },
            )
        if request.url.path.endswith("/smart-notes"):
            return httpx.Response(
                200,
                json={
                    "smartNotes": {
                        "actionItems": ["Send a quote"],
                        "decisionsMade": ["Review pricing"],
                        "openQuestions": ["Preferred start date"],
                        "importantDatesAndDeadlines": [
                            {"dateMention": "Friday", "eventDescription": "Send the quote"}
                        ],
                        "risksAndIssues": ["Budget not confirmed"],
                        "customerFeedback": ["Interested in the product"],
                    }
                },
            )
        if request.url.path.endswith("/talk-listen-ratio"):
            return httpx.Response(
                200,
                json={
                    "talkListenRatio": [
                        {"caller": "caller1", "talkingTime": 120, "ratio": 60},
                        {"caller": "caller2", "talkingTime": 80, "ratio": 40},
                    ],
                    "callers": [
                        {"type": "contact", "localIdentifier": "caller1"},
                        {"type": "agent", "localIdentifier": "caller2"},
                    ],
                },
            )
        raise AssertionError(f"Unexpected CloudTalk request: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        calls = CommunicationsClient("cloudtalk", CREDENTIALS["cloudtalk"], client=client)
        records = calls.recent_calls(TARGET)

    assert len(records) == 1
    assert records[0].external_id == "12345"
    assert records[0].transcript == "Hello I am interested."
    assert records[0].recording_url == "https://my.cloudtalk.io/calls/12345"
    assert [request.url.host for request in seen] == [
        "my.cloudtalk.io",
        *["api.cloudtalk.io"] * 6,
    ]
    assert records[0].insights == {
        "summary": "Buyer asked for pricing.",
        "overall_sentiment": "positive",
        "topics": [{"topic": "Pricing", "weight": 0.8}],
        "smart_notes": {
            "action_items": ["Send a quote"],
            "decisions_made": ["Review pricing"],
            "open_questions": ["Preferred start date"],
            "important_dates_and_deadlines": [
                {"date_mention": "Friday", "event_description": "Send the quote"}
            ],
            "risks_and_issues": ["Budget not confirmed"],
            "customer_feedback": ["Interested in the product"],
        },
        "talk_listen_ratio": [
            {"caller": "contact", "talking_time": 120.0, "ratio": 60.0},
            {"caller": "agent", "talking_time": 80.0, "ratio": 40.0},
        ],
    }
    assert {request.url.path.rsplit("/", 1)[-1] for request in seen[1:]} == {
        "transcription",
        "summary",
        "overall-sentiment",
        "topics",
        "smart-notes",
        "talk-listen-ratio",
    }
    assert all("recording" not in request.url.path for request in seen)


def test_workspace_cloudtalk_history_includes_unmatched_calls(calls, monkeypatch):
    session, client, _, lead, connection, _, adapter = calls
    connection.provider_type = "cloudtalk"
    session.commit()
    records = [
        CallSnapshot(external_id="call-matched", customer_phone=TARGET, status="completed"),
        CallSnapshot(external_id="call-new", customer_phone="+15555550123", status="completed"),
    ]

    def recent_calls(target, *, page=1):
        assert target is None
        assert page == 1
        return records

    monkeypatch.setattr(adapter, "recent_calls", recent_calls)
    response = client.get("/v1/calling/cloudtalk/recent")

    assert response.status_code == 200
    payload = response.json()
    assert [record["id"] for record in payload["records"]] == ["call-matched", "call-new"]
    assert payload["records"][0]["lead_id"] == str(lead.id)
    assert payload["records"][1]["lead_id"] is None


def test_cloudtalk_paginates_ci_transcript_and_uses_details_link_fallback():
    seen = []

    def transport(request):
        seen.append(request)
        if request.url.host == "my.cloudtalk.io":
            return httpx.Response(
                200,
                json={
                    "responseData": {
                        "data": [
                            {
                                "Cdr": {
                                    "id": 12346,
                                    "public_external": "14155550111",
                                    "type": "incoming",
                                }
                            }
                        ]
                    }
                },
            )
        if request.url.path.endswith("/details-link"):
            return httpx.Response(200, json={"link": "https://my.cloudtalk.io/calls/12346"})
        if request.url.path.endswith("/transcription"):
            offset = int(request.url.params.get("offset", "0"))
            return httpx.Response(
                200,
                json={
                    "data": {"segments": [{"text": "hello" if offset == 0 else "world"}]},
                    "pagination": {"limit": 1000, "offset": offset, "total": 1001},
                },
            )
        if request.url.path.endswith("/overall-sentiment"):
            return httpx.Response(403, json={"message": "Not available"})
        return httpx.Response(404, json={"message": "Unavailable"})

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        records = CommunicationsClient(
            "cloudtalk", CREDENTIALS["cloudtalk"], client=client
        ).recent_calls(TARGET)

    assert len(records) == 1
    assert records[0].transcript == "hello world"
    assert records[0].recording_url == "https://my.cloudtalk.io/calls/12346"
    assert records[0].insights == {
        "summary": None,
        "overall_sentiment": None,
        "topics": [],
        "smart_notes": {
            "action_items": [],
            "decisions_made": [],
            "open_questions": [],
            "important_dates_and_deadlines": [],
            "risks_and_issues": [],
            "customer_feedback": [],
        },
        "talk_listen_ratio": [],
    }
    assert any(request.url.path.endswith("/details-link") for request in seen)
    transcript_offsets = [
        request.url.params.get("offset")
        for request in seen
        if "transcription" in request.url.path
    ]
    assert transcript_offsets == ["0", "1000"]


def test_cloudtalk_history_paginates_and_matches_lead_numbers():
    requests = []

    def transport(request):
        requests.append(request)
        if request.url.host == "api.cloudtalk.io":
            return httpx.Response(404, json={"statusCode": 404, "message": "No CI data"})
        page = int(request.url.params["page"])
        rows = (
            [{
                "Cdr": {
                    "id": "100",
                    "public_external": "+1 (415) 555-0111",
                    "type": "outgoing",
                    "started_at": "2026-10-15T12:00:00Z",
                    "talking_time": "0",
                    "recording_link": "https://my.cloudtalk.io/calls/100",
                }
            }]
            if page == 1
            else [
                {
                    "Cdr": {
                        "id": "101",
                        "public_external": "14155550111",
                        "type": "incoming",
                        "started_at": "2026-10-16T12:00:00Z",
                        "talking_time": "41",
                    },
                    "Contact": {"contact_numbers": ["+14155550111"]},
                },
                {"Cdr": {"id": "102", "public_external": "14155550999"}},
            ]
        )
        return httpx.Response(200, json={
            "responseData": {"pageNumber": page, "pageCount": 2, "limit": 1000, "data": rows}
        })

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        records = CommunicationsClient(
            "cloudtalk", CREDENTIALS["cloudtalk"], client=client
        ).recent_calls(TARGET)

    assert [record.external_id for record in records] == ["100", "101"]
    assert records[0].status == "unknown"
    assert records[0].recording_url == "https://my.cloudtalk.io/calls/100"
    assert records[1].status == "completed"
    assert records[1].duration_seconds == 41
    history_requests = [request for request in requests if request.url.host == "my.cloudtalk.io"]
    assert [request.url.params["page"] for request in history_requests] == ["1", "2"]
    assert all(
        request.url.params["public_external"] == "14155550111"
        for request in history_requests
    )


def test_cloudtalk_agent_picker_can_load_agents_before_an_id_is_selected():
    seen = []

    def transport(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "responseData": {
                    "data": [
                        {"id": 7, "name": "Jordan Rep", "email": "private@example.com"},
                        {"id": "invalid", "name": "Invalid Agent"},
                    ]
                }
            },
        )

    credentials = {
        "access_key_id": "key-id",
        "access_key_secret": "key-secret",
    }
    _validate_manual_credentials("cloudtalk", "private_token", credentials)
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        agents = CommunicationsClient(
            "cloudtalk", credentials, client=client
        ).list_cloudtalk_agents()

    assert agents == [{"id": "7", "name": "Jordan Rep"}]
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/api/agents/index.json"
    assert seen[0].headers["authorization"].startswith("Basic ")
    assert "email" not in str(agents)


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


def test_cloudtalk_agent_picker_routes_store_only_selected_agent_id(calls, monkeypatch):
    from lead_agent.application.integrations import create_manual_connection
    from lead_agent.domain.integrations import (
        PROVIDER_SETUP,
        ManualConnectionInput,
    )
    from lead_agent.infrastructure.provider_lifecycle import WorkspaceAuthorizedConnectionResolver
    from lead_agent.infrastructure.secrets import get_secret_store

    session, client, workspace, _, _, other_connection, *_ = calls
    store = get_secret_store()
    setup = PROVIDER_SETUP["cloudtalk"]
    credentials = {"access_key_id": "key-id", "access_key_secret": "key-secret"}
    connection = create_manual_connection(
        session,
        workspace.workspace_id,
        "cloudtalk",
        ManualConnectionInput(
            account_name="CloudTalk test",
            auth_type="private_token",
            scopes=setup.required_scopes,
            credentials=credentials,
        ),
        store,
        workspace.actor_id,
    )

    class FakeCloudTalkClient:
        def __init__(self, provider, values):
            assert provider == "cloudtalk"
            assert values == credentials

        def list_cloudtalk_agents(self):
            return [{"id": "8", "name": "Jordan Rep"}]

    monkeypatch.setattr(integration_routes, "CommunicationsClient", FakeCloudTalkClient)
    monkeypatch.setattr(
        integration_routes,
        "get_workspace_connection_resolver",
        lambda: WorkspaceAuthorizedConnectionResolver(store),
    )

    listed = client.get(
        f"/v1/integrations/connections/{connection.id}/cloudtalk-agents"
    )
    assert listed.status_code == 200
    assert listed.json() == {
        "agents": [{"id": "8", "name": "Jordan Rep"}],
        "selected_agent_id": None,
    }
    wrong_workspace = client.get(
        f"/v1/integrations/connections/{other_connection.id}/cloudtalk-agents"
    )
    assert wrong_workspace.status_code == 404
    invalid = client.put(
        f"/v1/integrations/connections/{connection.id}/cloudtalk-agent",
        json={"agent_id": "999"},
    )
    assert invalid.status_code == 422
    assert store.retrieve_for_workspace(
        workspace.workspace_id, "cloudtalk", connection.secret_reference
    ) == credentials
    saved = client.put(
        f"/v1/integrations/connections/{connection.id}/cloudtalk-agent",
        json={"agent_id": "8"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["metadata"]["cloudtalk_agent_name"] == "Jordan Rep"
    assert saved.json()["status"] == "connected"
    stored = store.retrieve_for_workspace(
        workspace.workspace_id, "cloudtalk", connection.secret_reference
    )
    assert stored == {**credentials, "agent_id": "8"}


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
    insights = {
        "summary": "Lead requested a proposal.",
        "overall_sentiment": "positive",
        "topics": [{"topic": "Pricing", "weight": 0.7}],
        "smart_notes": {
            "action_items": [],
            "decisions_made": [],
            "open_questions": [],
            "important_dates_and_deadlines": [],
            "risks_and_issues": [],
            "customer_feedback": [],
        },
        "talk_listen_ratio": [],
    }
    adapter.recent_calls = lambda target: [
        CallSnapshot(
            external_id="CA123",
            customer_phone=target,
            status="completed",
            transcript="Lead requested a proposal.",
            duration_seconds=10,
            insights=insights,
        )
    ]
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
    assert data["records"][0]["insights"] == insights
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
