from __future__ import annotations

from datetime import UTC, datetime
from email.utils import getaddresses, parseaddr
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.domain.models import RecordedLeadEvidence
from lead_agent.infrastructure.db.models import PipelineEventORM


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
) -> int:
    """Save each Gmail message to a lead history once; return the number added."""
    if known_message_ids_by_connection is None or legacy_known_message_ids is None:
        known = list(
            session.scalars(
                select(PipelineEventORM).where(
                    PipelineEventORM.lead_id == lead_id,
                    PipelineEventORM.workspace_id == workspace_id,
                    PipelineEventORM.stage == "gmail_message",
                )
            )
        )
        known_message_ids_by_connection = {
            (str(event.details.get("gmail_connection_id")), str(event.details.get("message_id")))
            for event in known
            if isinstance(event.details, dict)
            and event.details.get("gmail_connection_id")
            and event.details.get("message_id")
        }
        legacy_known_message_ids = {
            str(event.details.get("message_id"))
            for event in known
            if isinstance(event.details, dict)
            and event.details.get("message_id")
            and not event.details.get("gmail_connection_id")
        }

    connection_key = str(connection_id)
    added = 0
    now = datetime.now(UTC)
    for message in messages:
        message_id = str(message.get("message_id") or "").strip()
        scoped_id = (connection_key, message_id)
        if (
            not message_id
            or scoped_id in known_message_ids_by_connection
            or message_id in legacy_known_message_ids
        ):
            continue
        details = {**message, "gmail_connection_id": connection_key}
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
        legacy_known_message_ids.discard(message_id)
        added += 1
    return added
