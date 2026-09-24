from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
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
    configure_inbound_sync,
    get_inbound_sync_state,
    run_inbound_sync,
)
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
