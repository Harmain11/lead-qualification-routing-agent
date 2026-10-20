from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_policies
from lead_agent.application.routing import record_pipeline_event
from lead_agent.application.services import process_source_event, reprocess_lead
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.intake import SourceEvent
from lead_agent.domain.routing import NotificationProvider
from lead_agent.infrastructure.db.models import (
    LeadORM,
    ProviderConnectionORM,
    SocialConversationORM,
    SocialMessageORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.notifications.factory import get_notification_provider
from lead_agent.settings.config import Settings, get_settings

router = APIRouter(prefix="/v1/webhooks/meta", tags=["Meta webhooks"])
_MAX_BODY = 2_000_000
_INQUIRY = re.compile(
    r"\b(?:pricing|price|quote|quotation|demo|proposal|consultation|"
    r"interested|looking for|need|would like|request a call|buy|purchase)\b",
    re.I,
)


def _provider_for_object(value: object) -> str | None:
    return {
        "instagram": "instagram",
        "page": "messenger",
        "whatsapp_business_account": "whatsapp_business",
    }.get(str(value))


def _app_secret(settings: Settings, provider: str) -> str:
    return {
        "instagram": settings.meta_instagram_client_secret,
        "messenger": settings.meta_messenger_client_secret,
        "whatsapp_business": settings.meta_whatsapp_client_secret,
    }[provider]


def _verify_signature(body: bytes, header: str | None, secret: str) -> None:
    if not secret or not header or not header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, header):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def _messages(payload: dict[str, Any], provider: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return result
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id") or "")
        if provider in {"instagram", "messenger"}:
            events = entry.get("messaging", [])
            for event in events if isinstance(events, list) else []:
                if not isinstance(event, dict):
                    continue
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                sender = event.get("sender")
                recipient = event.get("recipient")
                sender_id = str(sender.get("id") or "") if isinstance(sender, dict) else ""
                recipient_id = (
                    str(recipient.get("id") or "") if isinstance(recipient, dict) else ""
                )
                echo = message.get("is_echo") is True or sender_id == entry_id
                participant = recipient_id if echo else sender_id
                event_id = str(message.get("mid") or event.get("event_id") or "")
                content = str(message.get("text") or "")
                if event_id and participant:
                    result.append({
                        "account_id": entry_id,
                        "participant_id": participant,
                    "conversation_identity": str(
                        (event.get("conversation") or {}).get("id") or participant
                        if isinstance(event.get("conversation") or {}, dict)
                        else participant
                    ),
                        "event_id": event_id,
                        "direction": "outbound" if echo else "inbound",
                        "content": content[:8000],
                        "timestamp": event.get("timestamp"),
                        "raw": event,
                        "name": str(sender.get("name") or "") if isinstance(sender, dict) else "",
                    })
        elif provider == "whatsapp_business":
            changes = entry.get("changes", [])
            for change in changes if isinstance(changes, list) else []:
                value = change.get("value", {}) if isinstance(change, dict) else {}
                if not isinstance(value, dict):
                    continue
                metadata = value.get("metadata", {})
                phone_id = (
                    str(metadata.get("phone_number_id") or "")
                    if isinstance(metadata, dict)
                    else ""
                )
                contact_names = {
                    str(item.get("wa_id")): str(item.get("profile", {}).get("name") or "")
                    for item in value.get("contacts", [])
                    if isinstance(item, dict)
                } if isinstance(value.get("contacts"), list) else {}
                messages = value.get("messages", [])
                for message in messages if isinstance(messages, list) else []:
                    if not isinstance(message, dict):
                        continue
                    participant = str(message.get("from") or "")
                    event_id = str(message.get("id") or "")
                    body = message.get("text", {})
                    content = str(body.get("body") or "") if isinstance(body, dict) else ""
                    if event_id and participant:
                        result.append({
                            "account_id": phone_id or entry_id,
                            "participant_id": participant,
                            "conversation_identity": str(
                                (message.get("context") or {}).get("conversation_id") or participant
                                if isinstance(message.get("context") or {}, dict)
                                else participant
                            ),
                            "event_id": event_id,
                            "direction": "inbound",
                            "content": content[:8000],
                            "timestamp": message.get("timestamp"),
                            "raw": message,
                            "name": contact_names.get(participant, ""),
                        })
    return result


def _account_matches(connection: ProviderConnectionORM, candidates: set[str]) -> bool:
    metadata = connection.connection_metadata or {}
    ids = {
        str(connection.external_account_id or ""),
        *(
            str(metadata.get(key) or "")
            for key in (
                "verified_account_id", "account_id", "page_id",
                "business_account_id", "phone_number_id",
            )
        ),
    }
    return bool((ids - {""}) & candidates)


def _canonical_account_id(
    connection: ProviderConnectionORM, fallback: str
) -> str:
    metadata = connection.connection_metadata or {}
    return str(
        connection.external_account_id
        or metadata.get("verified_account_id")
        or metadata.get("account_id")
        or metadata.get("page_id")
        or metadata.get("business_account_id")
        or metadata.get("phone_number_id")
        or fallback
    )


def _connected_account(
    session: Session, provider: str, candidates: set[str]
) -> ProviderConnectionORM:
    connections = list(session.scalars(
        select(ProviderConnectionORM).where(
            ProviderConnectionORM.provider_type == provider,
            ProviderConnectionORM.status == "verified",
            ProviderConnectionORM.is_active.is_(True),
        )
    ))
    matches = [row for row in connections if _account_matches(row, candidates)]
    if len(matches) != 1:
        raise HTTPException(
            status_code=404,
            detail="No unique connected Meta account matches this event",
        )
    return matches[0]


def _timestamp(value: object) -> datetime:
    try:
        raw = int(value or 0)
        # WhatsApp timestamps are seconds; Messenger/Instagram timestamps are milliseconds.
        stamp = raw / 1000 if raw > 10_000_000_000 else raw
        return datetime.fromtimestamp(stamp, UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return datetime.now(UTC)


def _lead_payload(
    provider: str, connection_id: str, participant: str, conversation: str,
    name: str, content: str, event_id: str, received_at: datetime, raw: dict[str, Any],
) -> dict[str, Any]:
    digest = sha256(f"{connection_id}:{participant}".encode()).hexdigest()[:28]
    return {
        "source": provider,
        "source_detail": f"{provider}-message",
        "external_id": conversation,
        "full_name": name or f"Social contact {participant[:32]}",
        "email": f"social.{digest}@contact.invalid",
        "company_name": "Social inquiry",
        "product_or_service": "Social conversation",
        "message": content,
        "received_at": received_at.isoformat(),
        "raw_payload": {
            "provider": provider,
            "connection_id": connection_id,
            "participant_id": participant,
            "conversation_identity": conversation,
            "event_id": event_id,
            "message": raw,
        },
    }


def _process_message(
    session: Session,
    connection: ProviderConnectionORM,
    provider: str,
    item: dict[str, Any],
    policies: tuple[IcpConfig, RoutingConfig],
    notification_provider: NotificationProvider,
) -> str:
    duplicate = session.scalar(select(SocialMessageORM.id).where(
        SocialMessageORM.connection_id == connection.id,
        SocialMessageORM.provider_event_id == item["event_id"],
    ))
    if duplicate:
        return "duplicate"
    participant = item["participant_id"]
    conversation_identity = item["conversation_identity"] or participant
    account_id = _canonical_account_id(connection, item["account_id"])
    now = datetime.now(UTC)
    conversation = session.scalar(select(SocialConversationORM).where(
        SocialConversationORM.workspace_id == connection.workspace_id,
        SocialConversationORM.provider == provider,
        SocialConversationORM.connection_id == connection.id,
        SocialConversationORM.external_account_id == account_id,
        SocialConversationORM.participant_id == participant,
        SocialConversationORM.conversation_identity == conversation_identity,
    ))
    if conversation is None:
        conversation = SocialConversationORM(
            id=uuid4(),
            workspace_id=connection.workspace_id,
            connection_id=connection.id,
            provider=provider,
            external_account_id=account_id,
            participant_id=participant,
            conversation_identity=conversation_identity,
            created_at=now,
            updated_at=now,
        )
        session.add(conversation)
        session.flush()
    received_at = _timestamp(item["timestamp"])
    message = SocialMessageORM(
        id=uuid4(),
        workspace_id=connection.workspace_id,
        connection_id=connection.id,
        conversation_id=conversation.id,
        provider_event_id=item["event_id"],
        direction=item["direction"],
        content=item["content"],
        raw_payload=item["raw"],
        lead_id=conversation.lead_id,
        event_at=received_at,
        created_at=now,
    )
    session.add(message)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return "duplicate"

    plausible = (
        item["direction"] == "inbound"
        and bool(item["content"].strip())
        and bool(_INQUIRY.search(item["content"]))
    )
    if plausible:
        payload = _lead_payload(
            provider, str(connection.id), participant, conversation_identity,
            item["name"], item["content"], item["event_id"], received_at, item["raw"],
        )
        if conversation.lead_id:
            lead = session.get(LeadORM, conversation.lead_id)
            if lead is not None:
                _, _, lead = reprocess_lead(
                    session, lead, payload, *policies,
                    notification_provider=notification_provider,
                    reroute_reason="New qualified message in the same social conversation.",
                    approval_provider=provider,
                )
        else:
            event = SourceEvent.create(
                provider,
                item["event_id"],
                payload["raw_payload"],
                payload,
                received_at,
                {
                    "social_connection_id": str(connection.id),
                    "conversation_id": conversation_identity,
                },
            )
            intake = process_source_event(
                session, event, *policies,
                notification_provider=notification_provider,
                workspace_id=connection.workspace_id,
            )
            lead = intake.lead
            conversation.lead_id = lead.id
        message.lead_id = conversation.lead_id
    if conversation.lead_id:
        record_pipeline_event(
            session,
            conversation.lead_id,
            "social_message",
            item["direction"],
            {
                "provider": provider,
                "event_id": item["event_id"],
                "conversation_id": conversation_identity,
                "content": item["content"][:2000],
            },
        )
    conversation.updated_at = now
    session.commit()
    return "processed"


@router.get("")
def verify_meta_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Response:
    query = request.query_params
    if (
        query.get("hub.mode") != "subscribe"
        or not settings.webhook_signing_secret
        or not hmac.compare_digest(
            query.get("hub.verify_token", ""), settings.webhook_signing_secret
        )
    ):
        raise HTTPException(status_code=403, detail="Webhook verification failed")
    challenge = query.get("hub.challenge")
    if not challenge:
        raise HTTPException(status_code=400, detail="Missing verification challenge")
    return Response(content=challenge, media_type="text/plain")


@router.post("")
async def receive_meta_webhook(
    request: Request,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    settings: Settings = Depends(get_settings),
) -> dict[str, int]:
    body = await request.body()
    if len(body) > _MAX_BODY:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=400, detail="Invalid webhook JSON") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload")
    provider = _provider_for_object(payload.get("object"))
    if provider is None:
        raise HTTPException(status_code=400, detail="Unsupported Meta webhook object")
    _verify_signature(
        body, request.headers.get("x-hub-signature-256"), _app_secret(settings, provider)
    )
    entries = payload.get("entry", [])
    processed = duplicates = ignored = 0
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            ignored += 1
            continue
        account_ids = {str(entry.get("id") or "")}
        changes = entry.get("changes", [])
        for change in changes if isinstance(changes, list) else []:
            value = change.get("value", {}) if isinstance(change, dict) else {}
            metadata = value.get("metadata", {}) if isinstance(value, dict) else {}
            if isinstance(metadata, dict) and metadata.get("phone_number_id"):
                account_ids.add(str(metadata["phone_number_id"]))
        connection = _connected_account(session, provider, account_ids - {""})
        for item in _messages({"entry": [entry]}, provider):
            # Bind account resolution to a single verified workspace connection; never guess.
            if not _account_matches(connection, {item["account_id"]} | account_ids):
                ignored += 1
                continue
            outcome = _process_message(
                session, connection, provider, item, policies, notification_provider
            )
            if outcome == "duplicate":
                duplicates += 1
            elif outcome == "processed":
                processed += 1
            else:
                ignored += 1
    return {"processed": processed, "duplicates": duplicates, "ignored": ignored}
