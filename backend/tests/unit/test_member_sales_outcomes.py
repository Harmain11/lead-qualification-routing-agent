from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.main import app
from lead_agent.api.routes.leads import LeadProgressUpdate, update_lead_progress
from lead_agent.application.analytics import AnalyticsService
from lead_agent.application.services import process_lead
from lead_agent.domain.routing import NotificationDelivery
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    LeadWinNotificationORM,
    ProviderConnectionORM,
    UserORM,
    WorkspaceMembershipORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.repositories import ensure_workspace, get_current_routing
from lead_agent.infrastructure.slack import get_workspace_slack_notification_resolver


class SlackFake:
    def __init__(self, connection_id):
        self.connection_id = connection_id
        self.channel_id = "C123"
        self.sent = []
        self.fail_dm = False

    def send(self, message):
        self.sent.append(("channel", message.deduplication_key, message.text))
        return NotificationDelivery(state="sent", external_id="channel-ts")

    def send_direct(self, member_id, message):
        self.sent.append((member_id, message.deduplication_key, message.text))
        return NotificationDelivery(
            state="retryable" if self.fail_dm else "sent",
            external_id=None if self.fail_dm else "dm-ts",
            error_message="Rate limited" if self.fail_dm else None,
        )


@pytest.fixture
def sales_store(icp, routing):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        workspace_id, other_id = uuid4(), uuid4()
        ensure_workspace(session, workspace_id, name="Sales test")
        ensure_workspace(session, other_id, name="Other test")
        now = datetime.now(UTC)
        members = []
        for index, workspace in enumerate((workspace_id, workspace_id, other_id)):
            user = UserORM(
                id=uuid4(), email=f"member{index}@example.test", display_name="Morgan",
                password_hash="unused-test-hash", status="active", created_at=now, updated_at=now,
            )
            session.add(user)
            session.flush()
            member = WorkspaceMembershipORM(
                id=uuid4(), workspace_id=workspace, user_id=user.id, role="sales_rep",
                status="active", permission_overrides={}, created_at=now, updated_at=now,
                slack_member_id=f"U1234567{index}", slack_team_id="T123",
            )
            session.add(member)
            members.append(member)
        connection = ProviderConnectionORM(
            id=uuid4(), workspace_id=workspace_id, provider_type="slack",
            account_name="Test Slack", auth_type="oauth2", status="verified",
            scopes=["chat:write", "channels:read"], secret_reference="test-only",
            credential_state="configured", connection_metadata={
                "slack_team_id": "T123", "slack_channel_id": "C123",
            }, is_active=True, created_at=now, updated_at=now,
        )
        session.add(connection)
        session.commit()
        leads = []
        for index in range(2):
            _, _, lead = process_lead(session, {
                "source": "website", "external_id": f"sales-{index}",
                "full_name": "Test Lead", "email": f"lead{index}@example.test",
                "company_name": "Northstar", "job_title": "VP of Operations",
                "industry": "SaaS", "employee_count": 180, "geography": "United States",
                "budget": 30000, "product_or_service": "Lead routing automation",
                "message": (
                    "I own the budget and need Salesforce routing live this month. "
                    "Please book a discovery call."
                ),
            }, icp, routing, workspace_id=workspace_id)
            leads.append(lead)
        provider = SlackFake(connection.id)
        resolver = SimpleNamespace(resolve_for_workspace=lambda *args: provider)
        workspace = WorkspaceContext(
            workspace_id=workspace_id, user_id=members[0].user_id,
            actor_id=str(members[0].user_id), role="owner",
            permissions=frozenset({"leads.view", "leads.edit", "analytics.view", "members.manage"}),
        )
        yield SimpleNamespace(
            session=session, leads=leads, members=members, provider=provider,
            resolver=resolver, workspace=workspace, policies=(icp, routing),
        )
    engine.dispose()


def close(store, index=0, **changes):
    data = dict(stage="converted", note="Confirmed successful conversion.",
                conversion_type="project", closed_revenue=Decimal("1200.50"),
                credited_user_id=store.members[index].user_id, send_direct_message=True)
    data.update(changes)
    return update_lead_progress(
        store.leads[index].id, LeadProgressUpdate(**data), session=store.session,
        policies=store.policies, notification_provider=None,
        workspace=store.workspace, slack_resolver=store.resolver,
    )


def test_project_sale_comparison_uses_ids_not_matching_names(sales_store, monkeypatch):
    store = sales_store
    def forbid_reanalysis(*args, **kwargs):
        raise AssertionError("A closing outcome must not trigger AI analysis or routing.")
    monkeypatch.setattr(
        "lead_agent.api.routes.leads._reanalyze_lead_with_history", forbid_reanalysis
    )
    original_route = get_current_routing(
        store.session, store.leads[0].id, store.workspace.workspace_id
    ).id
    first = close(store)
    close(store, index=1, conversion_type="sale", closed_revenue=Decimal("500.00"))
    assert first.outcome.pipeline_stage == "closed_won"
    assert first.outcome.credited_user_id == store.members[0].user_id
    assert first.outcome.currency == "USD"
    assert {receipt["status"] for receipt in first.outcome.notifications} == {"sent"}
    assert get_current_routing(
        store.session, store.leads[0].id, store.workspace.workspace_id
    ).id == original_route
    report = AnalyticsService(store.session, store.workspace.workspace_id).reps()
    by_id = {row["user_id"]: row for row in report["members"]}
    assert len(by_id) == 2  # Both members have the same name; the foreign member is excluded.
    assert by_id[str(store.members[0].user_id)]["project_wins"] == 1
    assert by_id[str(store.members[1].user_id)]["sale_wins"] == 1
    assert by_id[str(store.members[0].user_id)]["revenue"] == 1200.5
    revenue = AnalyticsService(store.session, store.workspace.workspace_id).revenue()
    assert revenue["data_status"] == "manual"


def test_repeated_save_and_partial_retry_do_not_duplicate_team_message(sales_store):
    store = sales_store
    store.provider.fail_dm = True
    first = close(store)
    assert {receipt["status"] for receipt in first.outcome.notifications} == {"sent", "retryable"}
    close(store, closed_revenue=Decimal("1250.00"))
    assert len(store.provider.sent) == 2
    from lead_agent.application.lead_outcomes import send_win_notifications
    store.provider.fail_dm = False
    send_win_notifications(
        store.session, store.leads[0], store.resolver, store.workspace.actor_id, retry=True
    )
    assert [message[0] for message in store.provider.sent].count("channel") == 1
    assert len(store.provider.sent) == 3
    receipts = store.session.scalars(select(LeadWinNotificationORM)).all()
    assert len(receipts) == 2 and all(receipt.status == "sent" for receipt in receipts)


def test_win_direct_message_is_not_sent_without_explicit_opt_in(sales_store):
    store = sales_store
    result = close(store, send_direct_message=False)
    assert [message[0] for message in store.provider.sent] == ["channel"]
    assert [receipt["destination"] for receipt in result.outcome.notifications] == ["channel"]


def test_lost_and_missing_slack_link_do_not_undo_outcome(sales_store):
    store = sales_store
    store.members[0].slack_member_id = None
    store.session.commit()
    result = close(store, closed_revenue=None)
    assert result.outcome.closed_revenue is None
    statuses = {
        receipt["destination"]: receipt["status"] for receipt in result.outcome.notifications
    }
    assert statuses == {
        "channel": "sent", "direct_message": "not_configured",
    }
    result = close(store, index=1, stage="lost", conversion_type=None,
                   closed_revenue=None, send_direct_message=False)
    assert result.outcome.pipeline_stage == "closed_lost"
    assert result.outcome.notifications == []
    assert len(store.provider.sent) == 1


def test_workspace_and_slack_team_boundaries(sales_store):
    store = sales_store
    with pytest.raises(HTTPException) as error:
        close(store, credited_user_id=store.members[2].user_id)
    assert error.value.status_code == 422
    assert store.leads[0].outcome is None
    store.members[0].slack_team_id = "DIFFERENT_TEAM"
    store.session.commit()
    result = close(store)
    direct = next(
        item for item in result.outcome.notifications if item["destination"] == "direct_message"
    )
    assert direct["status"] == "not_configured"
    assert len(store.provider.sent) == 1


@pytest.mark.parametrize("fields", [
    {}, {"conversion_type": "sale", "closed_revenue": -1},
    {"conversion_type": "sale", "closed_revenue": "1.999"},
    {"conversion_type": "invalid"},
])
def test_invalid_conversion_facts_rejected(fields):
    with pytest.raises(ValidationError):
        LeadProgressUpdate(stage="converted", note="Confirmed conversion.", **fields)


def test_http_permissions_mapping_and_cross_workspace_lead(sales_store):
    store = sales_store
    app.dependency_overrides[get_db] = lambda: store.session
    app.dependency_overrides[get_workspace_context] = lambda: store.workspace
    app.dependency_overrides[get_workspace_slack_notification_resolver] = lambda: store.resolver
    try:
        with TestClient(app) as client:
            options = client.get("/v1/leads/closing-options")
            assert options.status_code == 200 and len(options.json()) == 2
            linked = client.patch(
                f"/v1/members/{store.members[0].id}/slack-link",
                json={"slack_member_id": "U99999999"},
            )
            assert linked.status_code == 200 and linked.json()["slack_team_id"] == "T123"
            foreign = client.patch(
                f"/v1/members/{store.members[2].id}/slack-link", json={"slack_member_id": None}
            )
            assert foreign.status_code == 404
            store.workspace = WorkspaceContext(
                workspace_id=store.workspace.workspace_id, permissions=frozenset({"leads.view"})
            )
            denied = client.post(f"/v1/leads/{store.leads[0].id}/progress", json={
                "stage": "converted", "note": "Confirmed sale.", "conversion_type": "sale",
            })
            assert denied.status_code == 403
    finally:
        app.dependency_overrides.clear()
