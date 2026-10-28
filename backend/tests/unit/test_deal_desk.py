from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.routes import deals, health
from lead_agent.application.commercial import pipeline_value_usd, quote_total
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import LeadORM
from lead_agent.infrastructure.db.deals import DealORM
from lead_agent.infrastructure.db.schema_check import require_current_schema
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.repositories import ensure_workspace


@pytest.fixture
def desk():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        first, second = uuid4(), uuid4()
        ensure_workspace(session, first)
        ensure_workspace(session, second)
        now = datetime.now(UTC)
        lead = LeadORM(
            id=uuid4(), workspace_id=first, lead_number=1, source="manual",
            full_name="Test Prospect", email="prospect@example.test",
            company_name="Test Company", product_or_service="Service",
            message="Review pricing", received_at=now, raw_payload={},
            normalization_version="1", created_at=now, updated_at=now,
        )
        session.add(lead)
        session.commit()
        scope = [WorkspaceContext(first, permissions=frozenset({
            "leads.view", "leads.edit", "workspace.manage",
        }))]
        app = FastAPI()
        app.include_router(deals.router)
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_workspace_context] = lambda: scope[0]
        with TestClient(app) as client:
            yield client, session, scope, lead, second
    engine.dispose()


def payload(lead_id, **changes):
    return {"version": 0, "deal": {
        "leadId": str(lead_id), "stage": "quoted", "updatedAt": datetime.now(UTC).isoformat(),
        "valueOverride": 123.45, **changes,
    }}


def test_deals_persist_and_optimistic_conflicts_preserve_saved_data(desk):
    client, session, _, lead, _ = desk
    url = f"/v1/leads/{lead.id}/deal"
    saved = client.put(url, json=payload(lead.id))
    assert saved.status_code == 200
    assert saved.json()["version"] == 1
    assert client.put(url, json=payload(lead.id, valueOverride=9)).status_code == 409
    session.expire_all()
    listing = client.get("/v1/deals").json()
    assert listing["deals"][str(lead.id)]["deal"]["valueOverride"] == 123.45
    updated = payload(lead.id, valueOverride=140.29)
    updated["version"] = 1
    assert client.put(url, json=updated).json()["version"] == 2


def test_deals_workspace_isolation_and_role_permissions(desk):
    client, _, scope, lead, other = desk
    url = f"/v1/leads/{lead.id}/deal"
    assert client.put(url, json=payload(lead.id)).status_code == 200
    scope[0] = WorkspaceContext(other, permissions=frozenset({"leads.view", "leads.edit"}))
    assert client.get("/v1/deals").json()["deals"] == {}
    assert client.put(url, json=payload(lead.id)).status_code == 404
    scope[0] = WorkspaceContext(lead.workspace_id, permissions=frozenset({"leads.view"}))
    assert client.get("/v1/deals").status_code == 200
    assert client.put(url, json=payload(lead.id)).status_code == 403
    assert client.put("/v1/deals/settings", json={"version": 0}).status_code == 403
    scope[0] = WorkspaceContext(lead.workspace_id)
    assert client.get("/v1/deals").status_code == 403


def test_deal_compare_and_swap_catches_an_already_loaded_stale_row(desk):
    client, session, scope, lead, _ = desk
    url = f"/v1/leads/{lead.id}/deal"
    assert client.put(url, json=payload(lead.id)).status_code == 200
    with Session(session.bind) as stale:
        cached = stale.get(DealORM, lead.id)
        assert cached.version == 1
        newer = payload(lead.id, valueOverride=234.56)
        newer["version"] = 1
        assert client.put(url, json=newer).status_code == 200
        assert cached.version == 1
        with pytest.raises(HTTPException) as caught:
            deals.save_deal(lead.id, deals.DealUpdate.model_validate(newer), stale, scope[0])
        assert caught.value.status_code == 409
    assert client.get("/v1/deals").json()["deals"][str(lead.id)]["deal"]["valueOverride"] == 234.56


@pytest.mark.parametrize("changes,status", [
    ({"stage": "won"}, 409),
    ({"stage": "lost"}, 409),
    ({"leadId": str(uuid4())}, 422),
    ({"valueOverride": -1}, 422),
    ({"valueOverride": 1.001}, 422),
    ({"generatedBy": {"id": str(uuid4()), "name": "Foreign user"}}, 422),
])
def test_deal_validation_cannot_bypass_outcomes_or_member_credit(desk, changes, status):
    client, _, _, lead, _ = desk
    assert client.put(f"/v1/leads/{lead.id}/deal", json=payload(lead.id, **changes)).status_code == status


def test_commercial_settings_persist_with_conflicts_and_workspace_isolation(desk):
    client, _, scope, _, other = desk
    settings = {"version": 0, "monthlyGoal": 23456.78, "catalog": [], "quoteCounter": 7}
    assert client.put("/v1/deals/settings", json=settings).json()["version"] == 1
    assert client.put("/v1/deals/settings", json=settings).status_code == 409
    assert client.get("/v1/deals").json()["settings"]["monthlyGoal"] == 23456.78
    scope[0] = WorkspaceContext(other, permissions=frozenset({"leads.view"}))
    assert client.get("/v1/deals").json()["settings"] is None


def test_quote_rounding_uses_cents_for_discount_tax_and_recurrence():
    quote = {
        "termMonths": 3, "discountPct": 10, "taxPct": 7.5,
        "lines": [
            {"unitPrice": 10.05, "quantity": 3, "discountPct": 10, "billing": "one_time"},
            {"unitPrice": 0.15, "quantity": 1, "discountPct": 10, "billing": "monthly"},
        ],
    }
    assert quote_total(quote) == Decimal("26.66")
    lead = SimpleNamespace(outcome=None, budget=1000000)
    assert pipeline_value_usd(lead, None) == 0
    assert pipeline_value_usd(lead, {"quotes": [{
        **quote, "currency": "USD", "status": "draft",
    }]}) == 26.66
    assert pipeline_value_usd(lead, {"quotes": [{
        **quote, "currency": "EUR", "status": "draft",
    }]}) == 0
    lead.outcome = SimpleNamespace(pipeline_stage="closed_won")
    assert pipeline_value_usd(lead, {"valueOverride": 123.45}) == 0


@pytest.mark.parametrize("table", [
    "lead_deals", "workspace_commercial_settings", "workspace_agent_conversations",
])
def test_readiness_detects_missing_commercial_and_conversation_schema(desk, table):
    _, session, _, _, _ = desk
    require_current_schema(session.connection())
    session.execute(text(f"DROP TABLE {table}"))
    with pytest.raises(RuntimeError, match="schema"):
        require_current_schema(session.connection())
    with pytest.raises(HTTPException) as caught:
        health.readiness(session, SimpleNamespace(app_env="production", redis_required=False))
    assert caught.value.status_code == 503
    assert caught.value.detail["components"]["schema"] == "unavailable"
