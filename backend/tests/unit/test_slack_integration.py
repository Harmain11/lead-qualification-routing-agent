from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.main import app
from lead_agent.application.integrations import (
    begin_oauth_connection,
    complete_oauth_connection,
    disconnect_connection,
)
from lead_agent.application.services import process_lead
from lead_agent.domain.integrations import PROVIDER_SETUP
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.domain.rbac import role_permissions
from lead_agent.domain.routing import NotificationDelivery, NotificationMessage
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    NotificationORM,
    ProviderConnectionORM,
    ProviderLifecycleAuditORM,
    WorkspaceNotificationConfigORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.oauth import ProviderOAuthBroker
from lead_agent.infrastructure.provider_lifecycle import WorkspaceAuthorizedConnectionResolver
from lead_agent.infrastructure.repositories import ensure_workspace
from lead_agent.infrastructure.secrets import DevelopmentSecretStore
from lead_agent.infrastructure.slack import (
    SLACK_REQUIRED_SCOPES,
    SlackConnectionValidator,
    SlackNotificationProvider,
    SlackProviderRevoker,
    SlackWebApi,
    WorkspaceSlackNotificationResolver,
    get_workspace_slack_notification_resolver,
)
from lead_agent.settings.config import Settings


def slack_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_slack_direct_message_opens_dm_before_posting():
    calls = []

    def handler(request):
        calls.append((request.url.path, request.content.decode()))
        if request.url.path.endswith("/conversations.open"):
            return httpx.Response(200, json={"ok": True, "channel": {"id": "D123"}})
        return httpx.Response(200, json={"ok": True, "ts": "message-ts"})

    provider = SlackNotificationProvider(
        connection=SimpleNamespace(
            connection_id=uuid4(), workspace_id=uuid4(), credentials={"access_token": "test"}
        ),
        channel_id="C123",
        client=slack_client(handler),
    )
    result = provider.send_direct(
        "U12345678",
        NotificationMessage(deduplication_key="direct-test", priority="normal", text="Hello"),
    )
    assert result.state == "sent"
    assert [call[0] for call in calls] == [
        "/api/conversations.open", "/api/chat.postMessage",
    ]
    assert "channel=D123" in calls[1][1]


@pytest.mark.parametrize("fallback_enabled,expected_calls", [(False, 1), (True, 2)])
def test_slack_dm_failure_uses_channel_only_when_fallback_is_selected(
    fallback_enabled, expected_calls
):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/conversations.open"):
            return httpx.Response(200, json={"ok": False, "error": "user_not_found"})
        return httpx.Response(200, json={"ok": True, "ts": "fallback-ts"})

    provider = SlackNotificationProvider(
        connection=SimpleNamespace(
            connection_id=uuid4(), workspace_id=uuid4(), credentials={"access_token": "test"}
        ),
        channel_id="U12345678",
        destination_type="direct_message",
        fallback_to_channel=fallback_enabled,
        fallback_channel_id="C123",
        client=slack_client(handler),
    )
    result = provider.send(
        NotificationMessage(deduplication_key="fallback-test", priority="normal", text="Hello")
    )
    assert len(calls) == expected_calls
    assert (result.state == "sent") is fallback_enabled
    if fallback_enabled:
        assert "configured fallback channel" in result.error_message
    else:
        assert "Verify the member ID" in result.error_message


def database(tmp_path) -> Session:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'slack.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def make_connection(session: Session, store: DevelopmentSecretStore, workspace_id):
    ensure_workspace(session, workspace_id)
    session.commit()
    now = datetime.now(UTC)
    reference = store.store(workspace_id, "slack", {"access_token": f"workspace-{workspace_id}"})
    row = ProviderConnectionORM(
        id=uuid4(),
        workspace_id=workspace_id,
        provider_type="slack",
        external_account_id="T123",
        account_name="Test workspace",
        auth_type="oauth2",
        status="verified",
        scopes=list(SLACK_REQUIRED_SCOPES),
        secret_reference=reference,
        credential_state="configured",
        last_validation_at=now,
        last_validation_result={"status": "connected", "verified": True},
        connection_metadata={
            "slack_team_id": "T123",
            "slack_channel_id": "C123",
            "slack_channel_name": "lead-agent-testing",
        },
        is_active=True,
        disconnected_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return row


def test_slack_setup_scopes_are_minimal() -> None:
    assert PROVIDER_SETUP["slack"].required_scopes == ["chat:write", "channels:read"]
    assert "channels:history" not in PROVIDER_SETUP["slack"].required_scopes
    assert "users:read" not in PROVIDER_SETUP["slack"].required_scopes


def test_slack_oauth_uses_exact_scopes_and_derived_callback_path(tmp_path) -> None:
    store = DevelopmentSecretStore()
    with database(tmp_path) as session:
        workspace = uuid4()
        row, started = begin_oauth_connection(
            session,
            workspace,
            "slack",
            "Pending Slack workspace",
            [],
            ProviderOAuthBroker(
                Settings(slack_client_id="client-id", slack_client_secret="client-secret")
            ),
            store,
            "https://api.example.test",
            "operator",
        )
        params = parse_qs(urlparse(started.authorization_url).query)
        assert row.scopes == ["chat:write", "channels:read", "channels:join", "groups:read"]
        assert params["scope"] == ["chat:write,channels:read,channels:join,groups:read"]
        assert "team" not in params  # Slack must allow workspace selection.
        assert row.connection_metadata["oauth_redirect_uri"] == (
            "https://api.example.test/v1/integrations/slack/oauth/callback"
        )


def test_non_expiring_slack_oauth_token_does_not_get_a_fake_refresh_deadline(tmp_path) -> None:
    store = DevelopmentSecretStore()

    class Broker:
        def exchange(self, provider, authorization_code, code_verifier, redirect_uri):
            assert provider == "slack"
            assert authorization_code == "approved-code"
            assert code_verifier and redirect_uri.endswith("/v1/integrations/slack/oauth/callback")
            return {
                "access_token": "workspace-token",
                "scope": "chat:write,channels:read",
                "external_account_id": "T-expected",
                "account_name": "Test Slack workspace",
                "bot_user_id": "U-bot",
            }

    with database(tmp_path) as session:
        workspace = uuid4()
        row, started = begin_oauth_connection(
            session,
            workspace,
            "slack",
            "Pending Slack workspace",
            [],
            ProviderOAuthBroker(
                Settings(slack_client_id="client-id", slack_client_secret="client-secret")
            ),
            store,
            "https://api.example.test",
            "operator",
        )
        completed = complete_oauth_connection(
            session,
            workspace,
            row.id,
            started.state,
            "approved-code",
            store,
            Broker(),
            "operator",
        )
        assert completed.status == "connected"
        assert completed.connection_metadata["refresh_available"] is False
        assert "token_expires_at" not in completed.connection_metadata
        assert completed.external_account_id == "T-expected"
        audit = session.scalar(
            select(ProviderLifecycleAuditORM).where(
                ProviderLifecycleAuditORM.connection_id == completed.id,
                ProviderLifecycleAuditORM.action == "oauth_connect",
            )
        )
        assert audit is not None
        assert audit.actor_id == "operator"
        assert audit.details == {"scopes": ["chat:write", "channels:read", "channels:join", "groups:read"]}
        assert "workspace-token" not in str(audit.details)


def test_validator_checks_token_team_and_required_scopes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("auth.test")
        return httpx.Response(
            200,
            headers={"x-oauth-scopes": "chat:write,channels:read"},
            json={
                "ok": True,
                "team_id": "T-A",
                "team": "Workspace A",
                "user_id": "U-A",
                "bot_id": "B-A",
            },
        )

    result = SlackConnectionValidator(client=slack_client(handler)).validate(
        "slack", {"access_token": "secret-token"}, list(SLACK_REQUIRED_SCOPES)
    )
    assert result.verified is True
    assert result.metadata["slack_team_id"] == "T-A"
    assert result.metadata["granted_scopes"] == ["channels:read", "chat:write"]


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"ok": False, "error": "invalid_auth"}, "invalid_credentials"),
        ({"ok": False, "error": "token_revoked"}, "revoked"),
    ],
)
def test_validator_maps_bad_and_revoked_tokens(payload, status: str) -> None:
    client = slack_client(lambda _: httpx.Response(200, json=payload))
    result = SlackConnectionValidator(client=client).validate(
        "slack", {"access_token": "secret-token"}, list(SLACK_REQUIRED_SCOPES)
    )
    assert result.status == status
    assert "secret-token" not in result.message


def test_validator_rejects_insufficient_scope() -> None:
    client = slack_client(
        lambda _: httpx.Response(
            200,
            headers={"x-oauth-scopes": "chat:write"},
            json={"ok": True, "team_id": "T-A"},
        )
    )
    result = SlackConnectionValidator(client=client).validate(
        "slack", {"access_token": "secret-token"}, list(SLACK_REQUIRED_SCOPES)
    )
    assert result.status == "insufficient_permissions"
    assert result.error_code == "slack_missing_scope"


def test_validator_rejects_a_valid_token_from_the_wrong_workspace() -> None:
    client = slack_client(
        lambda _: httpx.Response(
            200,
            headers={"x-oauth-scopes": "chat:write,channels:read"},
            json={"ok": True, "team_id": "T-OTHER"},
        )
    )
    result = SlackConnectionValidator(client=client).validate(
        "slack",
        {"access_token": "secret-token", "external_account_id": "T-EXPECTED"},
        list(SLACK_REQUIRED_SCOPES),
    )
    assert result.verified is False
    assert result.error_code == "slack_workspace_mismatch"


def test_slack_api_maps_rate_limit_server_and_timeout_without_payloads() -> None:
    rate_limited = SlackWebApi(
        "secret-token",
        slack_client(
            lambda _: httpx.Response(
                429, headers={"retry-after": "11"}, json={"error": "xoxb-secret"}
            )
        ),
    )
    with pytest.raises(Exception, match="retry later"):
        try:
            rate_limited.call("auth.test")
        except Exception as error:
            assert error.metadata["retry_after_seconds"] == 11
            raise
    server_error = SlackWebApi("secret-token", slack_client(lambda _: httpx.Response(503)))
    with pytest.raises(Exception, match="temporarily unavailable"):
        server_error.call("auth.test")

    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret-token timeout")

    with pytest.raises(Exception, match="timed out") as error:
        SlackWebApi("secret-token", slack_client(timeout)).call("auth.test")
    assert "secret-token" not in str(error.value)


@pytest.mark.parametrize(
    ("status_code", "code"),
    [(401, "slack_invalid_auth"), (403, "slack_missing_scope"), (404, "slack_channel_not_found")],
)
def test_slack_http_errors_use_safe_categories(status_code: int, code: str) -> None:
    client = slack_client(lambda _: httpx.Response(status_code, json={"error": "private payload"}))
    with pytest.raises(ProviderOperationError) as error:
        SlackWebApi("secret-token", client).call("auth.test")
    assert error.value.code == code
    assert "private payload" not in str(error.value)


def test_resolver_and_channel_configuration_are_workspace_scoped(tmp_path) -> None:
    store = DevelopmentSecretStore()
    with database(tmp_path) as session:
        workspace_a, workspace_b = uuid4(), uuid4()
        connection_a = make_connection(session, store, workspace_a)
        connection_b = make_connection(session, store, workspace_b)

        def handler(request: httpx.Request) -> httpx.Response:
            token = request.headers["Authorization"]
            assert token in {f"Bearer workspace-{workspace_a}", f"Bearer workspace-{workspace_b}"}
            if request.url.path.endswith("conversations.info"):
                assert token == f"Bearer workspace-{workspace_a}"
                if request.content.decode().endswith("C-B"):
                    return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "channel": {"id": "C-new", "name": "lead-agent-testing", "is_member": True},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "channels": [{"id": "C123", "name": "lead-agent-testing", "is_member": True}],
                },
            )

        resolver = WorkspaceSlackNotificationResolver(
            store, WorkspaceAuthorizedConnectionResolver(store), slack_client(handler)
        )
        resolved = resolver.resolve_for_workspace(session, workspace_a)
        assert resolved is not None
        assert resolved.workspace_id == workspace_a
        assert resolved._api.token == f"workspace-{workspace_a}"
        with pytest.raises(LookupError):
            resolver.resolve_connection(session, workspace_a, connection_b.id)
        with pytest.raises(ValueError, match="unavailable"):
            resolver.select_channel(session, workspace_a, connection_a.id, "C-B", "actor-a")
        channels = resolver.list_channels(session, workspace_a, connection_a.id, "actor-a")
        assert channels == [{"id": "C123", "name": "lead-agent-testing", "is_member": True, "is_private": False}]
        assert resolver.select_channel(
            session, workspace_a, connection_a.id, "C-new", "actor-a"
        ) == {
            "id": "C-new",
            "name": "lead-agent-testing",
            "is_member": True,
            "is_private": False,
        }
        session.refresh(connection_a)
        session.refresh(connection_b)
        assert connection_a.connection_metadata["slack_channel_id"] == "C-new"
        assert connection_b.connection_metadata["slack_channel_id"] == "C123"
        audit = session.scalar(
            select(ProviderLifecycleAuditORM).where(
                ProviderLifecycleAuditORM.connection_id == connection_a.id,
                ProviderLifecycleAuditORM.action == "channel_selection",
            )
        )
        assert audit is not None
        assert audit.actor_id == "actor-a"
        assert audit.details["channel_id"] == "C-new"
        assert "access_token" not in str(audit.details)


def test_channel_list_includes_public_channels_before_app_is_invited(tmp_path) -> None:
    store = DevelopmentSecretStore()
    with database(tmp_path) as session:
        workspace = uuid4()
        connection = make_connection(session, store, workspace)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("conversations.list")
            assert parse_qs(request.content.decode())["types"] == ["public_channel"]
            return httpx.Response(200, json={
                "ok": True,
                "channels": [
                    {"id": "CJOINED", "name": "announcements", "is_member": True},
                    {"id": "CINVITE", "name": "leads", "is_member": False},
                    {"id": "CUNKNOWN", "name": "projects"},
                ],
            })

        resolver = WorkspaceSlackNotificationResolver(
            store, WorkspaceAuthorizedConnectionResolver(store), slack_client(handler)
        )
        assert resolver.list_channels(session, workspace, connection.id, "actor") == [
            {"id": "CJOINED", "name": "announcements", "is_member": True, "is_private": False},
            {"id": "CINVITE", "name": "leads", "is_member": False, "is_private": False},
            {"id": "CUNKNOWN", "name": "projects", "is_member": False, "is_private": False},
        ]


def test_select_public_channel_joins_only_the_selected_channel(tmp_path) -> None:
    store = DevelopmentSecretStore()
    with database(tmp_path) as session:
        workspace = uuid4()
        connection = make_connection(session, store, workspace)
        connection.scopes = [*connection.scopes, "channels:join", "groups:read"]
        session.commit()
        calls = []
        joined = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal joined
            method = request.url.path.rsplit("/", 1)[-1]
            assert parse_qs(request.content.decode())["channel"] == ["CNEW"]
            calls.append(method)
            if method == "conversations.join":
                joined = True
            return httpx.Response(200, json={
                "ok": True,
                "channel": {"id": "CNEW", "name": "sales", "is_member": joined, "is_private": False},
            })

        resolver = WorkspaceSlackNotificationResolver(
            store, WorkspaceAuthorizedConnectionResolver(store), slack_client(handler)
        )
        result = resolver.select_channel(session, workspace, connection.id, "CNEW", "actor")
        assert result["is_member"] is True
        assert calls == ["conversations.info", "conversations.join", "conversations.info"]
        assert connection.connection_metadata["slack_channel_id"] == "CNEW"


@pytest.mark.parametrize("private", [True, False])
def test_private_invitation_and_failed_public_join_preserve_destination(tmp_path, private) -> None:
    store = DevelopmentSecretStore()
    with database(tmp_path) as session:
        workspace = uuid4()
        connection = make_connection(session, store, workspace)
        connection.scopes = [*connection.scopes, "channels:join", "groups:read"]
        session.commit()
        methods = []

        def handler(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            methods.append(method)
            if method == "conversations.join":
                return httpx.Response(200, json={"ok": False, "error": "restricted_action"})
            return httpx.Response(200, json={
                "ok": True,
                "channel": {"id": "CNEW", "name": "sales", "is_member": False, "is_private": private},
            })

        resolver = WorkspaceSlackNotificationResolver(
            store, WorkspaceAuthorizedConnectionResolver(store), slack_client(handler)
        )
        with pytest.raises(ValueError):
            resolver.select_channel(session, workspace, connection.id, "CNEW", "actor")
        assert connection.connection_metadata["slack_channel_id"] == "C123"
        assert methods == (["conversations.info"] if private else ["conversations.info", "conversations.join"])


def test_channel_list_includes_invited_private_channels_with_granted_permission(tmp_path) -> None:
    store = DevelopmentSecretStore()
    with database(tmp_path) as session:
        workspace = uuid4()
        connection = make_connection(session, store, workspace)
        connection.scopes = [*connection.scopes, "groups:read"]
        session.commit()

        def handler(request: httpx.Request) -> httpx.Response:
            assert parse_qs(request.content.decode())["types"] == ["public_channel,private_channel"]
            return httpx.Response(200, json={
                "ok": True,
                "channels": [{"id": "CPRIVATE", "name": "lead-review", "is_member": True, "is_private": True}],
            })

        resolver = WorkspaceSlackNotificationResolver(
            store, WorkspaceAuthorizedConnectionResolver(store), slack_client(handler)
        )
        assert resolver.list_channels(session, workspace, connection.id, "actor") == [
            {"id": "CPRIVATE", "name": "lead-review", "is_member": True, "is_private": True}
        ]


def test_matching_slack_rule_can_override_the_workspace_channel(tmp_path) -> None:
    store = DevelopmentSecretStore()
    with database(tmp_path) as session:
        workspace = uuid4()
        connection = make_connection(session, store, workspace)
        session.add(
            WorkspaceNotificationConfigORM(
                workspace_id=workspace,
                default_slack_connection_id=connection.id,
                slack_rules=[
                    {
                        "id": str(uuid4()),
                        "name": "Warm website leads",
                        "connection_id": str(connection.id),
                        "channel_id": "C-WARM",
                        "queues": [],
                        "qualification_bands": ["hot", "warm"],
                        "sources": ["email", "website"],
                        "weight": 1,
                    }
                ],
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()
        resolver = WorkspaceSlackNotificationResolver(
            store, WorkspaceAuthorizedConnectionResolver(store), slack_client(lambda _: httpx.Response(200))
        )
        provider = resolver.resolve_for_lead(
            session,
            workspace,
            SimpleNamespace(id=uuid4(), source="website", source_detail=None),
            SimpleNamespace(assigned_queue="sales", qualification_band="warm"),
        )
        assert provider is not None
        assert provider.channel_id == "C-WARM"
        assert WorkspaceSlackNotificationResolver._rule_matches(
            {
                "queues": ["sales", "marketing"],
                "qualification_bands": ["hot", "warm"],
                "sources": ["email", "website"],
            },
            SimpleNamespace(source="website", source_detail=None),
            SimpleNamespace(assigned_queue="sales", qualification_band="warm"),
        )
        assert not WorkspaceSlackNotificationResolver._rule_matches(
            {
                "queues": ["sales", "marketing"],
                "qualification_bands": ["hot", "warm"],
                "sources": ["email", "website"],
            },
            SimpleNamespace(source="referral", source_detail=None),
            SimpleNamespace(assigned_queue="sales", qualification_band="warm"),
        )


def test_missing_channel_and_bot_membership_are_permanent_configuration_errors(tmp_path) -> None:
    store = DevelopmentSecretStore()
    with database(tmp_path) as session:
        workspace = uuid4()
        connection = make_connection(session, store, workspace)
        resolver = WorkspaceSlackNotificationResolver(
            store,
            WorkspaceAuthorizedConnectionResolver(store),
            slack_client(
                lambda request: httpx.Response(
                    200,
                    json=(
                        {
                            "ok": True,
                            "channel": {
                                "id": "C1",
                                "name": "private",
                                "is_member": False,
                            },
                        }
                        if request.url.path.endswith("conversations.info")
                        else {"ok": False, "error": "not_in_channel"}
                    ),
                )
            ),
        )
        with pytest.raises(ValueError, match="Invite the Slack bot"):
            resolver.select_channel(session, workspace, connection.id, "C1", "actor")
        provider = resolver.resolve_for_workspace(session, workspace)
        assert provider is not None
        delivery = provider.send(
            NotificationMessage(deduplication_key="d", priority="normal", text="test")
        )
        assert delivery.state == "failed"
        assert "channel" in (delivery.error_message or "")


def test_disconnect_is_workspace_local_and_remote_revoke_is_mocked(tmp_path) -> None:
    store = DevelopmentSecretStore()
    with database(tmp_path) as session:
        workspace_a, workspace_b = uuid4(), uuid4()
        connection_a = make_connection(session, store, workspace_a)
        connection_b = make_connection(session, store, workspace_b)
        resolver = WorkspaceSlackNotificationResolver(
            store,
            WorkspaceAuthorizedConnectionResolver(store),
            slack_client(lambda _: httpx.Response(200, json={"ok": True, "revoked": True})),
        )
        disconnect_connection(
            session,
            workspace_a,
            connection_a.id,
            store,
            SlackProviderRevoker(resolver.client),
            actor_id="admin-a",
        )
        assert resolver.resolve_for_workspace(session, workspace_a) is None
        still_connected = resolver.resolve_for_workspace(session, workspace_b)
        assert still_connected is not None
        assert still_connected.connection_id == connection_b.id


def test_successful_slack_notification_and_revoke_use_mocked_boundaries(tmp_path) -> None:
    store = DevelopmentSecretStore()
    with database(tmp_path) as session:
        workspace = uuid4()
        make_connection(session, store, workspace)
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            assert request.headers["Authorization"] == f"Bearer workspace-{workspace}"
            if request.url.path.endswith("chat.postMessage"):
                form = parse_qs(request.content.decode())
                assert form["client_msg_id"] == [
                    str(uuid5(NAMESPACE_URL, "lead-agent:route-1"))
                ]
            return httpx.Response(200, json={"ok": True, "ts": "123.456", "revoked": True})

        resolver = WorkspaceSlackNotificationResolver(
            store, WorkspaceAuthorizedConnectionResolver(store), slack_client(handler)
        )
        provider = resolver.resolve_for_workspace(session, workspace)
        assert provider is not None
        delivery = provider.send(
            NotificationMessage(
                deduplication_key="route-1", priority="high", text="A concise lead alert"
            )
        )
        assert delivery.state == "sent" and delivery.external_id == "123.456"
        assert (
            SlackProviderRevoker(slack_client(handler))
            .revoke("slack", {"access_token": f"workspace-{workspace}"})
            .status
            == "confirmed"
        )
        assert calls == ["/api/chat.postMessage", "/api/auth.revoke"]


def test_requalification_retry_stops_after_retry_limit(icp, routing, tmp_path) -> None:
    with database(tmp_path) as session:
        _, _, lead = process_lead(
            session,
            {
                "external_id": "retry-limit",
                "source": "website",
                "full_name": "Test Lead",
                "email": "test@example.test",
                "company_name": "Test Company",
                "product_or_service": "Lead routing",
                "message": "Need an implementation proposal this month.",
            },
            icp,
            routing,
            workspace_id=uuid4(),
        )
        notification = session.scalar(select(NotificationORM))
        assert notification is not None
        notification.status = "retryable"
        notification.provider = "mock"
        notification.attempts = 5
        session.commit()
        from lead_agent.application.routing import retry_current_notification
        from lead_agent.infrastructure.notifications.mock import MockNotificationProvider

        returned = retry_current_notification(session, lead, MockNotificationProvider())
        assert returned.id == notification.id
        assert returned.status == "failed"
        assert returned.attempts == 5


def test_notification_retry_honors_slack_retry_after(icp, routing, tmp_path) -> None:
    with database(tmp_path) as session:
        _, _, lead = process_lead(
            session,
            {
                "external_id": "retry-after",
                "source": "website",
                "full_name": "Test Lead",
                "email": "test@example.test",
                "company_name": "Test Company",
                "product_or_service": "Lead routing",
                "message": "Need an implementation proposal this month.",
            },
            icp,
            routing,
            workspace_id=uuid4(),
        )
        notification = session.scalar(select(NotificationORM))
        assert notification is not None
        notification.status = "retryable"
        notification.provider = "slack"
        notification.attempts = 1
        notification.payload = {
            **notification.payload,
            "retry_after_until": "2999-01-01T00:00:00+00:00",
        }
        session.commit()

        class UnexpectedProvider:
            provider_name = "slack"
            calls = 0

            def send(self, message):
                del message
                self.calls += 1
                raise AssertionError("Slack retry ran before Retry-After elapsed")

        from lead_agent.application.routing import retry_current_notification

        provider = UnexpectedProvider()
        returned = retry_current_notification(session, lead, provider)
        assert returned.status == "retryable"
        assert returned.attempts == 1
        assert provider.calls == 0


def test_unchanged_lead_processing_keeps_one_notification(icp, routing, tmp_path) -> None:
    with database(tmp_path) as session:
        payload = {
            "external_id": "unchanged-lead",
            "source": "website",
            "full_name": "Test Lead",
            "email": "test@example.test",
            "company_name": "Test Company",
            "product_or_service": "Lead routing",
            "message": "Need an implementation proposal this month.",
        }
        workspace_id = uuid4()
        process_lead(session, payload, icp, routing, workspace_id=workspace_id)
        first_count = len(list(session.scalars(select(NotificationORM))))
        process_lead(session, payload, icp, routing, workspace_id=workspace_id)
        second_count = len(list(session.scalars(select(NotificationORM))))
        assert first_count == second_count == 1


def test_agents_execute_does_not_grant_slack_administration(tmp_path) -> None:
    with database(tmp_path) as session:
        workspace_id = uuid4()

        def override_db():
            yield session

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
            workspace_id=workspace_id,
            actor_id="sales-rep",
            role="sales_rep",
            permissions=role_permissions("sales_rep") | {"integrations.connect"},
        )
        try:
            with TestClient(app) as client:
                changed = client.put(
                    f"/v1/integrations/connections/{uuid4()}/slack-channel",
                    json={"channel_id": "C123"},
                )
                test_message = client.post(
                    f"/v1/integrations/connections/{uuid4()}/slack-test-notification"
                )
                connect = client.post(
                    "/v1/integrations/slack/connect",
                    json={"account_name": "Workspace Slack", "scopes": []},
                )
            assert changed.status_code == 403
            assert test_message.status_code == 403
            assert connect.status_code == 403
            assert "agents.execute" in role_permissions("sales_rep")
            assert "integrations.manage" not in role_permissions("sales_rep")
        finally:
            app.dependency_overrides.clear()


def test_test_notification_endpoint_sends_a_labelled_message_and_audits_it(tmp_path) -> None:
    store = DevelopmentSecretStore()
    session = database(tmp_path)
    workspace = uuid4()
    connection = make_connection(session, store, workspace)

    def override_db():
        yield session

    resolver = WorkspaceSlackNotificationResolver(
        store,
        WorkspaceAuthorizedConnectionResolver(store),
        slack_client(lambda _: httpx.Response(200, json={"ok": True, "ts": "123.456"})),
    )
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        workspace_id=workspace,
        actor_id="workspace-admin",
        role="owner",
        permissions=role_permissions("owner"),
    )
    app.dependency_overrides[get_workspace_slack_notification_resolver] = lambda: resolver
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/v1/integrations/connections/{connection.id}/slack-test-notification"
            )
        assert response.status_code == 200
        assert response.json()["sent"] is True
        audit = session.scalar(
            select(ProviderLifecycleAuditORM).where(
                ProviderLifecycleAuditORM.connection_id == connection.id,
                ProviderLifecycleAuditORM.action == "test_notification",
            )
        )
        assert audit is not None and audit.actor_id == "workspace-admin"
        assert audit.details["channel_id"] == "C123"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_system_lead_notification_has_safe_correlated_lifecycle_audit(
    tmp_path, icp, routing
) -> None:
    store = DevelopmentSecretStore()
    with database(tmp_path) as session:
        workspace = uuid4()
        connection = make_connection(session, store, workspace)

        class SlackProvider:
            provider_name = "slack"
            connection_id = connection.id

            def send(self, message: NotificationMessage) -> NotificationDelivery:
                assert "Slack Audit Test Lead" in message.text
                assert "secret-token" not in message.text
                return NotificationDelivery(state="sent", external_id="123.456")

        _, _, lead = process_lead(
            session,
            {
                "source": "website",
                "full_name": "Slack Audit Test Lead",
                "email": "slack-audit@example.invalid",
                "company_name": "Slack Audit Demo",
                "industry": "technology",
                "job_title": "VP of Operations",
                "employee_count": 125,
                "geography": "United States",
                "budget": 50000,
                "product_or_service": "lead routing automation",
                "message": "Synthetic audit event test.",
            },
            icp,
            routing,
            notification_provider=SlackProvider(),
            workspace_id=workspace,
        )
        audit = session.scalar(
            select(ProviderLifecycleAuditORM).where(
                ProviderLifecycleAuditORM.connection_id == connection.id,
                ProviderLifecycleAuditORM.action == "lead_notification",
            )
        )
        assert audit is not None
        assert audit.outcome == "sent"
        assert audit.actor_id == "system"
        assert audit.correlation_id
        assert audit.details["lead_id"] == str(lead.id)
        assert audit.details["channel_id"] == "C123"
        assert "slack-audit@example.invalid" not in str(audit.details)
        assert "secret-token" not in str(audit.details)
