from __future__ import annotations

from datetime import UTC, datetime
from email.utils import getaddresses, parseaddr
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.domain.models import RecordedLeadEvidence
from lead_agent.infrastructure.db.models import PipelineEventORM, ProviderConnectionORM


def lead_history_context(
    session: Session,
    *,
    workspace_id: UUID,
    lead_id: UUID,
    limit: int = 60,
) -> tuple[str, tuple[RecordedLeadEvidence, ...]]:
    """Return recent verified lead history for analysis and scoring."""
    events = list(
        session.scalars(
            select(PipelineEventORM)
            .where(
                PipelineEventORM.workspace_id == workspace_id,
                PipelineEventORM.lead_id == lead_id,
                PipelineEventORM.stage.in_(("manual_progress", "gmail_message")),
            )
            .order_by(PipelineEventORM.created_at.desc())
            .limit(limit)
        )
    )
    events.reverse()
    context_lines: list[str] = []
    evidence: list[RecordedLeadEvidence] = []
    for event in events:
        details = event.details if isinstance(event.details, dict) else {}
        if event.stage == "manual_progress":
            stage = str(details.get("stage", event.status))
            context = f"Sales stage {stage}: {details.get('note', '')}"
            context_lines.append(context)
            evidence.append(
                RecordedLeadEvidence(
                    source=f"manual_progress:{stage}",
                    text=context,
                    observed_at=event.created_at,
                )
            )
        else:
            direction = details.get("direction", "email")
            context = (
                f"Gmail {direction} email ({details.get('sent_at', '')}) "
                f"{details.get('subject', '')}: {details.get('body', '')}"
            )
            context_lines.append(context)
            evidence.append(
                RecordedLeadEvidence(
                    source="gmail_message",
                    text=context,
                    observed_at=event.created_at,
                )
            )
    return "\n".join(context_lines)[-24000:], tuple(evidence)


def gmail_history_messages(
    thread_messages: list[dict[str, Any]],
    *,
    lead_email: str,
    mailbox_email: str,
) -> list[dict[str, str]]:
    target = lead_email.strip().lower()
    mailbox = mailbox_email.strip().lower()
    rows: list[dict[str, str]] = []
    for item in thread_messages:
        headers = item.get("headers", {})
        if not isinstance(headers, dict):
            continue
        headers = {str(key).lower(): str(value) for key, value in headers.items()}
        sender_name, sender = parseaddr(headers.get("from", ""))
        sender = sender.strip().lower()
        to_recipients = [
            address.strip().lower()
            for _name, address in getaddresses([headers.get("to", "")])
            if address
        ]
        recipients = [
            address.strip().lower()
            for _name, address in getaddresses([headers.get("to", ""), headers.get("cc", "")])
            if address
        ]
        if sender != target and target not in to_recipients:
            continue
        sent_at = ""
        try:
            sent_at = datetime.fromtimestamp(
                int(item.get("internal_date") or "0") / 1000, tz=UTC
            ).isoformat()
        except (TypeError, ValueError, OSError):
            pass
        rows.append({
            "message_id": str(item.get("message_id") or ""),
            "thread_id": str(item.get("thread_id") or ""),
            "sender": sender,
            "sender_name": sender_name[:255],
            "recipients": ", ".join(recipients),
            "subject": headers.get("subject", "(no subject)")[:500],
            "sent_at": sent_at,
            "rfc_message_id": headers.get("message-id", ""),
            "direction": "outbound" if sender == mailbox else "inbound",
            "body": str(item.get("body") or "")[:8000],
        })
    rows.sort(key=lambda row: row["sent_at"])
    return rows


def append_gmail_thread_messages(
    session: Session,
    *,
    workspace_id: UUID,
    lead_id: UUID,
    connection_id: UUID,
    messages: list[dict[str, Any]],
    known_message_ids_by_connection: set[tuple[str, str]] | None = None,
    legacy_known_message_ids: set[str] | None = None,
    existing_events: list[PipelineEventORM] | None = None,
) -> int:
    """Save each Gmail message to a lead history once; return the number added."""
    if existing_events is None and (
        known_message_ids_by_connection is None or legacy_known_message_ids is None
    ):
        existing_events = list(
            session.scalars(
                select(PipelineEventORM).where(
                    PipelineEventORM.lead_id == lead_id,
                    PipelineEventORM.workspace_id == workspace_id,
                    PipelineEventORM.stage == "gmail_message",
                )
            )
        )
    existing_events = existing_events or []
    connection_key = str(connection_id)
    connection_ids_to_lookup: set[UUID] = {connection_id}
    for event in existing_events:
        details = event.details if isinstance(event.details, dict) else {}
        stored_connection_id = str(details.get("gmail_connection_id") or "")
        if stored_connection_id:
            try:
                connection_ids_to_lookup.add(UUID(stored_connection_id))
            except ValueError:
                continue

    mailbox_by_connection: dict[str, str] = {}
    if connection_ids_to_lookup and callable(getattr(session, "scalars", None)):
        rows = session.scalars(
            select(ProviderConnectionORM).where(
                ProviderConnectionORM.workspace_id == workspace_id,
                ProviderConnectionORM.provider_type == "gmail",
                ProviderConnectionORM.id.in_(connection_ids_to_lookup),
            )
        )
        mailbox_by_connection = {
            str(row.id): _gmail_mailbox_email(row)
            for row in rows
            if _gmail_mailbox_email(row)
        }

    known_events_by_connection: dict[tuple[str, str], PipelineEventORM] = {}
    legacy_events_by_message_id: dict[str, PipelineEventORM] = {}
    known_events_by_mailbox: dict[tuple[str, str], PipelineEventORM] = {}
    for event in existing_events:
        details = event.details if isinstance(event.details, dict) else {}
        message_id = str(details.get("message_id") or "")
        if not message_id:
            continue
        stored_connection_id = str(details.get("gmail_connection_id") or "")
        if stored_connection_id:
            known_events_by_connection[(stored_connection_id, message_id)] = event
            mailbox = _normalize_gmail_mailbox(
                details.get("gmail_mailbox_email")
                or mailbox_by_connection.get(stored_connection_id, "")
            )
            if mailbox:
                known_events_by_mailbox.setdefault((mailbox, message_id), event)
        else:
            legacy_events_by_message_id[message_id] = event

    if known_message_ids_by_connection is None:
        known_message_ids_by_connection = set(known_events_by_connection)
    if legacy_known_message_ids is None:
        legacy_known_message_ids = set(legacy_events_by_message_id)

    current_mailbox = mailbox_by_connection.get(connection_key, "")
    known_message_ids_by_mailbox = set(known_events_by_mailbox)
    added = 0
    now = datetime.now(UTC)
    for message in messages:
        message_id = str(message.get("message_id") or "").strip()
        scoped_id = (connection_key, message_id)
        if not message_id:
            continue
        known_event = known_events_by_connection.get(scoped_id)
        mailbox = _normalize_gmail_mailbox(
            message.get("gmail_mailbox_email") or current_mailbox
        )
        mailbox_scoped_id = (mailbox, message_id) if mailbox else None
        if known_event is None and mailbox_scoped_id is not None:
            known_event = known_events_by_mailbox.get(mailbox_scoped_id)
        if known_event is None and message_id in legacy_known_message_ids:
            known_event = legacy_events_by_message_id.get(message_id)
        if (
            scoped_id in known_message_ids_by_connection
            or message_id in legacy_known_message_ids
            or (
                mailbox_scoped_id is not None
                and mailbox_scoped_id in known_message_ids_by_mailbox
            )
        ):
            thread_id = str(message.get("thread_id") or "").strip()
            if known_event is not None and thread_id:
                details = (
                    known_event.details
                    if isinstance(known_event.details, dict)
                    else {}
                )
                if str(details.get("thread_id") or "") != thread_id:
                    known_event.details = {**details, "thread_id": thread_id}
            continue
        details = {**message, "gmail_connection_id": connection_key}
        if mailbox:
            details["gmail_mailbox_email"] = mailbox
        event_id = uuid5(
            NAMESPACE_URL, f"corral:gmail:{workspace_id}:{lead_id}:{connection_key}:{message_id}"
        )
        event_time = now
        sent_at = str(message.get("sent_at") or "")
        if sent_at:
            try:
                event_time = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
                if event_time.tzinfo is None:
                    event_time = event_time.replace(tzinfo=UTC)
            except ValueError:
                pass
        event = PipelineEventORM(
            id=event_id,
            workspace_id=workspace_id,
            lead_id=lead_id,
            stage="gmail_message",
            status=str(message.get("direction") or "inbound"),
            details=details,
            created_at=event_time,
        )
        try:
            with session.begin_nested():
                session.add(event)
                session.flush()
        except IntegrityError:
            # Concurrent importer/manual syncs may have stored this deterministic
            # message event after the known-ID query.
            if session.get(PipelineEventORM, event_id) is None:
                raise
        known_message_ids_by_connection.add(scoped_id)
        if mailbox_scoped_id is not None:
            known_message_ids_by_mailbox.add(mailbox_scoped_id)
            known_events_by_mailbox[mailbox_scoped_id] = event
        legacy_known_message_ids.discard(message_id)
        added += 1
    return added


def _normalize_gmail_mailbox(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if "<" in candidate:
        candidate = parseaddr(candidate)[1]
    else:
        candidate = candidate.split(",", 1)[0].strip()
    candidate = candidate.strip().lower()
    return candidate if "@" in candidate else ""


def _gmail_mailbox_email(connection: ProviderConnectionORM) -> str:
    metadata = (
        connection.connection_metadata
        if isinstance(connection.connection_metadata, dict)
        else {}
    )
    for key in ("gmail_email", "email", "account_email"):
        mailbox = _normalize_gmail_mailbox(metadata.get(key))
        if mailbox:
            return mailbox
    return _normalize_gmail_mailbox(connection.account_name)
