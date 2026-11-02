from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from lead_agent.api.main import app
from lead_agent.application.crm import build_crm_payload, retry_crm_sync, sync_lead_to_crm
from lead_agent.application.services import process_lead
from lead_agent.domain.crm import (
    CanonicalCrmPayload,
    CrmEntityReferences,
    CrmProviderError,
    HubSpotCrmConfig,
)
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.infrastructure.crm.factory import get_crm_provider
from lead_agent.infrastructure.crm.hubspot import HubSpotCrmProvider
from lead_agent.infrastructure.crm.mock import MockCrmProvider
from lead_agent.infrastructure.crm.salesforce import (
    SalesforceConnectionValidator,
    SalesforceCrmProvider,
    SalesforceProviderRevoker,
)
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import CrmSyncORM, PipelineEventORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.repositories import (
    get_current_crm_sync,
    get_current_routing,
    list_crm_sync_history,
)


def lead_payload(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "external_id": "crm-test-001",
        "source": "website",
        "source_detail": "crm-test",
        "full_name": "Maya Thompson",
        "email": "maya@example.test",
        "phone": "+1-555-0100",
        "job_title": "VP of Operations",
        "company_name": "Northstar SaaS",
        "industry": "SaaS",
        "employee_count": 180,
        "geography": "United States",
        "budget": 30000,
        "product_or_service": "Lead routing automation",
        "message": (
            "We need an API integration this month and a proposal this week because "
            "manual lead qualification is causing missed opportunities."
        ),
    }
    value.update(overrides)
    return value


def session_for(tmp_path, *, threads: bool = False) -> Session:
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'crm.db'}",
        connect_args={"check_same_thread": False} if threads else {},
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def process_and_sync(session, payload, icp, routing, provider):
    _, _, lead = process_lead(session, payload, icp, routing)
    sync_lead_to_crm(session, lead, provider)
    return lead


def canonical_payload() -> CanonicalCrmPayload:
    from uuid import UUID

    return CanonicalCrmPayload(
        internal_lead_id=UUID("12345678-1234-5678-1234-567812345678"),
        external_source_id="source-1",
        full_name="Maya Thompson",
        first_name="Maya",
        last_name="Thompson",
        email="maya@example.test",
        company="Northstar SaaS",
        company_key="northstar-key",
        title="VP Operations",
        phone="+1-555-0100",
        source_channel="website",
        source_detail="contact-form",
        requested_service="Lead routing automation",
        summary="Maya needs lead routing automation.",
        qualification_score=91,
        qualification_band="hot",
        icp_score=40,
        intent_urgency_score=35,
        engagement_score=16,
        rationale=["Industry matched ICP", "Explicit timeline detected"],
        recommended_action="Schedule a discovery call.",
        action_plan={"immediate_action": "Schedule a discovery call."},
        assigned_queue="senior_rep",
        assigned_rep_id="rep-alex",
        assigned_rep_name="Alex Morgan",
        urgency_timeline="this month",
        lifecycle_stage="hot",
    )


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


def test_canonical_crm_payload_contains_phase_1_to_4_decision_data(icp, routing, tmp_path) -> None:
    with session_for(tmp_path) as session:
        _, _, lead = process_lead(session, lead_payload(), icp, routing)
        decision = get_current_routing(session, lead.id)
        assert decision is not None
        payload = build_crm_payload(lead, decision)
        assert payload.internal_lead_id == lead.id
        assert payload.source_channel == "website"
        assert payload.qualification_score == lead.qualification.total_score
        assert payload.icp_score is None
        assert payload.icp_source == "not_configured"
        assert payload.intent_urgency_score <= 35
        assert payload.engagement_score <= 25
        assert payload.rationale
        assert payload.recommended_action == decision.action_plan["immediate_action"]
        assert payload.assigned_rep_id == decision.assigned_rep_id


def test_mock_initial_sync_and_idempotent_resync(icp, routing, tmp_path) -> None:
    provider = MockCrmProvider()
    with session_for(tmp_path) as session:
        lead = process_and_sync(session, lead_payload(), icp, routing, provider)
        first = get_current_crm_sync(session, lead.id)
        assert first is not None
        assert first.status == "synced"
        assert first.operation == "create"
        assert provider.calls == 1
        second = sync_lead_to_crm(session, lead, provider)
        assert second.id == first.id
        assert provider.calls == 1
        assert session.scalar(select(func.count()).select_from(CrmSyncORM)) == 1


def test_changed_lead_creates_history_and_updates_existing_entities(icp, routing, tmp_path) -> None:
    provider = MockCrmProvider()
    with session_for(tmp_path) as session:
        lead = process_and_sync(session, lead_payload(), icp, routing, provider)
        first = get_current_crm_sync(session, lead.id)
        assert first is not None
        first_contact = first.provider_entity_ids["contact_id"]
        lead.job_title = "Chief Operating Officer"
        session.commit()
        second = sync_lead_to_crm(session, lead, provider)
        assert second.id != first.id
        assert second.operation == "update"
        assert second.provider_entity_ids["contact_id"] == first_contact
        assert len(list_crm_sync_history(session, lead.id)) == 2
        assert first.is_current is False


def test_transient_failure_retries_then_succeeds(icp, routing, tmp_path) -> None:
    transient = CrmProviderError(
        "temporary outage", code="temporary", category="transient", retryable=True
    )
    provider = MockCrmProvider(failures=[transient, transient])
    with session_for(tmp_path) as session:
        lead = process_and_sync(session, lead_payload(), icp, routing, provider)
        row = get_current_crm_sync(session, lead.id)
        assert row is not None
        assert row.status == "synced"
        assert row.attempts == 3
        assert [attempt["status"] for attempt in row.attempt_history] == [
            "retryable",
            "retryable",
            "synced",
        ]
        assert row.attempt_history[0]["error"] == "temporary outage"
        assert provider.calls == 3


@pytest.mark.parametrize(
    ("category", "code"),
    [("authentication", "bad_auth"), ("validation", "bad_payload")],
)
def test_permanent_failure_does_not_rollback_lead_or_route(
    category, code, icp, routing, tmp_path
) -> None:
    provider = MockCrmProvider(
        failures=[
            CrmProviderError("permanent failure", code=code, category=category, retryable=False)
        ]
    )
    with session_for(tmp_path) as session:
        lead = process_and_sync(session, lead_payload(), icp, routing, provider)
        row = get_current_crm_sync(session, lead.id)
        assert lead.id is not None
        assert get_current_routing(session, lead.id) is not None
        assert row is not None
        assert row.status == "failed"
        assert row.retryable is False
        assert row.attempts == 1


def test_manual_update_recovers_same_payload_after_connection_configuration_failure(
    icp, routing, tmp_path
) -> None:
    unavailable = CrmProviderError(
        "workspace CRM connection is unavailable",
        code="workspace_connection_unavailable",
        category="configuration",
        retryable=False,
    )
    with session_for(tmp_path) as session:
        lead = process_lead(session, lead_payload(), icp, routing)[2]
        failed = sync_lead_to_crm(
            session,
            lead,
            MockCrmProvider(failures=[unavailable]),
            request_origin="manual_crm_update",
        )
        assert failed.status == "failed"
        assert failed.retryable is False

        # Background work must keep respecting the permanent failure marker.
        unchanged = sync_lead_to_crm(session, lead, MockCrmProvider(), request_origin="scheduled")
        assert unchanged.id == failed.id
        assert unchanged.attempts == 1

        recovered = sync_lead_to_crm(
            session, lead, MockCrmProvider(), request_origin="manual_crm_update"
        )
        assert recovered.id == failed.id
        assert recovered.status == "synced"
        assert recovered.attempts == 2


def test_retryable_failure_manual_retry_reuses_sync_record(icp, routing, tmp_path) -> None:
    failures = [
        CrmProviderError("rate limited", code="rate_limit", category="rate_limit", retryable=True)
        for _ in range(3)
    ]
    provider = MockCrmProvider(failures=failures)
    with session_for(tmp_path) as session:
        lead = process_and_sync(session, lead_payload(), icp, routing, provider)
        failed = get_current_crm_sync(session, lead.id)
        assert failed is not None
        assert failed.status == "retryable"
        recovered = retry_crm_sync(session, lead, provider)
        assert recovered.id == failed.id
        assert recovered.status == "synced"
        assert recovered.attempts == 4
        assert len(recovered.attempt_history) == 4
        assert [attempt["status"] for attempt in recovered.attempt_history] == [
            "retryable",
            "retryable",
            "retryable",
            "synced",
        ]
        assert session.scalar(select(func.count()).select_from(CrmSyncORM)) == 1


def test_qualification_lifecycle_does_not_append_automatic_crm_write(
    icp, routing, tmp_path
) -> None:
    with session_for(tmp_path) as session:
        _, _, lead = process_lead(session, lead_payload(), icp, routing)
        stages = list(
            session.scalars(
                select(PipelineEventORM.stage)
                .where(PipelineEventORM.lead_id == lead.id)
                .order_by(PipelineEventORM.created_at)
            )
        )
        assert stages == [
            "received",
            "normalized",
            "analyzed",
            "qualified",
            "routed",
            "notified",
        ]


def test_salesforce_mapping_owner_and_external_id_upsert(crm_config) -> None:
    transport = FakeTransport(
        [
            FakeResponse(
                200, {"access_token": "token", "instance_url": "https://org.salesforce.com"}
            ),
            FakeResponse(201, {"id": "00Q-contact", "created": True}),
        ]
    )
    config = crm_config.salesforce.model_copy(update={"owner_mapping": {"rep-alex": "005-owner"}})
    provider = SalesforceCrmProvider(
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
        login_base_url="https://login.salesforce.test",
        api_version="v68.0",
        config=config,
        transport=transport,
    )
    result = provider.sync(canonical_payload())
    request = transport.requests[1]
    assert result.entities.contact_id == "00Q-contact"
    assert "/Lead/Lead_Agent_ID__c/12345678-1234-5678-1234-567812345678" in request["url"]
    assert request["json"]["OwnerId"] == "005-owner"
    assert request["json"]["Qualification_Score__c"] == 91
    assert request["json"]["Qualification_Rationale__c"].startswith("Industry matched")
    assert request["json"]["Sales_Action_Plan__c"] == (
        '{"immediate_action":"Schedule a discovery call."}'
    )
    assert request["json"]["Assigned_Rep_Name__c"] == "Alex Morgan"


def test_salesforce_existing_record_uses_same_external_identity(crm_config) -> None:
    transport = FakeTransport(
        [
            FakeResponse(
                200, {"access_token": "token", "instance_url": "https://org.salesforce.com"}
            ),
            FakeResponse(201, {"id": "00Q-contact"}),
            FakeResponse(
                200, {"access_token": "token", "instance_url": "https://org.salesforce.com"}
            ),
            FakeResponse(200, {"id": "00Q-contact", "created": False}),
        ]
    )
    provider = SalesforceCrmProvider(
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
        login_base_url="https://login.test",
        api_version="v68.0",
        config=crm_config.salesforce,
        transport=transport,
    )
    first = provider.sync(canonical_payload())
    second = provider.sync(canonical_payload(), first.entities)
    assert transport.requests[1]["url"] == transport.requests[3]["url"]
    assert second.operation == "update"
    assert second.entities.contact_id == "00Q-contact"


@pytest.mark.parametrize(
    ("status", "category", "retryable"),
    [(401, "authentication", False), (429, "rate_limit", True), (400, "validation", False)],
)
def test_salesforce_error_classification(status, category, retryable, crm_config) -> None:
    transport = FakeTransport(
        [
            FakeResponse(
                200, {"access_token": "token", "instance_url": "https://org.salesforce.com"}
            ),
            FakeResponse(status),
        ]
    )
    provider = SalesforceCrmProvider(
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
        login_base_url="https://login.salesforce.test",
        api_version="v68.0",
        config=crm_config.salesforce,
        transport=transport,
    )
    with pytest.raises(CrmProviderError) as caught:
        provider.sync(canonical_payload())
    assert caught.value.category == category
    assert caught.value.retryable is retryable


def test_salesforce_missing_credentials_fails_without_http(crm_config) -> None:
    transport = FakeTransport([])
    provider = SalesforceCrmProvider(
        client_id="",
        client_secret="",
        refresh_token="",
        login_base_url="https://login.salesforce.com",
        api_version="v68.0",
        config=crm_config.salesforce,
        transport=transport,
    )
    with pytest.raises(CrmProviderError) as caught:
        provider.sync(canonical_payload())
    assert caught.value.category == "configuration"
    assert transport.requests == []


def test_salesforce_workspace_access_token_skips_duplicate_refresh(crm_config) -> None:
    transport = FakeTransport([FakeResponse(201, {"id": "00Q-workspace"})])
    provider = SalesforceCrmProvider(
        client_id="operator-client",
        client_secret="operator-secret",
        refresh_token="workspace-refresh",
        access_token="workspace-access",
        instance_url="https://org.salesforce.com",
        login_base_url="https://login.salesforce.com",
        api_version="v68.0",
        config=crm_config.salesforce,
        transport=transport,
    )
    result = provider.sync(canonical_payload())
    assert result.entities.contact_id == "00Q-workspace"
    assert len(transport.requests) == 1
    assert transport.requests[0]["method"] == "PATCH"
    assert transport.requests[0]["headers"]["Authorization"] == "Bearer workspace-access"


def test_salesforce_validator_probes_limits_with_scoped_token() -> None:
    transport = FakeTransport([FakeResponse(200, {"DailyApiRequests": {"Max": 15000}})])
    validator = SalesforceConnectionValidator(api_version="v68.0", transport=transport)
    result = validator.validate(
        "salesforce",
        {"access_token": "workspace-access", "instance_url": "https://org.salesforce.com"},
        ["api", "refresh_token"],
    )
    assert result.valid is True
    assert result.verified is True
    assert result.status == "connected"
    assert transport.requests[0]["method"] == "GET"
    assert transport.requests[0]["url"].endswith("/services/data/v68.0/limits")
    assert transport.requests[0]["headers"]["Authorization"] == "Bearer workspace-access"
    assert result.metadata["instance_host"] == "org.salesforce.com"


def test_salesforce_validator_translates_auth_and_malformed_failures() -> None:
    auth_validator = SalesforceConnectionValidator(
        api_version="v68.0", transport=FakeTransport([FakeResponse(401)])
    )
    with pytest.raises(ProviderOperationError) as auth_error:
        auth_validator.validate(
            "salesforce",
            {"access_token": "workspace-access", "instance_url": "https://org.salesforce.com"},
            ["api"],
        )
    assert auth_error.value.category == "invalid_credentials"
    assert "workspace-access" not in str(auth_error.value)

    malformed_validator = SalesforceConnectionValidator(
        api_version="v68.0", transport=FakeTransport([FakeResponse(200, ["not-a-map"])])
    )
    with pytest.raises(ProviderOperationError) as malformed_error:
        malformed_validator.validate(
            "salesforce",
            {"access_token": "workspace-access", "instance_url": "https://org.salesforce.com"},
            ["api"],
        )
    assert malformed_error.value.category == "malformed_response"


def test_salesforce_revoker_uses_oauth_revoke_endpoint_without_exposing_token() -> None:
    transport = FakeTransport([FakeResponse(200)])
    revoker = SalesforceProviderRevoker(
        login_base_url="https://login.salesforce.com", transport=transport
    )
    result = revoker.revoke(
        "salesforce", {"access_token": "workspace-access", "refresh_token": "workspace-refresh"}
    )
    assert result.status == "confirmed"
    assert transport.requests[0]["url"].endswith("/services/oauth2/revoke")
    assert transport.requests[0]["data"] == {"token": "workspace-refresh"}

    failed = SalesforceProviderRevoker(
        login_base_url="https://login.salesforce.com", transport=FakeTransport([FakeResponse(503)])
    ).revoke("salesforce", {"access_token": "workspace-access"})
    assert failed.status == "failed"
    assert failed.error_code == "salesforce_revocation_rejected"


def test_hubspot_contact_company_deal_mapping_and_associations(crm_config) -> None:
    transport = FakeTransport(
        [
            FakeResponse(
                200,
                {
                    "results": [
                        {"name": "qualification_score", "type": "number"},
                        {"name": "lead_agent_id", "type": "string"},
                    ]
                },
            ),
            FakeResponse(200, {"results": [{"id": "contact-1"}]}),
            FakeResponse(200, {"results": [{"id": "company-1"}]}),
            FakeResponse(204),
            FakeResponse(200, {"results": [{"id": "deal-1"}]}),
            FakeResponse(204),
            FakeResponse(204),
        ]
    )
    config = crm_config.hubspot.model_copy(
        update={
            "owner_mapping": {"rep-alex": "hubspot-owner"},
            "sync_company": True,
            "sync_deal": True,
            "contact_fields": {
                "qualification_score": "qualification_score",
                "internal_lead_id": "lead_agent_id",
            },
        }
    )
    provider = HubSpotCrmProvider(
        access_token="private-app-token",
        api_base_url="https://api.hubapi.test",
        config=config,
        transport=transport,
    )
    result = provider.sync(canonical_payload())
    contact_body = transport.requests[1]["json"]["inputs"][0]
    deal_body = transport.requests[4]["json"]["inputs"][0]
    assert result.entities == CrmEntityReferences(
        contact_id="contact-1", company_id="company-1", opportunity_id="deal-1"
    )
    assert contact_body["idProperty"] == "email"
    assert contact_body["properties"]["hubspot_owner_id"] == "hubspot-owner"
    assert contact_body["properties"]["qualification_score"] == "91"
    assert contact_body["properties"]["lead_agent_id"] == str(canonical_payload().internal_lead_id)
    assert deal_body["idProperty"] == "lead_agent_lead_id"
    assert deal_body["properties"]["dealstage"] == "appointmentscheduled"
    assert sum("associations/default" in item["url"] for item in transport.requests) == 3


def test_hubspot_duplicate_prevention_uses_stable_upsert_identities(crm_config) -> None:
    config: HubSpotCrmConfig = crm_config.hubspot.model_copy(
        update={"sync_company": False, "sync_deal": False, "contact_fields": {}}
    )
    transport = FakeTransport(
        [
            FakeResponse(200, {"results": [{"id": "contact-1"}]}),
            FakeResponse(200, {"results": [{"id": "contact-1"}]}),
        ]
    )
    provider = HubSpotCrmProvider(
        access_token="private-app-token",
        api_base_url="https://api.hubapi.test",
        config=config,
        transport=transport,
    )
    first = provider.sync(canonical_payload())
    second = provider.sync(canonical_payload(), first.entities)
    assert transport.requests[0]["method"] == "POST"
    assert transport.requests[0]["json"]["inputs"][0]["id"] == "maya@example.test"
    assert transport.requests[1]["method"] == "PATCH"
    assert transport.requests[1]["url"].endswith("/contacts/contact-1")
    assert len(transport.requests) == 2
    assert second.operation == "update"
    assert second.entities.contact_id == "contact-1"


def test_hubspot_creates_only_missing_contact_properties(crm_config) -> None:
    config: HubSpotCrmConfig = crm_config.hubspot.model_copy(
        update={
            "sync_company": False,
            "sync_deal": False,
            "contact_fields": {
                "summary": "lead_summary",
                "qualification_score": "qualification_score",
            },
        }
    )
    transport = FakeTransport(
        [
            FakeResponse(
                200,
                {"results": [{"name": "lead_summary", "type": "string"}]},
            ),
            FakeResponse(201, {"name": "qualification_score"}),
            FakeResponse(200, {"results": [{"id": "contact-1"}]}),
        ]
    )
    provider = HubSpotCrmProvider(
        access_token="private-app-token",
        api_base_url="https://api.hubapi.test",
        config=config,
        transport=transport,
    )

    provider.sync(canonical_payload())

    assert [request["method"] for request in transport.requests] == [
        "GET",
        "POST",
        "POST",
    ]
    assert transport.requests[0]["url"].endswith(
        "/crm/properties/2026-09/contacts"
    )
    created = transport.requests[1]
    assert created["url"].endswith("/crm/properties/2026-09/contacts")
    assert created["json"] == {
        "groupName": "contactinformation",
        "name": "qualification_score",
        "label": "Qualification Score",
        "type": "number",
        "fieldType": "number",
        "description": "Managed by Corral for lead qualification data.",
    }
    assert transport.requests[2]["url"].endswith(
        "/crm/v3/objects/contacts/batch/upsert"
    )


def test_hubspot_sync_continues_when_schema_write_is_not_granted(crm_config) -> None:
    config: HubSpotCrmConfig = crm_config.hubspot.model_copy(
        update={
            "sync_company": False,
            "sync_deal": False,
            "contact_fields": {"qualification_score": "qualification_score"},
        }
    )
    transport = FakeTransport(
        [
            FakeResponse(200, {"results": []}),
            FakeResponse(403, {"message": "insufficient scopes"}),
            FakeResponse(200, {"results": [{"id": "contact-1"}]}),
        ]
    )
    provider = HubSpotCrmProvider(
        access_token="private-app-token",
        api_base_url="https://api.hubapi.test",
        config=config,
        transport=transport,
    )

    result = provider.sync(canonical_payload())

    assert result.entities.contact_id == "contact-1"
    contact_body = transport.requests[2]["json"]["inputs"][0]["properties"]
    assert "qualification_score" not in contact_body


def test_hubspot_does_not_overwrite_contact_property_with_wrong_type(crm_config) -> None:
    config: HubSpotCrmConfig = crm_config.hubspot.model_copy(
        update={
            "sync_company": False,
            "sync_deal": False,
            "contact_fields": {"qualification_score": "qualification_score"},
        }
    )
    transport = FakeTransport(
        [
            FakeResponse(
                200,
                {"results": [{"name": "qualification_score", "type": "string"}]},
            )
        ]
    )
    provider = HubSpotCrmProvider(
        access_token="private-app-token",
        api_base_url="https://api.hubapi.test",
        config=config,
        transport=transport,
    )

    with pytest.raises(CrmProviderError) as caught:
        provider.sync(canonical_payload())

    assert caught.value.code == "hubspot_property_type_mismatch"
    assert "qualification_score" in str(caught.value)
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    ("status", "category", "retryable"),
    [(401, "authentication", False), (429, "rate_limit", True), (400, "validation", False)],
)
def test_hubspot_error_classification(status, category, retryable, crm_config) -> None:
    transport = FakeTransport([FakeResponse(status)])
    provider = HubSpotCrmProvider(
        access_token="private-app-token",
        api_base_url="https://api.hubapi.test",
        config=crm_config.hubspot.model_copy(
            update={"sync_company": False, "sync_deal": False, "contact_fields": {}}
        ),
        transport=transport,
    )
    with pytest.raises(CrmProviderError) as caught:
        provider.sync(canonical_payload())
    assert caught.value.category == category
    assert caught.value.retryable is retryable


def test_hubspot_missing_credentials_fails_without_http(crm_config) -> None:
    transport = FakeTransport([])
    provider = HubSpotCrmProvider(
        access_token="",
        api_base_url="https://api.hubapi.com",
        config=crm_config.hubspot,
        transport=transport,
    )
    with pytest.raises(CrmProviderError) as caught:
        provider.sync(canonical_payload())
    assert caught.value.category == "configuration"
    assert transport.requests == []


def test_cross_provider_contract_returns_canonical_entity_references(crm_config) -> None:
    salesforce_transport = FakeTransport(
        [
            FakeResponse(
                200, {"access_token": "token", "instance_url": "https://org.salesforce.com"}
            ),
            FakeResponse(201, {"id": "sf-lead"}),
        ]
    )
    salesforce = SalesforceCrmProvider(
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
        login_base_url="https://login.test",
        api_version="v68.0",
        config=crm_config.salesforce,
        transport=salesforce_transport,
    )
    hubspot_transport = FakeTransport([FakeResponse(200, {"results": [{"id": "hs-contact"}]})])
    hubspot = HubSpotCrmProvider(
        access_token="token",
        api_base_url="https://hubspot.test",
        config=crm_config.hubspot.model_copy(
            update={"sync_company": False, "sync_deal": False, "contact_fields": {}}
        ),
        transport=hubspot_transport,
    )
    for provider in (MockCrmProvider(), salesforce, hubspot):
        result = provider.sync(canonical_payload())
        assert result.provider == provider.provider_name
        assert result.entities.contact_id
        assert result.operation in {"create", "update", "upsert"}


def test_crm_api_inspection_history_and_manual_retry(icp, routing, tmp_path) -> None:
    failures = [
        CrmProviderError("temporary outage", code="temporary", category="transient", retryable=True)
        for _ in range(3)
    ]
    provider = MockCrmProvider(failures=failures)
    session = session_for(tmp_path, threads=True)
    try:
        lead = process_and_sync(session, lead_payload(), icp, routing, provider)

        def override_db():
            yield session

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_crm_provider] = lambda: provider
        with TestClient(app) as client:
            state = client.get(f"/v1/leads/{lead.id}/crm-sync")
            history = client.get(f"/v1/leads/{lead.id}/crm-sync/history")
            retried = client.post(f"/v1/leads/{lead.id}/crm-sync/retry")
        assert state.status_code == 200
        assert state.json()["status"] == "retryable"
        assert history.status_code == 200
        assert len(history.json()) == 1
        assert retried.status_code == 200
        assert retried.json()["status"] == "synced"
        assert retried.json()["provider_entity_ids"]["contact_id"]
        assert "payload" not in retried.json()
    finally:
        app.dependency_overrides.clear()
        session.close()
