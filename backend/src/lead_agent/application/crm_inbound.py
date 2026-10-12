from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Lock, RLock
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.application.services import process_source_event, reprocess_lead
from lead_agent.domain.analysis import ReasoningProvider
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.crm_inbound import (
    CanonicalInboundLead,
    CrmInboundProvider,
    InboundCursor,
    InboundSyncMode,
)
from lead_agent.domain.intake import SourceEvent
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.domain.routing import NotificationProvider
from lead_agent.infrastructure.db.models import (
    CrmInboundSyncRunORM,
    CrmInboundSyncStateORM,
    CrmProviderRecordLinkORM,
    CrmSyncORM,
    LeadORM,
    ProviderConnectionORM,
)
from lead_agent.infrastructure.db.operations import ConsentEventORM
from lead_agent.infrastructure.observability import (
    current_request_id,
    get_observability,
    reset_request_id,
    set_request_id,
)
from lead_agent.infrastructure.provider_lifecycle import record_provider_lifecycle_audit

_SYNC_LOCKS: dict[tuple[UUID, UUID, str], RLock] = {}
_SYNC_LOCKS_GUARD = Lock()
_SYNC_LEASE_SECONDS = 300


@contextmanager
def _inbound_request_context() -> Iterator[None]:
    """Carry a scheduled run's correlation through analysis, routing, and delivery."""
    token = set_request_id(str(uuid4())) if current_request_id() == "-" else None
    try:
        yield
    finally:
        if token is not None:
            reset_request_id(token)


def _sync_lock(workspace_id: UUID, connection_id: UUID, object_type: str) -> RLock:
    # This only reduces same-process contention. The PostgreSQL lease below is
    # the authoritative guard for multi-worker and multi-process deployments.
    key = (workspace_id, connection_id, object_type)
    with _SYNC_LOCKS_GUARD:
        return _SYNC_LOCKS.setdefault(key, RLock())


def _state(
    session: Session,
    connection: ProviderConnectionORM,
    object_type: str,
) -> CrmInboundSyncStateORM:
    row = session.scalar(
        select(CrmInboundSyncStateORM).where(
            CrmInboundSyncStateORM.workspace_id == connection.workspace_id,
            CrmInboundSyncStateORM.connection_id == connection.id,
            CrmInboundSyncStateORM.provider == connection.provider_type,
            CrmInboundSyncStateORM.object_type == object_type,
        )
    )
    if row is not None:
        return row
    now = datetime.now(UTC)
    row = CrmInboundSyncStateORM(
        id=uuid4(),
        workspace_id=connection.workspace_id,
        connection_id=connection.id,
        provider=connection.provider_type,
        object_type=object_type,
        enabled=False,
        initial_import_completed=False,
        sync_interval_seconds=60,
        next_sync_at=None,
        status="disabled",
        records_processed=0,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(CrmInboundSyncStateORM).where(
                CrmInboundSyncStateORM.workspace_id == connection.workspace_id,
                CrmInboundSyncStateORM.connection_id == connection.id,
                CrmInboundSyncStateORM.provider == connection.provider_type,
                CrmInboundSyncStateORM.object_type == object_type,
            )
        )
        if existing is None:
            raise
        return existing
    return row


def configure_inbound_sync(
    session: Session,
    connection: ProviderConnectionORM,
    *,
    enabled: bool,
    object_type: str = "Lead",
    interval_seconds: int = 60,
    actor_id: str,
    initial_cursor: InboundCursor | None = None,
) -> CrmInboundSyncStateORM:
    """Deliberately enable or disable scheduled inbound acquisition."""

    if connection.provider_type not in {
        "salesforce",
        "hubspot",
        "gohighlevel",
        "mock",
        "gmail",
        "outlook",
    }:
        raise ValueError("provider does not support automatic inbound synchronization")
    if not connection.is_active or connection.status not in {"connected", "verified"}:
        raise ValueError("provider connection is inactive or unavailable")
    interval = max(60, min(interval_seconds, 86_400))
    state = _state(session, connection, object_type)
    if initial_cursor is not None:
        if state.last_successful_cursor or state.records_processed:
            raise ValueError("an existing synchronization checkpoint cannot be replaced")
        state.last_successful_cursor = dict(initial_cursor)
        state.initial_import_completed = True
    now = datetime.now(UTC)
    state.enabled = enabled
    state.sync_interval_seconds = interval
    state.next_sync_at = now if enabled else None
    state.status = "ready" if enabled else "disabled"
    state.updated_at = now
    record_provider_lifecycle_audit(
        session,
        connection,
        action=(
            f"{connection.provider_type}_inbound_{'enabled' if enabled else 'disabled'}"
            if connection.provider_type in {"gmail", "outlook"}
            else "inbound_sync_configuration"
        ),
        outcome="enabled" if enabled else "disabled",
        actor_id=actor_id,
        details={"object_type": object_type, "interval_seconds": interval},
    )
    session.commit()
    return state


def list_due_inbound_sync_states(
    session: Session,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> list[CrmInboundSyncStateORM]:
    due = now or datetime.now(UTC)
    return list(
        session.scalars(
            select(CrmInboundSyncStateORM)
            .join(
                ProviderConnectionORM,
                ProviderConnectionORM.id == CrmInboundSyncStateORM.connection_id,
            )
            .where(
                CrmInboundSyncStateORM.enabled.is_(True),
                CrmInboundSyncStateORM.next_sync_at.is_not(None),
                CrmInboundSyncStateORM.next_sync_at <= due,
                or_(
                    CrmInboundSyncStateORM.status != "running",
                    CrmInboundSyncStateORM.lease_expires_at.is_(None),
                    CrmInboundSyncStateORM.lease_expires_at <= due,
                ),
                ProviderConnectionORM.is_active.is_(True),
                ProviderConnectionORM.status.in_({"connected", "verified"}),
            )
            .order_by(CrmInboundSyncStateORM.next_sync_at.asc())
            .limit(max(1, min(limit, 500)))
        )
    )


def upgrade_default_mail_sync_intervals(session: Session, *, now: datetime | None = None) -> int:
    """Upgrade the old five-minute default without unpausing or resetting mail."""
    now = now or datetime.now(UTC)
    states = list(
        session.scalars(
            select(CrmInboundSyncStateORM)
            .join(
                ProviderConnectionORM,
                ProviderConnectionORM.id == CrmInboundSyncStateORM.connection_id,
            )
            .where(
                CrmInboundSyncStateORM.provider.in_({"gmail", "outlook"}),
                CrmInboundSyncStateORM.enabled.is_(True),
                CrmInboundSyncStateORM.sync_interval_seconds == 300,
                ProviderConnectionORM.is_active.is_(True),
                ProviderConnectionORM.status == "verified",
            )
        )
    )
    for state in states:
        state.sync_interval_seconds = 60
        # A running sync still owns its lease. Do not discard its checkpoint.
        if state.status != "running":
            state.next_sync_at = now
        state.updated_at = now
    if states:
        session.commit()
    return len(states)


def claim_due_inbound_sync_states(
    session: Session,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> list[CrmInboundSyncStateORM]:
    """Claim bounded due work by advancing its next-dispatch time atomically."""

    claimed_at = now or datetime.now(UTC)
    upgrade_default_mail_sync_intervals(session, now=claimed_at)
    rows = list(
        session.scalars(
            select(CrmInboundSyncStateORM)
            .join(
                ProviderConnectionORM,
                ProviderConnectionORM.id == CrmInboundSyncStateORM.connection_id,
            )
            .where(
                CrmInboundSyncStateORM.enabled.is_(True),
                CrmInboundSyncStateORM.next_sync_at.is_not(None),
                CrmInboundSyncStateORM.next_sync_at <= claimed_at,
                or_(
                    CrmInboundSyncStateORM.status != "running",
                    CrmInboundSyncStateORM.lease_expires_at.is_(None),
                    CrmInboundSyncStateORM.lease_expires_at <= claimed_at,
                ),
                ProviderConnectionORM.is_active.is_(True),
                ProviderConnectionORM.status.in_({"connected", "verified"}),
            )
            .order_by(CrmInboundSyncStateORM.next_sync_at.asc())
            .limit(max(1, min(limit, 500)))
            .with_for_update(skip_locked=True)
        )
    )
    for row in rows:
        row.next_sync_at = claimed_at + timedelta(seconds=row.sync_interval_seconds)
        row.updated_at = claimed_at
    session.commit()
    return rows


def _acquire_sync_lease(
    session: Session,
    state: CrmInboundSyncStateORM,
    owner: str,
    now: datetime,
) -> bool:
    """Acquire ownership atomically so separate workers cannot overlap a sync."""

    result = session.execute(
        update(CrmInboundSyncStateORM)
        .where(
            and_(
                CrmInboundSyncStateORM.id == state.id,
                CrmInboundSyncStateORM.enabled.is_(True),
                or_(
                    CrmInboundSyncStateORM.status != "running",
                    CrmInboundSyncStateORM.lease_expires_at.is_(None),
                    CrmInboundSyncStateORM.lease_expires_at <= now,
                ),
            )
        )
        .values(
            lease_owner=owner,
            lease_expires_at=now + timedelta(seconds=_SYNC_LEASE_SECONDS),
            status="running",
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    return bool(getattr(result, "rowcount", 0) == 1)


def _renew_sync_lease(
    session: Session,
    state_id: UUID,
    owner: str,
    now: datetime,
) -> None:
    result = session.execute(
        update(CrmInboundSyncStateORM)
        .where(
            CrmInboundSyncStateORM.id == state_id,
            CrmInboundSyncStateORM.lease_owner == owner,
            CrmInboundSyncStateORM.status == "running",
        )
        .values(
            lease_expires_at=now + timedelta(seconds=_SYNC_LEASE_SECONDS),
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if int(getattr(result, "rowcount", 0)) != 1:
        raise RuntimeError("inbound synchronization lease was lost")


def get_inbound_sync_state(
    session: Session,
    workspace_id: UUID,
    connection_id: UUID,
    object_type: str = "Lead",
) -> CrmInboundSyncStateORM | None:
    return session.scalar(
        select(CrmInboundSyncStateORM).where(
            CrmInboundSyncStateORM.workspace_id == workspace_id,
            CrmInboundSyncStateORM.connection_id == connection_id,
            CrmInboundSyncStateORM.object_type == object_type,
        )
    )


def list_inbound_sync_runs(
    session: Session,
    workspace_id: UUID,
    connection_id: UUID,
    *,
    limit: int = 25,
) -> list[CrmInboundSyncRunORM]:
    return list(
        session.scalars(
            select(CrmInboundSyncRunORM)
            .where(
                CrmInboundSyncRunORM.workspace_id == workspace_id,
                CrmInboundSyncRunORM.connection_id == connection_id,
            )
            .order_by(CrmInboundSyncRunORM.started_at.desc())
            .limit(max(1, min(limit, 100)))
        )
    )


def _record_link(
    session: Session,
    connection: ProviderConnectionORM,
    canonical: CanonicalInboundLead,
) -> CrmProviderRecordLinkORM | None:
    return session.scalar(
        select(CrmProviderRecordLinkORM).where(
            CrmProviderRecordLinkORM.workspace_id == connection.workspace_id,
            CrmProviderRecordLinkORM.connection_id == connection.id,
            CrmProviderRecordLinkORM.provider == canonical.provider,
            CrmProviderRecordLinkORM.object_type == canonical.object_type,
            CrmProviderRecordLinkORM.provider_record_id == canonical.provider_record_id,
        )
    )


def _email_matches(session: Session, workspace_id: UUID, email: str) -> list[LeadORM]:
    normalized = email.strip().lower()
    if not normalized:
        return []
    return list(
        session.scalars(
            select(LeadORM).where(
                LeadORM.workspace_id == workspace_id,
                func.lower(LeadORM.email) == normalized,
            )
        )
    )


def _safe_provider_metadata(canonical: CanonicalInboundLead) -> dict[str, object]:
    allowed = {
        "owner_id",
        "original_source",
        "description_hash",
        "gmail_thread_id",
        "gmail_message_id",
        "gmail_mailbox",
        "outlook_conversation_id",
        "outlook_message_id",
        "outlook_internet_message_id",
        "outlook_mailbox",
        "sender_email",
        "location_id",
        "dnd",
        "dnd_call",
        "dnd_email",
    }
    return {
        key: value
        for key, value in canonical.provider_metadata.items()
        if key in allowed and isinstance(value, (str, int, float, bool, type(None)))
    }


def _json_safe_payload(values: Mapping[str, object]) -> dict[str, object]:
    def safe(value: object) -> object:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, Mapping):
            return {str(key): safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [safe(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    return {str(key): safe(value) for key, value in values.items()}


def _hubspot_own_writeback_matches(
    link: CrmProviderRecordLinkORM,
    canonical: CanonicalInboundLead,
    outbound: CrmSyncORM | None,
) -> bool:
    if (
        link.provider != "hubspot"
        or link.synchronization_origin != "local_writeback"
        or link.last_writeback_at is None
        or canonical.provider_modified_at is None
        or outbound is None
        or outbound.status != "synced"
        or outbound.payload_hash != link.last_writeback_fingerprint
    ):
        return False
    modified = canonical.provider_modified_at
    written = link.last_writeback_at
    if modified.tzinfo is None:
        modified = modified.replace(tzinfo=UTC)
    if written.tzinfo is None:
        written = written.replace(tzinfo=UTC)
    if not written - timedelta(minutes=5) <= modified <= written + timedelta(minutes=5):
        return False
    previous_description = link.provider_metadata.get("description_hash")
    if not previous_description or previous_description != canonical.provider_metadata.get(
        "description_hash"
    ):
        return False
    if link.provider_source != canonical.provider_source or link.provider_metadata.get(
        "owner_id"
    ) != canonical.provider_metadata.get("owner_id"):
        return False
    source = canonical.normalized_payload
    sent = outbound.payload
    field_pairs = (
        ("full_name", "full_name"),
        ("email", "email"),
        ("phone", "phone"),
        ("company_name", "company"),
        ("job_title", "title"),
    )
    return all(
        str(source.get(source_field) or "").strip() == str(sent.get(outbound_field) or "").strip()
        for source_field, outbound_field in field_pairs
    )


def _new_link(
    connection: ProviderConnectionORM,
    canonical: CanonicalInboundLead,
    lead: LeadORM,
    matching_strategy: str,
) -> CrmProviderRecordLinkORM:
    now = datetime.now(UTC)
    status = (
        "provider_deleted"
        if canonical.deleted
        else "provider_archived"
        if canonical.archived
        else "active"
    )
    return CrmProviderRecordLinkORM(
        id=uuid4(),
        workspace_id=connection.workspace_id,
        connection_id=connection.id,
        provider=canonical.provider,
        object_type=canonical.object_type,
        provider_record_id=canonical.provider_record_id,
        lead_id=lead.id,
        status=status,
        matching_strategy=matching_strategy,
        provider_source=canonical.provider_source,
        provider_metadata=_safe_provider_metadata(canonical),
        provider_modified_at=canonical.provider_modified_at,
        provider_version=canonical.provider_version,
        source_fingerprint=canonical.source_fingerprint,
        enrichment_fingerprint=canonical.enrichment_fingerprint,
        synchronization_origin="provider",
        last_change_kind="source_changed",
        last_seen_at=now,
        last_imported_at=now,
        created_at=now,
        updated_at=now,
    )


def _contact_update_payload(
    canonical: CanonicalInboundLead, lead: LeadORM, payload: dict[str, Any]
) -> dict[str, Any]:
    if canonical.provider != "gohighlevel":
        return payload
    # A contact-only source has no commercial inquiry or ICP enrichment fields.
    # Do not erase a rep's existing evidence when its contact details change.
    return {
        **payload,
        **{
            field: getattr(lead, field)
            for field in (
                "job_title",
                "industry",
                "employee_count",
                "annual_revenue",
                "geography",
                "budget",
                "product_or_service",
                "message",
                "received_at",
            )
        },
    }


def _process_record(
    session: Session,
    connection: ProviderConnectionORM,
    provider: CrmInboundProvider,
    canonical: CanonicalInboundLead,
    icp: IcpConfig,
    routing: RoutingConfig,
    reasoning_provider: ReasoningProvider | None,
    notification_provider: NotificationProvider | None,
    *,
    actor_id: str,
    correlation_id: str,
    send_confirmation_email: bool,
) -> str:
    normalized_payload = _json_safe_payload(canonical.normalized_payload)
    link = _record_link(session, connection, canonical)
    now = datetime.now(UTC)
    if canonical.provider == "gohighlevel":
        email = str(normalized_payload.get("email") or "").strip().lower()
        for channel, flag in (("all", "dnd"), ("call", "dnd_call"), ("email", "dnd_email")):
            if not canonical.provider_metadata.get(flag):
                continue
            latest = session.scalar(
                select(ConsentEventORM)
                .where(
                    ConsentEventORM.workspace_id == connection.workspace_id,
                    ConsentEventORM.email == email,
                    ConsentEventORM.channel == channel,
                )
                .order_by(ConsentEventORM.created_at.desc(), ConsentEventORM.id.desc())
            )
            if latest is None or latest.status not in {"denied", "withdrawn"}:
                session.add(
                    ConsentEventORM(
                        workspace_id=connection.workspace_id,
                        email=email,
                        channel=channel,
                        status="denied",
                        basis="provider_opt_out",
                        purpose="Do not contact",
                        evidence=(
                            f"HighLevel contact {canonical.provider_record_id} reports {flag}."
                        ),
                        actor_id=actor_id,
                        created_at=now,
                    )
                )
        session.flush()
    if canonical.deleted or canonical.archived:
        if link is not None:
            link.status = "provider_deleted" if canonical.deleted else "provider_archived"
            link.provider_modified_at = canonical.provider_modified_at
            link.provider_version = canonical.provider_version
            link.last_seen_at = now
            link.last_change_kind = link.status
            link.updated_at = now
            session.commit()
        return "skipped"

    if link is not None:
        lead = session.scalar(
            select(LeadORM).where(
                LeadORM.id == link.lead_id,
                LeadORM.workspace_id == connection.workspace_id,
            )
        )
        if lead is None:
            raise ValueError("provider record mapping references an unavailable lead")
        if link.source_fingerprint != canonical.source_fingerprint:
            outbound = (
                session.scalar(
                    select(CrmSyncORM)
                    .where(
                        CrmSyncORM.workspace_id == connection.workspace_id,
                        CrmSyncORM.lead_id == lead.id,
                        CrmSyncORM.provider == "hubspot",
                        CrmSyncORM.provider_connection_id == connection.id,
                        CrmSyncORM.is_current.is_(True),
                    )
                    .order_by(CrmSyncORM.requested_at.desc())
                )
                if link.provider == "hubspot"
                else None
            )
            if _hubspot_own_writeback_matches(link, canonical, outbound):
                link.source_fingerprint = canonical.source_fingerprint
                link.provider_modified_at = canonical.provider_modified_at
                link.provider_version = canonical.provider_version
                link.provider_metadata = _safe_provider_metadata(canonical)
                link.enrichment_fingerprint = canonical.enrichment_fingerprint
                link.last_change_kind = "own_writeback"
                link.last_seen_at = now
                link.updated_at = now
                session.commit()
                return "unchanged"
        if link.source_fingerprint == canonical.source_fingerprint:
            provider_modified_at = canonical.provider_modified_at
            last_writeback_at = link.last_writeback_at
            if provider_modified_at is not None and provider_modified_at.tzinfo is None:
                provider_modified_at = provider_modified_at.replace(tzinfo=UTC)
            if last_writeback_at is not None and last_writeback_at.tzinfo is None:
                last_writeback_at = last_writeback_at.replace(tzinfo=UTC)
            observed_after_writeback = bool(
                provider_modified_at
                and last_writeback_at
                and provider_modified_at >= last_writeback_at - timedelta(minutes=5)
            )
            own_writeback = bool(
                canonical.synchronization_origin == str(lead.id)
                or (
                    canonical.enrichment_fingerprint
                    and canonical.enrichment_fingerprint == link.enrichment_fingerprint
                    and link.synchronization_origin == "local_writeback"
                )
                or (link.synchronization_origin == "local_writeback" and observed_after_writeback)
            )
            link.status = "active"
            link.provider_modified_at = canonical.provider_modified_at
            link.provider_version = canonical.provider_version
            link.enrichment_fingerprint = canonical.enrichment_fingerprint
            link.provider_source = canonical.provider_source
            link.provider_metadata = _safe_provider_metadata(canonical)
            link.last_change_kind = "own_writeback" if own_writeback else "no_meaningful_change"
            link.last_seen_at = now
            link.updated_at = now
            session.commit()
            return "unchanged"
        reprocess_lead(
            session,
            lead,
            _contact_update_payload(canonical, lead, normalized_payload),
            icp,
            routing,
            provider=reasoning_provider,
            notification_provider=notification_provider,
            send_confirmation_email=send_confirmation_email,
            preserve_inquiry=canonical.provider == "gohighlevel",
        )
        link = _record_link(session, connection, canonical)
        if link is None:
            raise RuntimeError("provider record mapping disappeared during update")
        link.status = "active"
        link.provider_source = canonical.provider_source
        link.provider_metadata = _safe_provider_metadata(canonical)
        link.provider_modified_at = canonical.provider_modified_at
        link.provider_version = canonical.provider_version
        link.source_fingerprint = canonical.source_fingerprint
        link.enrichment_fingerprint = canonical.enrichment_fingerprint
        link.synchronization_origin = "provider"
        link.last_change_kind = "source_changed"
        link.last_seen_at = now
        link.last_imported_at = now
        link.updated_at = now
        session.commit()
        return "updated"

    email = str(normalized_payload.get("email") or "")
    matches = _email_matches(session, connection.workspace_id, email)
    if len(matches) == 1:
        lead = matches[0]
        reprocess_lead(
            session,
            lead,
            _contact_update_payload(canonical, lead, normalized_payload),
            icp,
            routing,
            provider=reasoning_provider,
            notification_provider=notification_provider,
            send_confirmation_email=send_confirmation_email,
            preserve_inquiry=canonical.provider == "gohighlevel",
        )
        strategy = "exact_email"
        outcome = "updated"
    else:
        event = SourceEvent.create(
            channel=(
                canonical.provider
                if canonical.provider in {"gmail", "outlook"}
                else f"crm:{canonical.provider}"
            ),
            external_id=f"{canonical.object_type}:{canonical.provider_record_id}",
            raw_payload={
                "provider": canonical.provider,
                "object_type": canonical.object_type,
                "provider_record_id": canonical.provider_record_id,
                "source_fingerprint": canonical.source_fingerprint,
                **(
                    {
                        "gmail_message_id": canonical.provider_record_id,
                        "gmail_thread_id": canonical.provider_metadata.get("gmail_thread_id"),
                        "gmail_mailbox": canonical.provider_metadata.get("gmail_mailbox"),
                    }
                    if canonical.provider == "gmail"
                    else {
                        "outlook_message_id": canonical.provider_record_id,
                        "outlook_conversation_id": canonical.provider_metadata.get(
                            "outlook_conversation_id"
                        ),
                        "outlook_internet_message_id": canonical.provider_metadata.get(
                            "outlook_internet_message_id"
                        ),
                        "outlook_mailbox": canonical.provider_metadata.get("outlook_mailbox"),
                    }
                    if canonical.provider == "outlook"
                    else {}
                ),
            },
            normalized_payload=normalized_payload,
            received_at=canonical.provider_modified_at,
            metadata={"provider_import": True},
        )
        result = process_source_event(
            session,
            event,
            icp,
            routing,
            provider=reasoning_provider,
            notification_provider=notification_provider,
            workspace_id=connection.workspace_id,
            send_confirmation_email=send_confirmation_email,
        )
        lead = result.lead
        strategy = "new_provider_record" if not matches else "ambiguous_email_created_separate"
        outcome = "created"
    session.add(_new_link(connection, canonical, lead, strategy))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = _record_link(session, connection, canonical)
        if existing is None:
            raise
        return "unchanged"
    if canonical.provider in {"gmail", "outlook"}:
        record_provider_lifecycle_audit(
            session,
            connection,
            action=(
                "gmail_candidate_imported"
                if canonical.provider == "gmail"
                else "outlook_candidate_imported"
            ),
            outcome=outcome,
            actor_id=actor_id,
            correlation_id=correlation_id,
            details={
                "message_id": canonical.provider_record_id,
                "thread_id": canonical.provider_metadata.get("gmail_thread_id"),
                "lead_id": str(lead.id),
            },
        )
    return outcome


def _start_cursor(state: CrmInboundSyncStateORM, mode: InboundSyncMode) -> InboundCursor | None:
    if state.resume_cursor:
        return dict(state.resume_cursor)
    if mode in {"incremental", "manual"}:
        return dict(state.last_successful_cursor) if state.last_successful_cursor else None
    return None


def _fail_run(
    session: Session,
    state: CrmInboundSyncStateORM,
    run: CrmInboundSyncRunORM,
    error: ProviderOperationError,
    connection: ProviderConnectionORM,
) -> CrmInboundSyncRunORM:
    now = datetime.now(UTC)
    run.status = "failed"
    run.error_category = error.category
    run.error_code = error.code
    run.last_error = str(error)[:2000]
    run.completed_at = now
    run.updated_at = now
    state.status = "failed"
    state.lease_owner = None
    state.lease_expires_at = None
    state.resume_cursor = run.cursor_current
    state.error_category = error.category
    state.error_code = error.code
    state.last_error = str(error)[:2000]
    state.next_sync_at = now + timedelta(seconds=max(60, state.sync_interval_seconds))
    state.updated_at = now
    record_provider_lifecycle_audit(
        session,
        connection,
        action="inbound_sync",
        outcome="failed",
        actor_id=run.requested_by,
        correlation_id=run.correlation_id,
        error_code=error.code,
        details={
            "mode": run.mode,
            "object_type": run.object_type,
            "records_fetched": run.records_fetched,
            "retryable": error.retryable,
            "category": error.category,
        },
    )
    session.commit()
    get_observability().provider_failure(
        run.provider,
        error.category,
        connection_id=str(connection.id),
        operation="crm_inbound_sync",
        error_code=error.code,
    )
    return run


def run_inbound_sync(
    session: Session,
    connection: ProviderConnectionORM,
    provider: CrmInboundProvider,
    *,
    mode: InboundSyncMode,
    object_type: str,
    limit: int,
    page_size: int,
    actor_id: str,
    icp: IcpConfig,
    routing: RoutingConfig,
    reasoning_provider: ReasoningProvider | None = None,
    notification_provider: NotificationProvider | None = None,
    send_confirmation_email: bool = True,
    before_page: Callable[[CrmInboundSyncRunORM], None] | None = None,
) -> CrmInboundSyncRunORM:
    """Run one bounded, resumable provider import without retaining whole-CRM pages."""

    if provider.provider_name != connection.provider_type:
        raise ValueError("inbound provider does not match the connection")
    if getattr(provider, "connection_id", None) != connection.id:
        raise ValueError("inbound provider connection identity does not match")
    if not connection.is_active or connection.status not in {"connected", "verified"}:
        raise ValueError("provider connection is inactive or unavailable")
    bounded_limit = max(1, min(limit, 5000))
    bounded_page = max(1, min(page_size, 200, bounded_limit))

    with (
        _inbound_request_context(),
        _sync_lock(connection.workspace_id, connection.id, object_type),
    ):
        state = _state(session, connection, object_type)
        if not state.enabled:
            raise ValueError("inbound synchronization is disabled")
        run_id = uuid4()
        if not _acquire_sync_lease(session, state, str(run_id), datetime.now(UTC)):
            raise ValueError("an inbound synchronization run is already active")
        session.refresh(state)
        active = session.scalar(
            select(CrmInboundSyncRunORM)
            .where(
                CrmInboundSyncRunORM.state_id == state.id,
                CrmInboundSyncRunORM.status == "running",
                CrmInboundSyncRunORM.id != run_id,
            )
            .order_by(CrmInboundSyncRunORM.started_at.desc())
        )
        if active is not None:
            active.status = "abandoned"
            active.error_category = "temporarily_unavailable"
            active.error_code = "worker_lost"
            active.last_error = "Previous worker lease expired before completion."
            active.completed_at = datetime.now(UTC)
            active.updated_at = datetime.now(UTC)
        cursor = _start_cursor(state, mode)
        now = datetime.now(UTC)
        request_id = current_request_id()
        correlation_id = request_id if request_id != "-" else str(uuid4())
        run = CrmInboundSyncRunORM(
            id=run_id,
            workspace_id=connection.workspace_id,
            connection_id=connection.id,
            state_id=state.id,
            provider=connection.provider_type,
            object_type=object_type,
            mode=mode,
            status="running",
            requested_by=actor_id[:255],
            correlation_id=correlation_id[:255],
            cursor_start=cursor,
            cursor_current=cursor,
            records_fetched=0,
            records_created=0,
            records_updated=0,
            records_unchanged=0,
            records_skipped=0,
            records_failed=0,
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        state.error_category = None
        state.error_code = None
        state.last_error = None
        state.updated_at = now
        session.add(run)
        record_provider_lifecycle_audit(
            session,
            connection,
            action=(
                f"{connection.provider_type}_{mode}_sync"
                if connection.provider_type in {"gmail", "outlook"}
                else "inbound_sync"
            ),
            outcome="started",
            actor_id=actor_id,
            correlation_id=correlation_id,
            details={"mode": mode, "object_type": object_type, "limit": bounded_limit},
        )
        session.commit()

        final_checkpoint = state.last_successful_cursor
        while run.records_fetched < bounded_limit:
            if before_page is not None:
                before_page(run)
            remaining = bounded_limit - run.records_fetched
            request_size = min(bounded_page, remaining)
            page_start = cursor
            try:
                page = provider.list_records(
                    object_type=object_type,
                    cursor=cursor,
                    limit=request_size,
                )
            except ProviderOperationError as error:
                run.cursor_current = page_start
                return _fail_run(session, state, run, error, connection)
            except Exception as error:
                translated = ProviderOperationError(
                    "Inbound provider request failed",
                    provider=connection.provider_type,
                    code=f"{connection.provider_type}_inbound_unexpected_failure",
                    category="temporarily_unavailable",
                    retryable=True,
                )
                translated.__cause__ = error
                run.cursor_current = page_start
                return _fail_run(session, state, run, translated, connection)

            if not page.records:
                cursor = page.next_cursor
                if page.checkpoint:
                    final_checkpoint = page.checkpoint
                break
            run.records_fetched += len(page.records)
            session.commit()
            for record in page.records:
                try:
                    canonical = provider.normalize_record(record)
                    outcome = _process_record(
                        session,
                        connection,
                        provider,
                        canonical,
                        icp,
                        routing,
                        reasoning_provider,
                        notification_provider,
                        actor_id=actor_id,
                        correlation_id=run.correlation_id,
                        send_confirmation_email=send_confirmation_email,
                    )
                except ProviderOperationError as error:
                    run = session.get(CrmInboundSyncRunORM, run.id) or run
                    run.records_failed += 1
                    run.cursor_current = page_start
                    session.commit()
                    return _fail_run(session, state, run, error, connection)
                except ValueError as error:
                    run = session.get(CrmInboundSyncRunORM, run.id) or run
                    run.records_skipped += 1
                    if connection.provider_type in {"gmail", "outlook"}:
                        record_provider_lifecycle_audit(
                            session,
                            connection,
                            action=(
                                "gmail_candidate_skipped"
                                if connection.provider_type == "gmail"
                                else "outlook_candidate_skipped"
                            ),
                            outcome="skipped",
                            actor_id=actor_id,
                            correlation_id=run.correlation_id,
                            details={
                                "message_id": record.record_id,
                                "reason": str(error)[:128],
                            },
                        )
                    session.commit()
                    continue
                run = session.get(CrmInboundSyncRunORM, run.id) or run
                if outcome == "created":
                    run.records_created += 1
                elif outcome == "updated":
                    run.records_updated += 1
                elif outcome == "unchanged":
                    run.records_unchanged += 1
                else:
                    run.records_skipped += 1
                session.commit()

            final_checkpoint = page.checkpoint or final_checkpoint
            cursor = page.next_cursor or page.checkpoint
            run = session.get(CrmInboundSyncRunORM, run.id) or run
            run.cursor_current = cursor
            run.checkpoint = final_checkpoint
            state = session.get(CrmInboundSyncStateORM, state.id) or state
            _renew_sync_lease(session, state.id, str(run.id), datetime.now(UTC))
            state.resume_cursor = cursor
            state.records_processed += len(page.records)
            state.updated_at = datetime.now(UTC)
            record_provider_lifecycle_audit(
                session,
                connection,
                action="inbound_sync_batch",
                outcome="completed",
                actor_id=actor_id,
                correlation_id=run.correlation_id,
                details={
                    "run_id": str(run.id),
                    "records_fetched": len(page.records),
                    "created": run.records_created,
                    "updated": run.records_updated,
                    "unchanged": run.records_unchanged,
                    "skipped": run.records_skipped,
                },
            )
            session.commit()
            if page.next_cursor is None and len(page.records) < request_size:
                cursor = None
                break

        run = session.get(CrmInboundSyncRunORM, run.id) or run
        state = session.get(CrmInboundSyncStateORM, state.id) or state
        now = datetime.now(UTC)
        bounded = run.records_fetched >= bounded_limit and cursor is not None
        run.status = "completed_bounded" if bounded else "completed"
        run.cursor_current = cursor
        run.checkpoint = final_checkpoint
        run.completed_at = now
        run.updated_at = now
        state.status = "ready"
        state.lease_owner = None
        state.lease_expires_at = None
        state.last_successful_cursor = final_checkpoint
        state.resume_cursor = cursor if bounded else None
        state.last_successful_sync_at = now
        if mode == "initial" and not bounded:
            state.initial_import_completed = True
        state.next_sync_at = now + timedelta(seconds=state.sync_interval_seconds)
        state.error_category = None
        state.error_code = None
        state.last_error = None
        state.updated_at = now
        record_provider_lifecycle_audit(
            session,
            connection,
            action=(
                f"{connection.provider_type}_{mode}_sync"
                if connection.provider_type in {"gmail", "outlook"}
                else "inbound_sync"
            ),
            outcome=run.status,
            actor_id=actor_id,
            correlation_id=run.correlation_id,
            details={
                "run_id": str(run.id),
                "mode": mode,
                "object_type": object_type,
                "records_fetched": run.records_fetched,
                "created": run.records_created,
                "updated": run.records_updated,
                "unchanged": run.records_unchanged,
                "skipped": run.records_skipped,
                "failed": run.records_failed,
            },
        )
        session.commit()
        return run
