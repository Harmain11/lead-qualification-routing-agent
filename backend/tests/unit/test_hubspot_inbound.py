from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest

from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.infrastructure.crm.hubspot import (
    HubSpotConnectionValidator,
    HubSpotInboundCrmProvider,
    HubSpotProviderRevoker,
)
from lead_agent.infrastructure.oauth import ProviderOAuthBroker
from lead_agent.settings.config import Settings


class Response:
    def __init__(self, status_code: int, body: Any = None) -> None:
        self.status_code = status_code
        self.body = body if body is not None else {}
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        return self.body


class Transport:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self.requests.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected HubSpot request")
        return self.responses.pop(0)


def _contact(record_id: str = "123", *, updated: str = "2026-09-24T12:00:00Z") -> dict[str, Any]:
    return {
        "id": record_id,
        "createdAt": "2026-09-20T10:00:00Z",
        "updatedAt": updated,
        "archived": False,
        "properties": {
            "email": "Jordan@example.test",
            "firstname": "Jordan",
            "lastname": "Tester",
            "company": "Example Corp",
            "jobtitle": "VP Operations",
            "phone": "+1-555-0100",
            "hs_analytics_source": "PAID_SEARCH",
            "createdate": "2026-09-20T10:00:00Z",
            "hs_lastmodifieddate": updated,
            "hubspot_owner_id": "77",
            "description": "Needs lead routing.",
        },
    }


def _provider(transport: Transport) -> HubSpotInboundCrmProvider:
    return HubSpotInboundCrmProvider(
        connection_id=uuid4(), access_token="not-a-real-test-token", transport=transport
    )


def test_hubspot_contacts_initial_import_pagination_and_canonical_normalization() -> None:
    transport = Transport(
        [
            Response(200, {"results": [_contact()], "paging": {"next": {"after": "opaque-2"}}}),
            Response(200, {"results": [_contact("124")]}),
        ]
    )
    provider = _provider(transport)

    first = provider.list_records(object_type="contacts", cursor=None, limit=100)
    second = provider.list_records(object_type="contacts", cursor=first.next_cursor, limit=100)
    canonical = provider.normalize_record(first.records[0])

    assert first.next_cursor == {"mode": "initial", "after": "opaque-2"}
    assert second.next_cursor is None
    assert first.checkpoint == {
        "modified_after_ms": str(int(datetime(2026, 9, 24, 12, tzinfo=UTC).timestamp() * 1000))
    }
    assert transport.requests[1]["url"].endswith("after=opaque-2")
    assert canonical.provider_record_id == "123"
    assert canonical.object_type == "contacts"
    assert canonical.normalized_payload["email"] == "jordan@example.test"
    assert canonical.normalized_payload["source"] == "paid_search"
    assert canonical.normalized_payload["source_detail"] == "crm-import:hubspot:PAID_SEARCH"
    assert canonical.provider_metadata["owner_id"] == "77"
    assert len(canonical.source_fingerprint) == 64


def test_hubspot_incremental_uses_checkpoint_overlap_and_preserves_cursor() -> None:
    transport = Transport(
        [Response(200, {"results": [_contact()], "paging": {"next": {"after": 17}}})]
    )
    provider = _provider(transport)
    checkpoint = {"modified_after_ms": "1790251200000"}

    page = provider.list_records(object_type="contacts", cursor=checkpoint, limit=25)

    request = transport.requests[0]
    assert request["method"] == "POST"
    assert request["url"].endswith("/crm/v3/objects/contacts/search")
    assert request["json"]["sorts"] == ["lastmodifieddate"]
    filters = request["json"]["filterGroups"][0]["filters"]
    assert filters[0]["propertyName"] == "lastmodifieddate"
    assert int(filters[0]["value"]) == int(checkpoint["modified_after_ms"]) - 60_000
    assert page.next_cursor == {
        "mode": "incremental",
        "after": "17",
        "modified_after_ms": checkpoint["modified_after_ms"],
    }
    assert page.checkpoint is not None


def test_hubspot_rejects_non_contact_object() -> None:
    provider = _provider(Transport([]))
    with pytest.raises(ProviderOperationError, match="Contacts only"):
        provider.list_records(object_type="companies", cursor=None, limit=10)


@pytest.mark.parametrize(
    ("status", "category", "retryable"),
    [
        (401, "invalid_credentials", False),
        (403, "insufficient_permissions", False),
        (408, "temporarily_unavailable", True),
        (429, "rate_limited", True),
        (503, "temporarily_unavailable", True),
    ],
)
def test_hubspot_maps_provider_http_failures(status: int, category: str, retryable: bool) -> None:
    provider = _provider(Transport([Response(status)]))
    with pytest.raises(ProviderOperationError) as caught:
        provider.list_records(object_type="contacts", cursor=None, limit=10)
    assert caught.value.category == category
    assert caught.value.retryable is retryable


def test_hubspot_connection_validation_checks_active_token_scopes_and_read_probe() -> None:
    transport = Transport(
        [
            Response(
                200,
                {
                    "active": True,
                    "hub_id": 9001,
                    "scopes": ["oauth", "crm.objects.contacts.read", "crm.objects.contacts.write"],
                },
            ),
            Response(200, {"results": []}),
        ]
    )
    validator = HubSpotConnectionValidator(
        client_id="app-id", client_secret="app-secret", transport=transport
    )

    result = validator.validate(
        "hubspot",
        {"access_token": "test-access"},
        ["oauth", "crm.objects.contacts.read", "crm.objects.contacts.write"],
    )

    assert result.verified
    assert result.metadata["hub_id"] == "9001"
    assert transport.requests[0]["url"].endswith("/oauth/2026-09/token/introspect")
    assert transport.requests[1]["method"] == "GET"
    assert "contacts?limit=1" in transport.requests[1]["url"]


def test_hubspot_connection_validation_fails_closed_on_missing_write_scope() -> None:
    validator = HubSpotConnectionValidator(
        client_id="app-id",
        client_secret="app-secret",
        transport=Transport(
            [Response(200, {"active": True, "scopes": ["crm.objects.contacts.read"]})]
        ),
    )
    with pytest.raises(ProviderOperationError) as caught:
        validator.validate(
            "hubspot", {"access_token": "test-access"}, ["crm.objects.contacts.read"]
        )
    assert caught.value.category == "insufficient_permissions"


def test_hubspot_revoker_uses_refresh_token_without_exposing_it_in_url() -> None:
    transport = Transport([Response(200)])
    revoker = HubSpotProviderRevoker(
        client_id="app-id", client_secret="app-secret", transport=transport
    )

    result = revoker.revoke("hubspot", {"refresh_token": "refresh-value"})

    assert result.status == "confirmed"
    assert transport.requests[0]["url"].endswith("/oauth/2026-09/token/revoke")
    assert transport.requests[0]["data"]["token"] == "refresh-value"
    assert "refresh-value" not in transport.requests[0]["url"]


def test_hubspot_oauth_uses_shared_s256_and_versioned_exchange_refresh() -> None:
    captured: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "test-access",
                "refresh_token": "rotated-refresh",
                "expires_in": 1800,
                "hub_id": 9001,
            },
        )

    settings = Settings(
        hubspot_client_id="client-id",
        hubspot_client_secret="client-secret",
        hubspot_oauth_api_version="2026-09",
    )
    broker = ProviderOAuthBroker(
        settings, client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    authorization_url = broker.begin(
        "hubspot",
        uuid4(),
        "state-value",
        "s256-challenge",
        "https://app.test/callback",
        ["oauth", "crm.objects.contacts.read", "crm.objects.contacts.write"],
    )
    tokens = broker.exchange(
        "hubspot", "temporary-code", "pkce-verifier", "https://app.test/callback"
    )
    refreshed = broker.refresh("hubspot", tokens["refresh_token"])

    assert "code_challenge=s256-challenge" in authorization_url
    assert "code_challenge_method=S256" in authorization_url
    assert "scope=oauth+crm.objects.contacts.read+crm.objects.contacts.write" in authorization_url
    assert captured[0].url.path == "/oauth/2026-09/token"
    assert b"code_verifier=pkce-verifier" in captured[0].content
    assert captured[1].url.path == "/oauth/2026-09/token"
    assert b"grant_type=refresh_token" in captured[1].content
    assert refreshed["refresh_token"] == "rotated-refresh"
