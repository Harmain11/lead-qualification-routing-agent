from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from lead_agent.domain.crm_inbound import ExternalCrmRecord
from lead_agent.domain.integrations import PROVIDER_SETUP
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.infrastructure.oauth import ProviderOAuthBroker
from lead_agent.infrastructure.outlook import (
    OUTLOOK_READ_SCOPE,
    OUTLOOK_SCOPES,
    OutlookConnectionValidator,
    OutlookInboundProvider,
    candidate_reason,
    html_to_text,
)
from lead_agent.settings.config import Settings


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _message(message_id: str = "m-1", *, subject: str = "Pricing request") -> dict:
    return {
        "id": message_id,
        "conversationId": "conversation-1",
        "internetMessageId": f"<{message_id}@client.example>",
        "subject": subject,
        "receivedDateTime": "2026-09-27T08:00:00Z",
        "from": {"emailAddress": {"name": "Mira Example", "address": "mira@client.example"}},
        "replyTo": [{"emailAddress": {"name": "Sales", "address": "sales@client.example"}}],
        "bodyPreview": "Could we schedule a demo for our implementation team?",
        "body": {
            "contentType": "html",
            "content": "<p>Could we schedule a <b>demo</b> for our implementation team?</p>",
        },
    }


def test_outlook_is_available_read_only_and_has_no_send_scope() -> None:
    setup = PROVIDER_SETUP["outlook"]
    assert setup.availability == "available"
    assert setup.required_scopes == OUTLOOK_SCOPES
    assert "Mail.ReadWrite" not in setup.required_scopes
    assert "Mail.Send" not in setup.required_scopes


def test_outlook_validator_verifies_profile_and_inbox_identity() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/me"):
            return httpx.Response(
                200,
                json={"id": "account-1", "displayName": "Mira", "mail": "mira@outlook.test"},
            )
        return httpx.Response(200, json={"id": "inbox", "displayName": "Inbox"})

    result = OutlookConnectionValidator(client=_client(handler)).validate(
        "outlook", {"access_token": "access"}, OUTLOOK_SCOPES
    )
    assert result.verified
    assert result.metadata["outlook_email"] == "mira@outlook.test"
    assert result.metadata["outlook_account_id"] == "account-1"
    assert paths == ["/v1.0/me", "/v1.0/me/mailFolders/inbox"]


def test_outlook_validator_requires_mail_read_and_classifies_mailbox_failure() -> None:
    missing = OutlookConnectionValidator().validate("outlook", {"access_token": "access"}, [])
    assert missing.status == "insufficient_permissions"

    def unavailable(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": "account-1", "mail": "mira@outlook.test"})
        return httpx.Response(404, json={"error": {"code": "ErrorItemNotFound"}})

    result = OutlookConnectionValidator(client=_client(unavailable)).validate(
        "outlook", {"access_token": "access"}, [OUTLOOK_READ_SCOPE]
    )
    assert result.status == "configuration_error"
    assert result.error_code == "outlook_mailbox_unavailable"


@pytest.mark.parametrize(
    ("status", "category", "retryable", "code"),
    [
        (401, "revoked", False, "outlook_reauthorization_required"),
        (403, "insufficient_permissions", False, "outlook_insufficient_scope"),
        (429, "rate_limited", True, "outlook_rate_limited"),
        (503, "temporarily_unavailable", True, "outlook_provider_unavailable"),
    ],
)
def test_outlook_graph_error_mapping(
    status: int, category: str, retryable: bool, code: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"Retry-After": "7"} if status == 429 else {}
        return httpx.Response(status, headers=headers, json={"error": {"code": "GraphError"}})

    provider = OutlookInboundProvider(
        uuid4(), "access", "inbox@outlook.test", client=_client(handler)
    )
    with pytest.raises(ProviderOperationError) as caught:
        provider.list_records(object_type="message", cursor=None, limit=10)
    assert caught.value.category == category
    assert caught.value.retryable is retryable
    assert caught.value.code == code
    if status == 429:
        assert caught.value.metadata["retry_after_seconds"] == 7


def test_outlook_graph_timeout_and_transport_fail_secret_safe() -> None:
    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider timeout")

    provider = OutlookInboundProvider(
        uuid4(), "access", "inbox@outlook.test", client=_client(timeout)
    )
    with pytest.raises(ProviderOperationError) as caught:
        provider.list_records(object_type="message", cursor=None, limit=10)
    assert caught.value.code == "outlook_timeout"
    assert "provider timeout" not in str(caught.value)


def test_outlook_initial_delta_is_bounded_and_incremental_uses_delta_link() -> None:
    requests: list[httpx.Request] = []
    next_url = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$skiptoken=2"
    delta_url = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$deltatoken=3"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "skiptoken=2" in str(request.url):
            return httpx.Response(
                200, json={"value": [_message("m-2")], "@odata.deltaLink": delta_url}
            )
        if "deltatoken=3" in str(request.url):
            return httpx.Response(200, json={"value": [], "@odata.deltaLink": delta_url})
        return httpx.Response(200, json={"value": [_message()], "@odata.nextLink": next_url})

    provider = OutlookInboundProvider(
        uuid4(), "access", "inbox@outlook.test", initial_sync_days=14, client=_client(handler)
    )
    first = provider.list_records(object_type="message", cursor=None, limit=25)
    second = provider.list_records(object_type="message", cursor=first.next_cursor, limit=25)
    assert [record.record_id for record in first.records] == ["m-1"]
    assert first.checkpoint is None
    assert second.checkpoint == {"delta_url": delta_url, "reconciliation": False}
    assert requests[0].url.params["$top"] == "25"
    assert "receivedDateTime ge" in requests[0].url.params["$filter"]


def test_outlook_expired_delta_reconciles_only_recent_inbox() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "deltatoken=expired" in str(request.url):
            return httpx.Response(
                410, json={"error": {"code": "SyncStateNotFound", "message": "expired"}}
            )
        return httpx.Response(
            200,
            json={
                "value": [_message("reconciled")],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?$deltatoken=fresh",
            },
        )

    provider = OutlookInboundProvider(
        uuid4(), "access", "inbox@outlook.test", client=_client(handler)
    )
    page = provider.list_records(
        object_type="message",
        cursor={"delta_url": "https://graph.microsoft.com/v1.0/delta?$deltatoken=expired"},
        limit=10,
    )
    assert [record.record_id for record in page.records] == ["reconciled"]
    assert page.checkpoint is not None
    assert "receivedDateTime" in calls[-1]
    assert "messages/delta" in calls[-1]
    assert len(calls) == 2


def test_outlook_normalization_sanitizes_html_and_preserves_provenance() -> None:
    provider = OutlookInboundProvider(uuid4(), "access", "inbox@outlook.test")
    normalized = provider.normalize_record(
        ExternalCrmRecord(
            provider="outlook",
            object_type="message",
            record_id="message-1",
            values=_message(),
            modified_at=datetime(2026, 9, 27, 8, tzinfo=UTC),
            provider_version="change-1",
        )
    )
    assert normalized.provider == "outlook"
    assert normalized.normalized_payload["source_detail"] == "Microsoft Outlook"
    assert normalized.normalized_payload["reply_to"] == "sales@client.example"
    assert normalized.provider_metadata["outlook_conversation_id"] == "conversation-1"
    assert "<b>" not in normalized.normalized_payload["message"]
    assert html_to_text("<script>bad()</script><p>Good &amp; clear</p>") == "Good & clear"


def test_outlook_candidate_filter_is_deterministic() -> None:
    message = _message()
    assert candidate_reason(message, "inbox@outlook.test") is None
    assert (
        candidate_reason({**message, "subject": "Weekly newsletter"}, "inbox@outlook.test")
        == "bulk_or_transactional"
    )
    assert candidate_reason(
        {**message, "from": {"emailAddress": {"address": "no-reply@system.test"}}},
        "inbox@outlook.test",
    ) == "automated_sender"


def test_outlook_oauth_uses_common_authority_pkce_and_exact_scopes() -> None:
    settings = Settings(
        microsoft_client_id="client-id",
        microsoft_client_secret="client-secret",
        microsoft_tenant_id="common",
    )
    broker = ProviderOAuthBroker(settings)
    authorization_url = broker.begin(
        "outlook",
        uuid4(),
        "state-value",
        "challenge",
        "http://localhost:8001/callback",
        OUTLOOK_SCOPES,
    )
    assert "login.microsoftonline.com/common/oauth2/v2.0/authorize" in authorization_url
    assert "code_challenge_method=S256" in authorization_url
    for scope in OUTLOOK_SCOPES:
        assert scope in authorization_url


def test_outlook_oauth_exchange_and_refresh_accept_rotated_refresh_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=(
                {
                    "access_token": "new-access",
                    "refresh_token": "rotated-refresh",
                    "expires_in": 3600,
                    "scope": " ".join(OUTLOOK_SCOPES),
                }
            ),
        )

    settings = Settings(
        microsoft_client_id="client-id",
        microsoft_client_secret="client-secret",
        microsoft_tenant_id="common",
    )
    broker = ProviderOAuthBroker(settings, client=_client(handler))
    tokens = broker.exchange(
        "outlook", "temporary-code", "pkce-verifier", "http://localhost:8001/callback"
    )
    refreshed = broker.refresh("outlook", tokens["refresh_token"])
    assert tokens["refresh_token"] == "rotated-refresh"
    assert refreshed["refresh_token"] == "rotated-refresh"
    assert b"code_verifier=pkce-verifier" in requests[0].content
    assert b"grant_type=refresh_token" in requests[1].content
