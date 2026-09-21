from datetime import UTC, datetime

from fastapi import HTTPException
import pytest

from lead_agent.api.routes.intake import _verify_signature
from lead_agent.infrastructure.intake.adapters import (
    ClutchAdapter,
    EmailAdapter,
    LinkedInAdapter,
    WebsiteAdapter,
)


def test_website_adapter_preserves_raw_payload_and_attribution() -> None:
    payload = {
        "event_id": "web-001",
        "full_name": " Jane Doe ",
        "email": "jane@example.com",
        "company_name": "Acme",
        "product_or_service": "Lead routing",
        "message": "Please send an overview.",
    }

    event = WebsiteAdapter().adapt(payload)

    assert event.channel == "website"
    assert event.external_id == "web-001"
    assert event.raw_payload == payload
    assert event.normalized_payload["source"] == "website"
    assert event.normalized_payload["external_id"] == "web-001"


def test_website_signature_verification_accepts_only_the_expected_digest() -> None:
    import hashlib
    import hmac

    body = b'{"event_id":"web-001"}'
    digest = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    _verify_signature(body, f"sha256={digest}", "secret")
    with pytest.raises(HTTPException):
        _verify_signature(body, "sha256=invalid", "secret")


def test_email_adapter_maps_sender_subject_body_and_domain() -> None:
    event = EmailAdapter().adapt(
        {
            "external_message_id": "mail-001",
            "sender": 'Jane Doe <jane@example.com>',
            "subject": "Workflow pricing",
            "body": "We need help with manual qualification.",
            "timestamp": "2026-09-21T10:00:00Z",
        }
    )

    assert event.channel == "email"
    assert event.external_id == "mail-001"
    assert event.normalized_payload["full_name"] == "Jane Doe"
    assert event.normalized_payload["email"] == "jane@example.com"
    assert event.normalized_payload["company_name"] == "example.com"
    assert "Subject: Workflow pricing" in event.normalized_payload["message"]
    assert event.received_at == datetime(2026, 9, 21, 10, 0, tzinfo=UTC)


def test_linkedin_adapter_is_an_official_fixture_boundary() -> None:
    event = LinkedInAdapter().adapt(
        {
            "lead_id": "li-001",
            "sender_name": "Amina Khan",
            "company_name": "Example SaaS",
            "message": "Can you show how routing works?",
        }
    )

    assert event.channel == "linkedin"
    assert event.external_id == "li-001"
    assert event.metadata["access_mode"] == "official-fixture"
    assert event.metadata["live_api_required"] is True


def test_clutch_adapter_uses_the_selected_mailbox_boundary() -> None:
    event = ClutchAdapter().adapt(
        {
            "inquiry_id": "clutch-001",
            "sender": "buyer@example.com",
            "subject": "New inquiry",
            "body": "We are looking for a lead workflow.",
        }
    )

    assert event.channel == "clutch"
    assert event.external_id == "clutch-001"
    assert event.metadata["mailbox_boundary"] is True
    assert event.normalized_payload["source"] == "clutch"


@pytest.mark.parametrize(
    "adapter,payload",
    [
        (EmailAdapter(), {"sender": "buyer@example.com"}),
        (LinkedInAdapter(), {"sender_name": "Buyer"}),
        (ClutchAdapter(), {"sender": "buyer@example.com", "subject": "Inquiry"}),
    ],
)
def test_adapters_reject_missing_required_provider_fields(adapter, payload) -> None:
    with pytest.raises(ValueError):
        adapter.adapt(payload)
