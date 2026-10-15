from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from test_calling import calls as calls_fixture

from lead_agent.application.crm import build_crm_payload
from lead_agent.application.crm_inbound import _process_record
from lead_agent.application.governance import outreach_allowed
from lead_agent.infrastructure.communications import CommunicationsClient
from lead_agent.infrastructure.crm.gohighlevel import GoHighLevelCrmProvider
from lead_agent.infrastructure.db.operations import ConsentEventORM

calls = calls_fixture

CREDS = {"access_token": "private-token", "location_id": "location1"}
CONTACT = {
    "id": "contact1",
    "locationId": "location1",
    "firstName": "Alex",
    "lastName": "Smith",
    "email": "alex@acme.test",
    "companyName": "Acme",
    "phone": "+14155550111",
    "dateAdded": "2026-01-01T00:00:00Z",
    "dnd": True,
}


def test_contact_search_scopes_location_and_cursor_and_deduplicates_source():
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={"contacts": [{**CONTACT, "searchAfter": ["contact1", 1]}]})

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        provider = GoHighLevelCrmProvider(
            uuid4(), CREDS, client=CommunicationsClient("gohighlevel", CREDS, client=http)
        )
        page = provider.list_records(object_type="contacts", cursor=None, limit=1)
        assert page.next_cursor == {"search_after": ["contact1", 1]}
        provider.list_records(object_type="contacts", cursor=page.next_cursor, limit=1)
        import json

        assert json.loads(requests[0].content) == {
            "locationId": "location1",
            "pageLimit": 1,
            "page": 1,
        }
        assert "page" not in json.loads(requests[1].content)
        assert requests[0].headers["version"] == "2021-07-28"
        record = page.records[0]
        normalized = provider.normalize_record(record)
        assert normalized.provider_metadata["dnd"] is True
        assert normalized.normalized_payload["source"] == "gohighlevel"
        record.values["dateUpdated"] = datetime.now(UTC).isoformat()
        assert provider.normalize_record(record).source_fingerprint == normalized.source_fingerprint
        with pytest.raises(ValueError):
            provider._record({**CONTACT, "locationId": "other"})
        with pytest.raises(ValueError):
            provider.normalize_record(
                record.model_copy(update={"values": {**CONTACT, "email": ""}})
            )


def test_contact_writeback_updates_imported_id_without_overwriting_optouts(calls):
    _, _, _, lead, _, _, _ = calls
    from lead_agent.domain.crm import CrmEntityReferences

    seen = []

    def transport(request):
        seen.append(request)
        return httpx.Response(200, json={"contact": CONTACT})

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        provider = GoHighLevelCrmProvider(
            uuid4(), CREDS, client=CommunicationsClient("gohighlevel", CREDS, client=http)
        )
        result = provider.sync(
            build_crm_payload(lead, lead.routing_decisions[-1]),
            CrmEntityReferences(contact_id="contact1"),
        )
    assert [r.method for r in seen] == ["GET", "PUT"]
    import json

    payload = json.loads(seen[-1].content)
    assert not {"dnd", "dndSettings", "tags", "customFields"}.intersection(payload)
    assert result.entities.contact_id == "contact1"


def test_imported_optout_is_enforced_before_pipeline_and_is_not_duplicated(calls, icp, routing):
    session, _, workspace, lead, connection, _, _ = calls
    connection.provider_type = "gohighlevel"
    lead.job_title = "VP Revenue"
    lead.industry = "Software"
    original_inquiry = lead.message
    session.commit()
    provider = GoHighLevelCrmProvider(connection.id, CREDS)
    canonical = provider.normalize_record(provider._record(CONTACT))
    for _ in range(2):
        _process_record(
            session,
            connection,
            provider,
            canonical,
            icp,
            routing,
            None,
            None,
            actor_id="test",
            correlation_id=str(uuid4()),
            send_confirmation_email=False,
        )
    assert not outreach_allowed(session, workspace.workspace_id, lead.email, "call")[0]
    assert not outreach_allowed(session, workspace.workspace_id, lead.email, "email")[0]
    assert session.scalar(select(func.count()).select_from(ConsentEventORM)) == 1
    assert lead.job_title == "VP Revenue" and lead.industry == "Software"
    assert lead.message == original_inquiry
