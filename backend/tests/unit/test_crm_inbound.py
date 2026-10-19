from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.main import app
from lead_agent.application.crm import sync_lead_to_crm
from lead_agent.application.crm_inbound import (
    _contact_update_payload,
    _hubspot_own_writeback_matches,
    _process_record,
    configure_inbound_sync,
    get_inbound_sync_state,
    run_inbound_sync,
    upgrade_default_mail_sync_intervals,
)
from lead_agent.application.gmail_history import gmail_history_messages
from lead_agent.application.services import process_lead
from lead_agent.domain.crm_inbound import (
    CanonicalInboundLead,
    ExternalCrmRecord,
    InboundCrmPage,
    InboundCursor,
)
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.crm.hubspot import HubSpotInboundCrmProvider
from lead_agent.infrastructure.crm.inbound_factory import (
    get_workspace_inbound_crm_resolver,
)
from lead_agent.infrastructure.crm.salesforce import SalesforceInboundCrmProvider
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    CrmInboundSyncRunORM,
    CrmProviderRecordLinkORM,
    LeadORM,
    PipelineEventORM,
    ProviderConnectionORM,
    ProviderLifecycleAuditORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.repositories import ensure_workspace


class FakeResponse:
    def __init__(self, status_code: int, body: Any = None) -> None:
        self.status_code = status_code
        self.body = body if body is not None else {}
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        return self.body


class FakeTransport:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"Unexpected HTTP call: {method} {url}")
        return self.responses.pop(0)


def _hash(values: dict[str, Any]) -> str:
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _record(
    record_id: str,
    email: str,
    *,
    company: str = "Northstar SaaS",
    message: str = "We need lead routing automation this month with an approved budget.",
    version: int = 1,
    origin: str | None = None,
    deleted: bool = False,
) -> ExternalCrmRecord:
    modified = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=version)
    return ExternalCrmRecord(
        provider="salesforce",
        object_type="Lead",
        record_id=record_id,
        values={
            "full_name": f"Lead {record_id}",
            "email": email,
            "company_name": company,
            "phone": "+1-555-0100",
            "job_title": "VP of Operations",
            "industry": "SaaS",
            "employee_count": 180,
            "geography": "United States",
            "product_or_service": "Lead routing automation",
            "message": message,
            "provider_source": "Web",
            "origin": origin,
        },
        modified_at=modified,
        provider_version=str(version),
        deleted=deleted,
    )


class FakeInboundProvider:
    provider_name = "salesforce"

    def __init__(
        self,
        connection_id: UUID,
        records: list[ExternalCrmRecord],
        *,
        fail_call: int | None = None,
    ) -> None:
        self.connection_id = connection_id
        self.records = records
        self.fail_call = fail_call
        self.calls: list[InboundCursor | None] = []

    def list_records(
        self,
        *,
        object_type: str,
        cursor: InboundCursor | None,
        limit: int,
    ) -> InboundCrmPage:
        assert object_type == "Lead"
        self.calls.append(cursor)
        if self.fail_call is not None and len(self.calls) == self.fail_call:
            raise ProviderOperationError(
                "Salesforce rate limit reached",
                provider="salesforce",
                code="salesforce_rate_limited",
                category="rate_limited",
                retryable=True,
            )
        offset = int((cursor or {}).get("offset", 0))
        values = self.records[offset : offset + limit]
        end = offset + len(values)
        checkpoint = {"offset": end}
        next_cursor = checkpoint if end < len(self.records) else None
        return InboundCrmPage(
            records=values,
            next_cursor=next_cursor,
            checkpoint=checkpoint,
        )

    def fetch_record(self, *, object_type: str, record_id: str) -> ExternalCrmRecord:
        return next(
            item
            for item in self.records
            if item.object_type == object_type and item.record_id == record_id
        )

    def normalize_record(self, record: ExternalCrmRecord) -> CanonicalInboundLead:
        values = record.values
        source_values = {
            key: values.get(key)
            for key in (
                "full_name",
                "email",
                "company_name",
                "phone",
                "job_title",
                "industry",
                "employee_count",
                "geography",
                "provider_source",
                "message",
            )
        }
        payload = {
            **source_values,
            "source": "web",
            "source_detail": "crm-import:salesforce:Web",
            "external_id": record.record_id,
            "product_or_service": values.get("product_or_service"),
            "received_at": record.modified_at,
        }
        return CanonicalInboundLead(
            provider="salesforce",
            object_type="Lead",
            provider_record_id=record.record_id,
            normalized_payload=payload,
            source_fingerprint=_hash(source_values),
            provider_modified_at=record.modified_at,
            provider_version=record.provider_version,
            provider_source="Web",
            provider_metadata={"owner_id": "005-test", "original_source": "Web"},
            synchronization_origin=(str(values.get("origin")) if values.get("origin") else None),
            deleted=record.deleted,
        )


def _session(tmp_path, name: str = "inbound.db") -> Session:
    engine = create_engine(
        f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return Session(engine)


@pytest.mark.parametrize("provider,enabled,interval,active,expected", [
    ("gmail", True, 300, True, 60),
    ("outlook", True, 300, True, 60),
    ("gmail", False, 300, True, 300),
    ("gmail", True, 600, True, 600),
    ("gmail", True, 300, False, 300),
    ("salesforce", True, 300, True, 300),
])
def test_fast_mail_default_preserves_pause_custom_interval_and_checkpoint(
    tmp_path, provider, enabled, interval, active, expected,
):
    with _session(tmp_path) as session:
        connection = _connection(session, uuid4(), provider_type=provider)
        state = configure_inbound_sync(
            session, connection, enabled=enabled, object_type="message",
            interval_seconds=interval, actor_id="test-user",
        )
        state.last_successful_cursor = {"history_id": "saved-checkpoint"}
        state.initial_import_completed = True
        state.records_processed = 7
        connection.is_active = active
        session.commit()
        count = upgrade_default_mail_sync_intervals(session)
        assert count == int(expected != interval)
        assert state.sync_interval_seconds == expected
        assert state.enabled == enabled
        assert state.last_successful_cursor == {"history_id": "saved-checkpoint"}
        assert state.initial_import_completed and state.records_processed == 7


def _connection(
    session: Session,
    workspace_id: UUID,
    *,
    account: str = "test",
    provider_type: str = "salesforce",
):
    ensure_workspace(session, workspace_id, name=f"Workspace {workspace_id}")
    now = datetime.now(UTC)
    row = ProviderConnectionORM(
        id=uuid4(),
        workspace_id=workspace_id,
        provider_type=provider_type,
        external_account_id=account,
        account_name=account,
        auth_type="oauth2",
        status="verified",
        scopes=(
            ["crm.objects.contacts.read", "crm.objects.contacts.write"]
            if provider_type == "hubspot"
            else ["api", "refresh_token"]
        ),
        secret_reference="secret-reference",
        credential_state="configured",
        last_validation_result={"verified": True},
        connection_metadata={},
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return row


def _run(session, connection, provider, icp, routing, **kwargs):
    configure_inbound_sync(
        session,
        connection,
        enabled=True,
        object_type=kwargs.get("object_type", "Lead"),
        actor_id="test-user",
    )
    return run_inbound_sync(
        session,
        connection,
        provider,
        mode=kwargs.pop("mode", "initial"),
        object_type=kwargs.pop("object_type", "Lead"),
        limit=kwargs.pop("limit", 100),
        page_size=kwargs.pop("page_size", 2),
        actor_id="test-user",
        icp=icp,
        routing=routing,
        **kwargs,
    )


def _lead_payload(email: str, external_id: str) -> dict[str, object]:
    return {
        "external_id": external_id,
        "source": "website",
        "full_name": "Existing Lead",
        "email": email,
        "company_name": "Existing Company",
        "product_or_service": "Lead routing automation",
        "message": "We need lead routing automation this month.",
    }


def test_initial_import_is_paginated_bounded_and_uses_canonical_pipeline(
    tmp_path, icp, routing
) -> None:
    with _session(tmp_path) as session:
        workspace_id = uuid4()
        connection = _connection(session, workspace_id)
        provider = FakeInboundProvider(
            connection.id,
            [
                _record("00Q-1", "one@example.test"),
                _record("00Q-2", "two@example.test"),
                _record("00Q-3", "three@example.test"),
            ],
        )
        run = _run(session, connection, provider, icp, routing, page_size=2)
        assert run.status == "completed"
        assert run.records_created == 3
        assert run.records_fetched == 3
        assert len(provider.calls) == 2
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 3
        assert session.scalar(select(func.count()).select_from(CrmProviderRecordLinkORM)) == 3
        lifecycle = list(
            session.scalars(
                select(ProviderLifecycleAuditORM).where(
                    ProviderLifecycleAuditORM.connection_id == connection.id,
                    ProviderLifecycleAuditORM.action.in_({"inbound_sync", "inbound_sync_batch"}),
                )
            )
        )
        assert lifecycle
        assert all(event.correlation_id == run.correlation_id for event in lifecycle)
        leads = list(session.scalars(select(LeadORM)))
        assert all(item.qualification is not None for item in leads)
        assert all(item.source == "web" for item in leads)
        assert all(item.source_detail == "crm-import:salesforce:Web" for item in leads)


def test_bounded_initial_import_persists_resume_cursor(tmp_path, icp, routing) -> None:
    with _session(tmp_path) as session:
        connection = _connection(session, uuid4())
        records = [_record(f"00Q-{index}", f"lead{index}@example.test") for index in range(5)]
        first_provider = FakeInboundProvider(connection.id, records)
        first = _run(session, connection, first_provider, icp, routing, limit=2, page_size=2)
        assert first.status == "completed_bounded"
        state = get_inbound_sync_state(session, connection.workspace_id, connection.id)
        assert state is not None
        assert state.resume_cursor == {"offset": 2}

        second_provider = FakeInboundProvider(connection.id, records)
        second = _run(session, connection, second_provider, icp, routing, limit=10, page_size=2)
        assert second.records_created == 3
        assert second_provider.calls[0] == {"offset": 2}
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 5


def test_interrupted_batch_resumes_after_last_committed_page(tmp_path, icp, routing) -> None:
    with _session(tmp_path) as session:
        connection = _connection(session, uuid4())
        records = [_record(f"00Q-{index}", f"lead{index}@example.test") for index in range(3)]
        failed_provider = FakeInboundProvider(connection.id, records, fail_call=2)
        failed = _run(session, connection, failed_provider, icp, routing, page_size=1)
        assert failed.status == "failed"
        assert failed.error_category == "rate_limited"
        state = get_inbound_sync_state(session, connection.workspace_id, connection.id)
        assert state is not None
        assert state.resume_cursor == {"offset": 1}

        resumed_provider = FakeInboundProvider(connection.id, records)
        resumed = _run(session, connection, resumed_provider, icp, routing, page_size=1)
        assert resumed.records_created == 2
        assert resumed_provider.calls[0] == {"offset": 1}
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 3


def test_single_record_failure_does_not_abort_the_remaining_batch(tmp_path, icp, routing) -> None:
    class OneBadRecordProvider(FakeInboundProvider):
        def normalize_record(self, record):
            if record.record_id == "bad-message":
                raise ValueError("ai_classification_failed:ai_timeout")
            return super().normalize_record(record)

    with _session(tmp_path) as session:
        connection = _connection(session, uuid4())
        provider = OneBadRecordProvider(
            connection.id,
            [
                _record("bad-message", "bad@example.test"),
                _record("good-message", "good@example.test"),
            ],
        )
        run = _run(session, connection, provider, icp, routing, page_size=2)
        assert run.status == "completed_with_errors"
        assert run.records_failed == 1
        assert run.records_created == 1
        assert "ai_timeout" in (run.last_error or "")
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 1


def test_gmail_rate_limited_message_does_not_discard_other_valid_leads(tmp_path, icp, routing):
    class GmailBatchProvider(FakeInboundProvider):
        provider_name = "gmail"

        def list_records(self, *, object_type, cursor, limit):
            return super().list_records(object_type="Lead", cursor=cursor, limit=limit)

        def normalize_record(self, record):
            if record.record_id == "rate-limited":
                raise ProviderOperationError(
                    "Gmail rate limit", provider="gmail", code="gmail_rate_limited",
                    category="temporarily_unavailable", retryable=True,
                )
            return super().normalize_record(record).model_copy(
                update={"provider": "gmail", "object_type": "message"}
            )

    with _session(tmp_path) as session:
        connection = _connection(session, uuid4(), provider_type="gmail")
        provider = GmailBatchProvider(connection.id, [
            _record("rate-limited", "blocked@example.test"),
            _record("valid", "valid@example.test"),
        ])
        run = _run(
            session, connection, provider, icp, routing,
            object_type="message", mode="review_recent", page_size=2,
        )
        assert run.status == "failed"
        assert run.error_code == "gmail_rate_limited"
        assert run.records_failed == 1
        assert run.records_created == 1
        assert run.cursor_current == run.cursor_start
        assert run.checkpoint is None
        assert session.scalar(select(LeadORM.email)) == "valid@example.test"


def test_gmail_thread_messages_are_added_once_to_same_lead_across_follow_up_syncs(
    tmp_path, icp, routing
):
    class GmailProvider(FakeInboundProvider):
        provider_name = "gmail"

        def __init__(self, connection_id, records):
            super().__init__(connection_id, records)
            self.thread_messages = [
                {
                    "message_id": "gmail-message-1", "thread_id": "thread-1",
                    "sender": "prospect@example.test", "sender_name": "Lead m1",
                    "recipients": "inbox@example.test", "subject": "Services",
                    "sent_at": "2026-10-19T09:00:00+00:00",
                    "direction": "inbound", "body": "We are interested in services.",
                },
                {
                    "message_id": "gmail-message-2", "thread_id": "thread-1",
                    "sender": "inbox@example.test", "sender_name": "Corral",
                    "recipients": "prospect@example.test", "subject": "Re: Services",
                    "sent_at": "2026-10-19T10:00:00+00:00",
                    "direction": "outbound", "body": "Could you share more details?",
                },
            ]

        def list_records(self, *, object_type, cursor, limit):
            assert object_type == "message"
            self.calls.append(cursor)
            offset = int((cursor or {}).get("offset", 0))
            records = self.records[offset:offset + limit]
            checkpoint = {"offset": offset + len(records)}
            return InboundCrmPage(
                records=records,
                next_cursor=checkpoint if checkpoint["offset"] < len(self.records) else None,
                checkpoint=checkpoint,
            )

        def normalize_record(self, record):
            if record.record_id == "message-3":
                raise ValueError("not_a_new_lead")
            canonical = super().normalize_record(record)
            return canonical.model_copy(update={
                "provider": "gmail",
                "object_type": "message",
                "provider_metadata": {
                    "gmail_thread_id": record.values["thread_id"],
                    "gmail_message_id": record.record_id,
                },
            })

        def fetch_thread(self, thread_id, *, lead_email):
            assert thread_id == "thread-1"
            return self.thread_messages

    with _session(tmp_path) as session:
        connection = _connection(session, uuid4(), provider_type="gmail")
        records = [
            _record("message-1", "prospect@example.test", message="Initial services inquiry."),
            _record(
                "message-2", "prospect@example.test",
                message="Follow-up with more service details.", version=2,
            ),
            _record(
                "message-3", "prospect@example.test",
                message="Additional context in a separate email.", version=3,
            ),
        ]
        records[0].values["thread_id"] = "thread-1"
        records[1].values["thread_id"] = "thread-1"
        records[2].values.update({
            "thread_id": "thread-2",
            "headers": {
                "from": "Lead message-3 <prospect@example.test>",
                "to": "Inbox <inbox@example.test>",
            },
            "thread_messages": [{
                "message_id": "gmail-message-3",
                "thread_id": "thread-2",
                "headers": {
                    "from": "Lead message-3 <prospect@example.test>",
                    "to": "Inbox <inbox@example.test>",
                    "subject": "Additional information",
                },
                "body": "Additional context in a separate email.",
                "internal_date": "1792404000000",
            }],
        })
        provider = GmailProvider(connection.id, records[:1])
        first = _run(
            session, connection, provider, icp, routing,
            object_type="message", mode="initial", page_size=1,
            send_confirmation_email=False,
        )
        provider.records.append(records[1])
        second = _run(
            session, connection, provider, icp, routing,
            object_type="message", mode="incremental", page_size=1,
            send_confirmation_email=False,
        )
        provider.records.append(records[2])
        third = _run(
            session, connection, provider, icp, routing,
            object_type="message", mode="incremental", page_size=1,
            send_confirmation_email=False,
        )
        lead = session.scalar(select(LeadORM).where(LeadORM.email == "prospect@example.test"))
        assert lead is not None
        events = list(
            session.scalars(
                select(PipelineEventORM)
                .where(
                    PipelineEventORM.lead_id == lead.id,
                    PipelineEventORM.stage == "gmail_message",
                )
                .order_by(PipelineEventORM.created_at)
            )
        )
        assert first.records_created == 1
        assert second.records_updated == 1
        assert third.records_skipped == 1
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 1
        assert [event.details["message_id"] for event in events] == [
            "gmail-message-1", "gmail-message-2", "gmail-message-3",
        ]
        assert "Follow-up with more service details." in lead.message


def test_gmail_updates_preserve_known_details_when_follow_up_omits_them():
    lead = LeadORM(
        id=uuid4(), workspace_id=uuid4(), source="email", source_detail="Gmail",
        full_name="Avery Lane", email="avery@example.test", phone="+1-555-0100",
        job_title="VP of Operations", company_name="Northstar",
        industry="SaaS", employee_count=180, annual_revenue=1250000,
        geography="United States", budget=50000,
        product_or_service="Lead routing automation", message="Initial conversation.",
        received_at=datetime(2026, 10, 1, tzinfo=UTC), raw_payload={},
        normalization_version="1", created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    canonical = CanonicalInboundLead(
        provider="gmail", object_type="message", provider_record_id="follow-up",
        normalized_payload={
            "full_name": "Name not provided", "email": "avery@example.test",
            "phone": "", "job_title": None, "company_name": "Unknown",
            "industry": None, "employee_count": None, "annual_revenue": None,
            "geography": None, "budget": None, "product_or_service": "Unknown",
            "message": "New follow-up details.",
        },
        source_fingerprint="a" * 64, provider_source="Gmail", provider_metadata={},
    )
    payload = _contact_update_payload(canonical, lead, canonical.normalized_payload)
    assert payload["full_name"] == "Avery Lane"
    assert payload["company_name"] == "Northstar"
    assert payload["product_or_service"] == "Lead routing automation"
    assert payload["phone"] == "+1-555-0100"
    assert payload["job_title"] == "VP of Operations"
    assert payload["message"] == "New follow-up details."


def test_gmail_history_is_scoped_to_exact_sender_or_to_email_not_cc():
    messages = [
        {
            "message_id": "cc-only",
            "headers": {
                "from": "Other Lead <other@example.test>",
                "to": "inbox@example.test",
                "cc": "prospect@example.test",
            },
            "internal_date": "1792404000000",
            "body": "Not addressed to this prospect.",
        },
        {
            "message_id": "to-match",
            "headers": {
                "from": "Other Lead <other@example.test>",
                "to": "Prospect <prospect@example.test>",
            },
            "internal_date": "1792404060000",
            "body": "This message is addressed to this prospect.",
        },
        {
            "message_id": "from-match",
            "headers": {
                "from": "Prospect <prospect@example.test>",
                "to": "inbox@example.test",
            },
            "internal_date": "1792404120000",
            "body": "This message is from this prospect.",
        },
    ]
    rows = gmail_history_messages(
        messages,
        lead_email="prospect@example.test",
        mailbox_email="inbox@example.test",
    )
    assert [row["message_id"] for row in rows] == ["to-match", "from-match"]
    assert rows[0]["direction"] == "inbound"
    assert rows[1]["direction"] == "inbound"


def test_gmail_skips_ambiguous_email_match_instead_of_creating_a_duplicate(
    tmp_path, icp, routing
):
    with _session(tmp_path) as session:
        workspace_id = uuid4()
        connection = _connection(session, workspace_id, provider_type="gmail")
        now = datetime.now(UTC)
        for name in ("Existing Lead A", "Existing Lead B"):
            session.add(LeadORM(
                id=uuid4(), workspace_id=workspace_id, source="website",
                full_name=name, email="shared@example.test", phone=None, job_title=None,
                company_name="Existing Company", product_or_service="AI services",
                message="Initial conversation.", received_at=now, raw_payload={},
                normalization_version="1", created_at=now, updated_at=now,
            ))
        session.commit()
        canonical = CanonicalInboundLead(
            provider="gmail", object_type="message", provider_record_id="thread-new:contact",
            normalized_payload={
                "email": "shared@example.test", "full_name": "Shared Contact",
                "phone": "", "company_name": "Unknown",
                "product_or_service": "Unknown", "message": "Follow-up email.",
                "source": "email", "source_detail": "Gmail",
            },
            source_fingerprint="a" * 64, provider_source="Gmail",
            provider_metadata={"gmail_thread_id": "thread-new"},
        )
        with pytest.raises(ValueError, match="gmail_contact_match_ambiguous"):
            _process_record(
                session, connection, object(), canonical, icp, routing, None, None,
                actor_id="test-user", correlation_id="test-run",
                send_confirmation_email=False,
            )
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 2


def test_active_inbound_lease_blocks_overlap_across_workers(tmp_path, icp, routing) -> None:
    with _session(tmp_path) as session:
        connection = _connection(session, uuid4())
        configure_inbound_sync(session, connection, enabled=True, actor_id="test-user")
        state = get_inbound_sync_state(session, connection.workspace_id, connection.id)
        assert state is not None
        state.status = "running"
        state.lease_owner = "worker-a"
        state.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        session.commit()

        provider = FakeInboundProvider(
            connection.id, [_record("00Q-overlap", "overlap@example.test")]
        )
        with pytest.raises(ValueError, match="already active"):
            run_inbound_sync(
                session,
                connection,
                provider,
                mode="manual",
                object_type="Lead",
                limit=10,
                page_size=1,
                actor_id="worker-b",
                icp=icp,
                routing=routing,
            )
        assert provider.calls == []


def test_expired_inbound_lease_abandons_lost_worker_and_resumes(tmp_path, icp, routing) -> None:
    with _session(tmp_path) as session:
        connection = _connection(session, uuid4())
        configure_inbound_sync(session, connection, enabled=True, actor_id="test-user")
        state = get_inbound_sync_state(session, connection.workspace_id, connection.id)
        assert state is not None
        now = datetime.now(UTC)
        state.status = "running"
        state.lease_owner = "dead-worker"
        state.lease_expires_at = now - timedelta(seconds=1)
        stale = CrmInboundSyncRunORM(
            id=uuid4(),
            workspace_id=connection.workspace_id,
            connection_id=connection.id,
            state_id=state.id,
            provider="salesforce",
            object_type="Lead",
            mode="manual",
            status="running",
            requested_by="dead-worker",
            correlation_id="stale-run",
            cursor_start=None,
            cursor_current=None,
            records_fetched=0,
            records_created=0,
            records_updated=0,
            records_unchanged=0,
            records_skipped=0,
            records_failed=0,
            started_at=now - timedelta(minutes=10),
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=10),
        )
        session.add(stale)
        session.commit()

        resumed = _run(
            session,
            connection,
            FakeInboundProvider(connection.id, [_record("00Q-recover", "recover@example.test")]),
            icp,
            routing,
        )
        session.refresh(stale)
        session.refresh(state)
        assert resumed.status == "completed"
        assert stale.status == "abandoned"
        assert stale.error_code == "worker_lost"
        assert state.lease_owner is None
        assert state.lease_expires_at is None


def test_idempotent_reimport_skips_unchanged_pipeline_work(tmp_path, icp, routing) -> None:
    with _session(tmp_path) as session:
        connection = _connection(session, uuid4())
        records = [_record("00Q-1", "one@example.test")]
        _run(session, connection, FakeInboundProvider(connection.id, records), icp, routing)
        lead = session.scalar(select(LeadORM))
        assert lead is not None
        routing_count = len(lead.routing_decisions)

        repeated = _run(
            session,
            connection,
            FakeInboundProvider(connection.id, records),
            icp,
            routing,
        )
        session.refresh(lead)
        assert repeated.records_unchanged == 1
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 1
        assert len(lead.routing_decisions) == routing_count


def test_existing_mapping_updates_same_local_lead_on_meaningful_change(
    tmp_path, icp, routing
) -> None:
    with _session(tmp_path) as session:
        connection = _connection(session, uuid4())
        first = [_record("00Q-1", "one@example.test", company="Original")]
        _run(session, connection, FakeInboundProvider(connection.id, first), icp, routing)
        lead = session.scalar(select(LeadORM))
        assert lead is not None
        lead_id = lead.id

        changed = [_record("00Q-1", "one@example.test", company="Updated", version=2)]
        run = _run(session, connection, FakeInboundProvider(connection.id, changed), icp, routing)
        session.refresh(lead)
        assert run.records_updated == 1
        assert lead.id == lead_id
        assert lead.company_name == "Updated"
        assert len(lead.routing_decisions) == 2


def test_first_import_uses_exact_email_but_ambiguous_email_creates_separate_lead(
    tmp_path, icp, routing
) -> None:
    with _session(tmp_path) as session:
        connection = _connection(session, uuid4())
        process_lead(
            session,
            _lead_payload("match@example.test", "local-1"),
            icp,
            routing,
            workspace_id=connection.workspace_id,
        )
        matched = _run(
            session,
            connection,
            FakeInboundProvider(connection.id, [_record("00Q-match", "match@example.test")]),
            icp,
            routing,
        )
        link = session.scalar(
            select(CrmProviderRecordLinkORM).where(
                CrmProviderRecordLinkORM.provider_record_id == "00Q-match"
            )
        )
        assert matched.records_updated == 1
        assert link is not None and link.matching_strategy == "exact_email"
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 1

        process_lead(
            session,
            _lead_payload("ambiguous@example.test", "local-2"),
            icp,
            routing,
            workspace_id=connection.workspace_id,
        )
        process_lead(
            session,
            _lead_payload("ambiguous@example.test", "local-3"),
            icp,
            routing,
            workspace_id=connection.workspace_id,
        )
        ambiguous = _run(
            session,
            connection,
            FakeInboundProvider(
                connection.id, [_record("00Q-ambiguous", "ambiguous@example.test")]
            ),
            icp,
            routing,
        )
        ambiguous_link = session.scalar(
            select(CrmProviderRecordLinkORM).where(
                CrmProviderRecordLinkORM.provider_record_id == "00Q-ambiguous"
            )
        )
        assert ambiguous.records_created == 1
        assert ambiguous_link is not None
        assert ambiguous_link.matching_strategy == "ambiguous_email_created_separate"
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 4


def test_own_writeback_and_provider_delete_do_not_reprocess_or_delete_local_lead(
    tmp_path, icp, routing
) -> None:
    with _session(tmp_path) as session:
        connection = _connection(session, uuid4())
        base = _record("00Q-1", "one@example.test")
        _run(session, connection, FakeInboundProvider(connection.id, [base]), icp, routing)
        lead = session.scalar(select(LeadORM))
        link = session.scalar(select(CrmProviderRecordLinkORM))
        assert lead is not None and link is not None
        link.synchronization_origin = "local_writeback"
        link.last_writeback_at = datetime(2026, 1, 1, tzinfo=UTC)
        session.commit()

        own_write = _record("00Q-1", "one@example.test", version=2)
        run = _run(
            session,
            connection,
            FakeInboundProvider(connection.id, [own_write]),
            icp,
            routing,
        )
        session.refresh(link)
        assert run.records_unchanged == 1
        assert link.last_change_kind == "own_writeback"

        deleted = _record("00Q-1", "one@example.test", version=3, deleted=True)
        deletion = _run(
            session,
            connection,
            FakeInboundProvider(connection.id, [deleted]),
            icp,
            routing,
        )
        session.refresh(link)
        assert deletion.records_skipped == 1
        assert link.status == "provider_deleted"
        assert session.get(LeadORM, lead.id) is not None


def test_hubspot_writeback_echo_matches_only_the_sent_fields() -> None:
    written_at = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    sent = {
        "full_name": "Taylor LocalOutboundTest",
        "email": "taylor@example.test",
        "phone": None,
        "company": "Phase7 Manual Outbound Corp",
        "title": "Senior Director of Revenue Operations",
    }
    link = SimpleNamespace(
        provider="hubspot",
        synchronization_origin="local_writeback",
        last_writeback_at=written_at,
        last_writeback_fingerprint="sent-payload-hash",
        provider_source="Unknown",
        provider_metadata={"owner_id": None, "description_hash": "stable-description"},
    )
    outbound = SimpleNamespace(status="synced", payload_hash="sent-payload-hash", payload=sent)
    canonical = CanonicalInboundLead(
        provider="hubspot",
        object_type="contacts",
        provider_record_id="123",
        normalized_payload={
            "full_name": sent["full_name"],
            "email": sent["email"],
            "phone": sent["phone"],
            "company_name": sent["company"],
            "job_title": sent["title"],
        },
        source_fingerprint="a" * 64,
        provider_modified_at=written_at + timedelta(seconds=2),
        provider_source="Unknown",
        provider_metadata={"owner_id": None, "description_hash": "stable-description"},
    )

    assert _hubspot_own_writeback_matches(link, canonical, outbound)
    assert not _hubspot_own_writeback_matches(
        link,
        canonical.model_copy(
            update={
                "normalized_payload": {
                    **canonical.normalized_payload,
                    "company_name": "Externally Changed Company",
                }
            }
        ),
        outbound,
    )
    assert not _hubspot_own_writeback_matches(
        link,
        canonical.model_copy(
            update={"provider_metadata": {"description_hash": "changed-description"}}
        ),
        outbound,
    )
    assert not _hubspot_own_writeback_matches(
        link,
        canonical.model_copy(update={"provider_modified_at": written_at + timedelta(minutes=6)}),
        outbound,
    )
    assert not _hubspot_own_writeback_matches(
        link,
        canonical,
        SimpleNamespace(status="synced", payload_hash="other-hash", payload=sent),
    )


def test_provider_record_identity_and_cursors_are_workspace_scoped(tmp_path, icp, routing) -> None:
    with _session(tmp_path) as session:
        first_connection = _connection(session, uuid4(), account="first")
        second_connection = _connection(session, uuid4(), account="second")
        record = _record("00Q-shared", "shared@example.test")
        _run(
            session,
            first_connection,
            FakeInboundProvider(first_connection.id, [record]),
            icp,
            routing,
        )
        _run(
            session,
            second_connection,
            FakeInboundProvider(second_connection.id, [record]),
            icp,
            routing,
        )
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 2
        assert session.scalar(select(func.count()).select_from(CrmProviderRecordLinkORM)) == 2
        states = list(session.scalars(select(models.CrmInboundSyncStateORM)))
        assert {item.workspace_id for item in states} == {
            first_connection.workspace_id,
            second_connection.workspace_id,
        }


def test_mapping_unique_constraint_prevents_duplicate_provider_link(tmp_path, icp, routing) -> None:
    with _session(tmp_path) as session:
        connection = _connection(session, uuid4())
        record = _record("00Q-1", "one@example.test")
        _run(session, connection, FakeInboundProvider(connection.id, [record]), icp, routing)
        existing = session.scalar(select(CrmProviderRecordLinkORM))
        assert existing is not None
        duplicate = CrmProviderRecordLinkORM(
            id=uuid4(),
            workspace_id=existing.workspace_id,
            connection_id=existing.connection_id,
            provider=existing.provider,
            object_type=existing.object_type,
            provider_record_id=existing.provider_record_id,
            lead_id=existing.lead_id,
            status="active",
            matching_strategy="duplicate",
            provider_metadata={},
            source_fingerprint=existing.source_fingerprint,
            synchronization_origin="provider",
            last_change_kind="source_changed",
            last_seen_at=datetime.now(UTC),
            last_imported_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.commit()


def test_salesforce_inbound_adapter_uses_configured_version_and_standard_fields(
    crm_config,
) -> None:
    body = {
        "records": [
            {
                "Id": "00Q-1",
                "FirstName": "Jordan",
                "LastName": "Test",
                "Company": "Phase7 Testing Corp",
                "Email": "jordan@example.test",
                "LeadSource": "Web",
                "Description": "Needs routing automation this month.",
                "SystemModstamp": "2026-01-01T00:00:00.000+0000",
                "CreatedDate": "2026-01-01T00:00:00.000+0000",
                "IsDeleted": False,
            }
        ],
        "done": True,
    }
    transport = FakeTransport([FakeResponse(200, body)])
    provider = SalesforceInboundCrmProvider(
        connection_id=uuid4(),
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
        access_token="access",
        instance_url="https://org.salesforce.com",
        login_base_url="https://login.salesforce.com",
        api_version="v67.0",
        config=crm_config.salesforce,
        transport=transport,
    )
    page = provider.list_records(object_type="Lead", cursor=None, limit=100)
    canonical = provider.normalize_record(page.records[0])
    request_url = transport.requests[0]["url"]
    assert "/services/data/v67.0/query?" in request_url
    assert "Qualification_Score__c" not in request_url
    assert canonical.normalized_payload["source"] == "web"
    assert canonical.normalized_payload["source_detail"] == "crm-import:salesforce:Web"


def test_hubspot_contact_import_uses_shared_canonical_qualification_pipeline(
    tmp_path, icp, routing
) -> None:
    modified = "2026-09-24T12:00:00Z"
    transport = FakeTransport(
        [
            FakeResponse(
                200,
                {
                    "results": [
                        {
                            "id": "hubspot-contact-44",
                            "updatedAt": modified,
                            "archived": False,
                            "properties": {
                                "email": "mira@example.test",
                                "firstname": "Mira",
                                "lastname": "Example",
                                "company": "Example Corp",
                                "jobtitle": "VP Operations",
                                "phone": "+1-555-0188",
                                "hs_analytics_source": "OFFLINE",
                                "description": "Need routing automation this month.",
                                "createdate": modified,
                                "hs_lastmodifieddate": modified,
                                "hubspot_owner_id": "owner-7",
                            },
                        }
                    ]
                },
            ),
            FakeResponse(200, {"results": []}),
        ]
    )
    with _session(tmp_path) as session:
        connection = _connection(session, uuid4(), provider_type="hubspot")
        provider = HubSpotInboundCrmProvider(
            connection_id=connection.id,
            access_token="mock-hubspot-token",
            transport=transport,
        )
        run = _run(
            session,
            connection,
            provider,
            icp,
            routing,
            object_type="contacts",
            page_size=10,
        )
        lead = session.scalar(select(LeadORM))
        link = session.scalar(select(CrmProviderRecordLinkORM))
        assert run.records_created == 1
        assert run.status == "completed"
        assert lead is not None
        assert lead.email == "mira@example.test"
        assert lead.source == "offline"
        assert lead.source_detail == "crm-import:hubspot:OFFLINE"
        assert link is not None
        assert link.provider_record_id == "hubspot-contact-44"
        assert link.object_type == "contacts"


def test_imported_salesforce_link_is_directly_updated_before_external_id_upserts(
    tmp_path, icp, routing, crm_config
) -> None:
    from lead_agent.infrastructure.crm.salesforce import SalesforceCrmProvider

    with _session(tmp_path) as session:
        connection = _connection(session, uuid4())
        _run(
            session,
            connection,
            FakeInboundProvider(connection.id, [_record("00Q-imported", "imported@example.test")]),
            icp,
            routing,
        )
        lead = session.scalar(select(LeadORM))
        assert lead is not None
        transport = FakeTransport([FakeResponse(204)])
        outbound = SalesforceCrmProvider(
            client_id="client",
            client_secret="secret",
            refresh_token="refresh",
            access_token="access",
            instance_url="https://org.salesforce.com",
            login_base_url="https://login.salesforce.com",
            api_version="v67.0",
            config=crm_config.salesforce,
            transport=transport,
        )
        outbound.connection_id = connection.id
        row = sync_lead_to_crm(session, lead, outbound)
        assert row.status == "synced"
        assert transport.requests[0]["url"].endswith("/sobjects/Lead/00Q-imported")
        assert transport.requests[0]["json"]["Lead_Agent_ID__c"] == str(lead.id)


class ResolverStub:
    def __init__(self, provider: FakeInboundProvider) -> None:
        self.provider = provider

    def resolve(self, session, workspace_id, connection_id, *, actor_id):
        del session, workspace_id, actor_id
        assert connection_id == self.provider.connection_id
        return self.provider


def test_inbound_api_requires_crm_sync_permission(tmp_path, icp, routing) -> None:
    session = _session(tmp_path)
    connection = _connection(session, uuid4())
    provider = FakeInboundProvider(connection.id, [_record("00Q-1", "one@example.test")])

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_workspace_inbound_crm_resolver] = lambda: ResolverStub(provider)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        workspace_id=connection.workspace_id,
        actor_id="viewer",
        role="viewer",
        permissions=frozenset({"integrations.view", "leads.view"}),
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/v1/integrations/connections/{connection.id}/inbound-sync",
                json={"mode": "manual", "limit": 10, "page_size": 2},
            )
        assert response.status_code == 403
        assert session.scalar(select(func.count()).select_from(CrmInboundSyncRunORM)) == 0
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_inbound_api_exposes_safe_run_and_state_without_provider_payloads(tmp_path) -> None:
    session = _session(tmp_path)
    connection = _connection(session, uuid4())
    provider = FakeInboundProvider(connection.id, [_record("00Q-1", "one@example.test")])

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_workspace_inbound_crm_resolver] = lambda: ResolverStub(provider)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        workspace_id=connection.workspace_id,
        actor_id="manager",
        role="sales_manager",
        permissions=frozenset(
            {"crm.sync", "integrations.manage", "integrations.view", "leads.view"}
        ),
    )
    try:
        with TestClient(app) as client:
            configured = client.put(
                f"/v1/integrations/connections/{connection.id}/inbound-sync/configuration",
                json={"enabled": True, "interval_seconds": 300},
            )
            started = client.post(
                f"/v1/integrations/connections/{connection.id}/inbound-sync",
                json={"mode": "manual", "limit": 10, "page_size": 2},
            )
            state = client.get(f"/v1/integrations/connections/{connection.id}/inbound-sync/state")
            runs = client.get(f"/v1/integrations/connections/{connection.id}/inbound-sync/runs")
        assert configured.status_code == 200
        assert started.status_code == 200
        assert started.json()["records_created"] == 1
        assert state.status_code == 200
        assert state.json()["status"] == "ready"
        assert runs.status_code == 200 and len(runs.json()) == 1
        serialized = json.dumps({"run": started.json(), "state": state.json(), "runs": runs.json()})
        assert "one@example.test" not in serialized
        assert "provider_record_id" not in serialized
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_gmail_manual_sync_is_accepted_and_status_is_pollable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lead_agent.worker import preview_sync

    session = _session(tmp_path)
    connection = _connection(session, uuid4(), provider_type="gmail")
    provider = FakeInboundProvider(connection.id, [])
    queued: list[dict[str, object]] = []
    monkeypatch.setattr(
        preview_sync,
        "enqueue_manual_sync",
        lambda state_id, **kwargs: queued.append({"state_id": state_id, **kwargs}),
    )

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_workspace_inbound_crm_resolver] = lambda: ResolverStub(provider)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        workspace_id=connection.workspace_id,
        actor_id="manager",
        role="sales_manager",
        permissions=frozenset({"crm.sync", "integrations.manage", "integrations.view"}),
    )
    try:
        with TestClient(app) as client:
            configured = client.put(
                f"/v1/integrations/connections/{connection.id}/inbound-sync/configuration",
                json={"enabled": True, "object_type": "message", "interval_seconds": 300},
            )
            response = client.post(
                f"/v1/integrations/connections/{connection.id}/inbound-sync",
                json={"mode": "review_recent", "object_type": "message", "limit": 20},
            )
            state = client.get(
                f"/v1/integrations/connections/{connection.id}/inbound-sync/state?object_type=message"
            )
            runs = client.get(
                f"/v1/integrations/connections/{connection.id}/inbound-sync/runs?limit=1"
            )
        assert configured.status_code == 200
        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        assert queued[0]["requested_mode"] == "review_recent"
        assert state.json()["status"] == "queued"
        assert runs.json() == []
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_preview_manual_sync_runs_without_the_celery_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from threading import Event

    from lead_agent.worker import preview_sync

    completed = Event()
    call: dict[str, object] = {}

    def run_locally(state_id: str, **kwargs: object) -> str:
        call.update({"state_id": state_id, **kwargs})
        completed.set()
        return "completed"

    monkeypatch.setattr(
        preview_sync.run_scheduled_crm_inbound_sync, "run", run_locally
    )
    preview_sync.enqueue_manual_sync(
        "state-123", requested_mode="review_recent", requested_limit=25
    )
    assert completed.wait(timeout=2)
    assert call == {
        "state_id": "state-123",
        "requested_mode": "review_recent",
        "requested_limit": 25,
    }
