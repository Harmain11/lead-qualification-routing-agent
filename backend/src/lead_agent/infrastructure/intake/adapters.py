from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from lead_agent.domain.intake import SourceEvent


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _fingerprint(channel: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f"{channel}:{hashlib.sha256(encoded).hexdigest()}"


def _sender_name(sender: str | None) -> str | None:
    if not sender:
        return None
    if "<" in sender:
        return _clean(sender.split("<", 1)[0].strip(' "'))
    return None


def _sender_email(sender: str | None) -> str | None:
    if not sender:
        return None
    if "<" in sender and ">" in sender:
        return _clean(sender.split("<", 1)[1].split(">", 1)[0])
    return _clean(sender)


class WebsiteAdapter:
    channel = "website"

    def adapt(self, payload: Mapping[str, Any]) -> SourceEvent:
        raw = dict(payload)
        external_id = _clean(raw.get("event_id") or raw.get("external_id"))
        event_id = external_id or _fingerprint(self.channel, raw)
        normalized = {
            **raw,
            "source": self.channel,
            "source_detail": _clean(raw.get("source_detail")) or "website-webhook",
            "external_id": event_id,
        }
        return SourceEvent.create(
            self.channel,
            event_id,
            raw,
            normalized,
            _timestamp(raw.get("received_at")),
            {"adapter": "website", "event_id_generated": external_id is None},
        )


class EmailAdapter:
    channel = "email"

    def adapt(self, payload: Mapping[str, Any]) -> SourceEvent:
        raw = dict(payload)
        sender = _clean(raw.get("sender") or raw.get("from"))
        sender_email = _sender_email(sender)
        subject = _clean(raw.get("subject")) or "Inbound email inquiry"
        body = _clean(raw.get("body") or raw.get("text"))
        if not sender_email or not body:
            raise ValueError("email payload requires sender and body")
        external_id = _clean(raw.get("external_message_id") or raw.get("message_id"))
        event_id = external_id or _fingerprint(self.channel, raw)
        domain = sender_email.split("@", 1)[1] if "@" in sender_email else None
        normalized = {
            "source": self.channel,
            "source_detail": _clean(raw.get("source_detail"))
            or _clean(raw.get("provider"))
            or "inbound-email",
            "external_id": event_id,
            "full_name": _clean(raw.get("sender_name")) or _sender_name(sender) or sender_email,
            "email": sender_email,
            "company_name": _clean(raw.get("company")) or domain or "Unknown sender company",
            "product_or_service": subject,
            "message": f"Subject: {subject}\n\n{body}",
            "received_at": raw.get("timestamp") or raw.get("received_at"),
            "job_title": raw.get("job_title"),
            "geography": raw.get("geography"),
            "phone": raw.get("phone"),
            "industry": raw.get("industry"),
            "budget": raw.get("budget"),
        }
        return SourceEvent.create(
            self.channel,
            event_id,
            raw,
            normalized,
            _timestamp(raw.get("timestamp") or raw.get("received_at")),
            {"adapter": "email", "sender_domain": domain, "subject": subject},
        )


class LinkedInAdapter:
    channel = "linkedin"

    def adapt(self, payload: Mapping[str, Any]) -> SourceEvent:
        raw = dict(payload)
        message = _clean(raw.get("message"))
        full_name = _clean(raw.get("sender_name") or raw.get("full_name"))
        if not full_name or not message:
            raise ValueError("LinkedIn payload requires sender_name and message")
        external_id = _clean(raw.get("event_id") or raw.get("lead_id") or raw.get("message_id"))
        event_id = external_id or _fingerprint(self.channel, raw)
        normalized = {
            **raw,
            "source": self.channel,
            "source_detail": _clean(raw.get("source_detail")) or "official-fixture",
            "external_id": event_id,
            "full_name": full_name,
            "email": _clean(raw.get("email")) or f"linkedin-{event_id}@invalid.local",
            "company_name": _clean(raw.get("company_name") or raw.get("company"))
            or "Unknown company",
            "product_or_service": _clean(raw.get("product_or_service")) or "LinkedIn inquiry",
            "message": message,
        }
        return SourceEvent.create(
            self.channel,
            event_id,
            raw,
            normalized,
            _timestamp(raw.get("timestamp") or raw.get("received_at")),
            {
                "adapter": "linkedin",
                "access_mode": "official-fixture",
                "live_api_required": True,
            },
        )


class ClutchAdapter(EmailAdapter):
    """Fixture boundary for the selected fourth channel: Clutch email notices."""

    channel = "clutch"

    def adapt(self, payload: Mapping[str, Any]) -> SourceEvent:
        raw = dict(payload)
        enriched = {
            **raw,
            "provider": "clutch-notification",
            "source_detail": "clutch-notification-email",
            "external_message_id": raw.get("inquiry_id") or raw.get("external_message_id"),
        }
        event = super().adapt(enriched)
        return SourceEvent(
            id=event.id,
            channel=self.channel,
            external_id=event.external_id,
            received_at=event.received_at,
            raw_payload=raw,
            normalized_payload={**event.normalized_payload, "source": self.channel},
            metadata={**event.metadata, "adapter": "clutch", "mailbox_boundary": True},
        )
