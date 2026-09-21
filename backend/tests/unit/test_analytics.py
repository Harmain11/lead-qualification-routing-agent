from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from lead_agent.api.main import app
from lead_agent.application.analytics import AnalyticsService
from lead_agent.application.services import process_lead
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import LeadOutcomeORM, WorkspaceORM
from lead_agent.infrastructure.db.session import get_db


def _factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _payload(
    identifier: str,
    *,
    source: str = "website",
    received_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "external_id": identifier,
        "source": source,
        "full_name": f"Lead {identifier}",
        "email": f"{identifier}@example.test",
        "job_title": "VP of Operations",
        "company_name": f"Company {identifier}",
        "industry": "SaaS",
        "employee_count": 180,
        "geography": "United States",
        "budget": 30000,
        "product_or_service": "Lead routing automation",
        "message": (
            "We need Salesforce lead routing live this month. I own the budget and need a "
            "technical discovery call this week because slow response is losing opportunities."
        ),
        "received_at": (received_at or datetime.now(UTC)).isoformat(),
    }


def _add_outcome(
    session: Session,
    lead_id: UUID,
    workspace_id: UUID,
    *,
    stage: str,
    opportunity: float,
    revenue: float,
    data_source: str = "crm",
) -> None:
    now = datetime.now(UTC)
    session.add(
        LeadOutcomeORM(
            id=uuid4(),
            workspace_id=workspace_id,
            lead_id=lead_id,
            pipeline_stage=stage,
            opportunity_value=opportunity,
            closed_revenue=revenue,
            currency="USD",
            data_source=data_source,
            opened_at=now - timedelta(days=2),
            closed_at=now if stage.startswith("closed_") else None,
            created_at=now,
            updated_at=now,
        )
    )
    session.commit()


def test_dashboard_channel_pipeline_and_revenue_analytics(
    icp: IcpConfig, routing: RoutingConfig
) -> None:
    factory = _factory()
    workspace_id = uuid4()
    with factory() as session:
        _, _, website = process_lead(
            session, _payload("web-analytics"), icp, routing, workspace_id=workspace_id
        )
        _, _, email = process_lead(
            session,
            _payload("email-analytics", source="email"),
            icp,
            routing,
            workspace_id=workspace_id,
        )
        _add_outcome(
            session,
            website.id,
            workspace_id,
            stage="closed_won",
            opportunity=30000,
            revenue=30000,
        )
        _add_outcome(
            session,
            email.id,
            workspace_id,
            stage="proposal",
            opportunity=18000,
            revenue=0,
        )

        service = AnalyticsService(session, workspace_id)
        dashboard = service.dashboard()
        channels = service.channels()
        pipeline = service.pipeline()
        revenue = service.revenue()

    metrics = {item["key"]: item["value"] for item in dashboard["metrics"]}
    assert metrics["total_leads"] == 2
    assert metrics["qualification_rate"] == 100
    assert metrics["hot_leads"] == 2
    assert metrics["closed_won_revenue"] == 30000
    assert {row["channel"] for row in channels["channels"]} == {"website", "email"}
    assert {row["stage"]: row["count"] for row in pipeline["stages"]}["closed"] == 1
    assert revenue["pipeline_value"] == 18000
    assert revenue["total_revenue"] == 30000
    assert revenue["data_status"] == "crm"


def test_period_filter_excludes_old_records(icp: IcpConfig, routing: RoutingConfig) -> None:
    factory = _factory()
    workspace_id = uuid4()
    with factory() as session:
        process_lead(
            session,
            _payload("recent", received_at=datetime.now(UTC) - timedelta(days=2)),
            icp,
            routing,
            workspace_id=workspace_id,
        )
        process_lead(
            session,
            _payload("old", received_at=datetime.now(UTC) - timedelta(days=45)),
            icp,
            routing,
            workspace_id=workspace_id,
        )
        weekly = AnalyticsService(session, workspace_id).dashboard("weekly")
        annual = AnalyticsService(session, workspace_id).dashboard("annual")

    weekly_total = next(item["value"] for item in weekly["metrics"] if item["key"] == "total_leads")
    annual_total = next(item["value"] for item in annual["metrics"] if item["key"] == "total_leads")
    assert weekly_total == 1
    assert annual_total == 2


def test_workspace_isolation_and_demo_live_separation(
    icp: IcpConfig, routing: RoutingConfig
) -> None:
    factory = _factory()
    live_id, demo_id = uuid4(), uuid4()
    with factory() as session:
        process_lead(session, _payload("live"), icp, routing, workspace_id=live_id)
        process_lead(session, _payload("demo"), icp, routing, workspace_id=demo_id)
        demo = session.get(WorkspaceORM, demo_id)
        assert demo is not None
        demo.is_demo = True
        session.commit()

        live_result = AnalyticsService(session, live_id).dashboard()
        demo_result = AnalyticsService(session, demo_id).dashboard()

    assert live_result["metadata"]["data_mode"] == "live"
    assert demo_result["metadata"]["data_mode"] == "demo"
    assert live_result["recent_leads"][0]["name"] == "Lead live"
    assert demo_result["recent_leads"][0]["name"] == "Lead demo"


def test_agent_activity_and_manual_review_are_workspace_scoped(
    icp: IcpConfig, routing: RoutingConfig
) -> None:
    factory = _factory()
    workspace_id = uuid4()
    other_id = uuid4()
    with factory() as session:
        _, _, lead = process_lead(
            session, _payload("review"), icp, routing, workspace_id=workspace_id
        )
        _, _, other_lead = process_lead(
            session, _payload("other"), icp, routing, workspace_id=other_id
        )
        assert lead.analysis is not None
        lead.analysis.requires_manual_review = True
        lead.analysis.status = "manual_review"
        lead.analysis.error_message = "Confidence needs human validation."
        session.commit()

        activity = AnalyticsService(session, workspace_id).agent_activity()
        review = AnalyticsService(session, workspace_id).manual_review()

    assert activity["activity"]
    assert all(item["lead_id"] != str(other_lead.id) for item in activity["activity"])
    assert any(
        item["name"] == "Analytics Agent" and item["status"] == "future"
        for item in activity["capabilities"]
    )
    assert review["total"] == 1
    assert review["items"][0]["review_reasons"] == ["Confidence needs human validation."]


def test_analytics_api_smoke_and_unknown_workspace(icp: IcpConfig, routing: RoutingConfig) -> None:
    factory = _factory()
    workspace_id = uuid4()
    with factory() as session:
        process_lead(session, _payload("api"), icp, routing, workspace_id=workspace_id)

    def override_db() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            headers = {"X-Workspace-ID": str(workspace_id)}
            paths = [
                "dashboard",
                "channels",
                "pipeline",
                "revenue",
                "reps",
                "time-series",
                "agent-activity",
                "manual-review",
            ]
            for path in paths:
                response = client.get(f"/v1/analytics/{path}?range=monthly", headers=headers)
                assert response.status_code == 200, response.text
                assert response.json()["metadata"]["workspace_id"] == str(workspace_id)

            unavailable = client.get(
                "/v1/analytics/dashboard", headers={"X-Workspace-ID": str(uuid4())}
            )
            assert unavailable.status_code == 404
    finally:
        app.dependency_overrides.clear()
