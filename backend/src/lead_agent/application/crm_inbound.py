from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from threading import Lock, RLock
from uuid import UUID, uuid4

from sqlalchemy import func, select
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
    LeadORM,
    ProviderConnectionORM,
)
from lead_agent.infrastructure.observability import current_request_id, get_observability
from lead_agent.infrastructure.provider_lifecycle import record_provider_lifecycle_audit

_SYNC_LOCKS: dict[tuple[UUID, UUID, str], RLock] = {}
_SYNC_LOCKS_GUARD = Lock()


def _sync_lock(workspace_id: UUID, connection_id: UUID, object_type: str) -> RLock:
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
        enabled=True,
        status="idle",
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
    allowed = {"owner_id", "original_source"}
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


def _process_record(
    session: Session,
    connection: ProviderConnectionORM,
    provider: CrmInboundProvider,
    canonical: CanonicalInboundLead,
    icp: IcpConfig,
    routing: RoutingConfig,
    reasoning_provider: ReasoningProvider | None,
    notification_provider: NotificationProvider | None,
) -> str:
    normalized_payload = _json_safe_payload(canonical.normalized_payload)
    link = _record_link(session, connection, canonical)
    now = datetime.now(UTC)
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
                or (
                    link.synchronization_origin == "local_writeback"
                    and observed_after_writeback
                )
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
            normalized_payload,
            icp,
            routing,
            provider=reasoning_provider,
            notification_provider=notification_provider,
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
            normalized_payload,
            icp,
            routing,
            provider=reasoning_provider,
            notification_provider=notification_provider,
        )
        strategy = "exact_email"
        outcome = "updated"
    else:
        event = SourceEvent.create(
            channel=f"crm:{canonical.provider}",
            external_id=f"{canonical.object_type}:{canonical.provider_record_id}",
            raw_payload={
                "provider": canonical.provider,
                "object_type": canonical.object_type,
                "provider_record_id": canonical.provider_record_id,
                "source_fingerprint": canonical.source_fingerprint,
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
            sync_crm=False,
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
    state.resume_cursor = run.cursor_current
    state.error_category = error.category
    state.error_code = error.code
    state.last_error = str(error)[:2000]
    state.updated_at = now
    record_provider_lifecycle_audit(
        session,
        connection,
        action="inbound_sync",
        outcome="failed",
        actor_id=run.requested_by,
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

    with _sync_lock(connection.workspace_id, connection.id, object_type):
        state = _state(session, connection, object_type)
        if not state.enabled:
            raise ValueError("inbound synchronization is disabled")
        active = session.scalar(
            select(CrmInboundSyncRunORM).where(
                CrmInboundSyncRunORM.state_id == state.id,
                CrmInboundSyncRunORM.status == "running",
            )
        )
        if active is not None:
            raise ValueError("an inbound synchronization run is already active")
        cursor = _start_cursor(state, mode)
        now = datetime.now(UTC)
        request_id = current_request_id()
        correlation_id = request_id if request_id != "-" else str(uuid4())
        run = CrmInboundSyncRunORM(
            id=uuid4(),
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
        state.status = "running"
        state.error_category = None
        state.error_code = None
        state.last_error = None
        state.updated_at = now
        session.add(run)
        record_provider_lifecycle_audit(
            session,
            connection,
            action="inbound_sync",
            outcome="started",
            actor_id=actor_id,
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
                    )
                except ProviderOperationError as error:
                    run = session.get(CrmInboundSyncRunORM, run.id) or run
                    run.records_failed += 1
                    run.cursor_current = page_start
                    session.commit()
                    return _fail_run(session, state, run, error, connection)
                except ValueError:
                    run = session.get(CrmInboundSyncRunORM, run.id) or run
                    run.records_skipped += 1
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
            state.resume_cursor = cursor
            state.records_processed += len(page.records)
            state.updated_at = datetime.now(UTC)
            record_provider_lifecycle_audit(
                session,
                connection,
                action="inbound_sync_batch",
                outcome="completed",
                actor_id=actor_id,
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
        state.last_successful_cursor = final_checkpoint
        state.resume_cursor = cursor if bounded else None
        state.last_successful_sync_at = now
        state.error_category = None
        state.error_code = None
        state.last_error = None
        state.updated_at = now
        record_provider_lifecycle_audit(
            session,
            connection,
            action="inbound_sync",
            outcome=run.status,
            actor_id=actor_id,
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
