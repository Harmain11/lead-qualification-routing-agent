from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from lead_agent.api.routes.meta_webhooks import (
    _account_matches,
    _messages,
    _provider_for_object,
    _verify_signature,
    verify_meta_webhook,
)
from lead_agent.settings.config import Settings


def test_meta_objects_map_to_supported_providers() -> None:
    assert _provider_for_object("instagram") == "instagram"
    assert _provider_for_object("page") == "messenger"
    assert _provider_for_object("whatsapp_business_account") == "whatsapp_business"
    assert _provider_for_object("unknown") is None


def test_signature_validation_uses_meta_sha256_header() -> None:
    body = b'{"object":"instagram"}'
    secret = "test-app-secret"
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    _verify_signature(body, signature, secret)
    with pytest.raises(HTTPException) as missing:
        _verify_signature(body, None, secret)
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as invalid:
        _verify_signature(body, "sha256=" + "0" * 64, secret)
    assert invalid.value.status_code == 401


def test_get_handshake_returns_the_exact_challenge_as_plain_text() -> None:
    query = (
        b"hub.mode=subscribe&hub.verify_token=local-token&hub.challenge=challenge-value"
    )
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/v1/webhooks/meta",
        "query_string": query,
        "headers": [],
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "scheme": "http",
    })
    response = verify_meta_webhook(request, Settings(webhook_signing_secret="local-token"))
    assert response.body == b"challenge-value"
    assert response.media_type == "text/plain"


def test_instagram_messages_preserve_direction_and_participant_identity() -> None:
    events = _messages(
        {
            "entry": [{
                "id": "instagram-account",
                "messaging": [
                    {
                        "sender": {"id": "prospect-1"},
                        "recipient": {"id": "instagram-account"},
                        "timestamp": 1_800_000_000_000,
                        "message": {"mid": "m-in", "text": "Can I get pricing?"},
                    },
                    {
                        "sender": {"id": "instagram-account"},
                        "recipient": {"id": "prospect-2"},
                        "timestamp": 1_800_000_000_100,
                        "message": {"mid": "m-out", "text": "Thanks", "is_echo": True},
                    },
                ],
            }]
        },
        "instagram",
    )
    assert len(events) == 2
    assert events[0]["participant_id"] == "prospect-1"
    assert events[0]["direction"] == "inbound"
    assert events[0]["event_id"] == "m-in"
    assert events[1]["participant_id"] == "prospect-2"
    assert events[1]["direction"] == "outbound"


def test_whatsapp_message_uses_phone_account_and_contact_name() -> None:
    events = _messages(
        {
            "entry": [{
                "id": "waba-id",
                "changes": [{
                    "value": {
                        "metadata": {"phone_number_id": "phone-id"},
                        "contacts": [{
                            "wa_id": "15551234567",
                            "profile": {"name": "Ari Example"},
                        }],
                        "messages": [{
                            "id": "wamid.1",
                            "from": "15551234567",
                            "timestamp": "1800000000",
                            "text": {"body": "I would like a demo"},
                        }],
                    }
                }],
            }]
        },
        "whatsapp_business",
    )
    assert len(events) == 1
    assert events[0]["account_id"] == "phone-id"
    assert events[0]["participant_id"] == "15551234567"
    assert events[0]["name"] == "Ari Example"
    assert events[0]["content"] == "I would like a demo"


def test_connection_account_matching_is_exact_and_supports_verified_ids() -> None:
    connection = SimpleNamespace(
        external_account_id="page-123",
        connection_metadata={"verified_account_id": "page-123"},
    )
    assert _account_matches(connection, {"page-123"})
    assert not _account_matches(connection, {"page-1234"})
