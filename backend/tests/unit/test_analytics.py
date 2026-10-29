from __future__ import annotations

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from lead_agent.api.main import app
from lead_agent.application.analytics import AnalyticsService
from lead_agent.application.services import process_lead
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    LeadFollowUpEmailORM,
    LeadORM,
    LeadOutcomeORM,
    PipelineEventORM,
    WorkspaceORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.reasoning.deterministic import DeterministicReasoningProvider


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
        # Analytics consumes recorded bands; do not depend on a published workspace ICP.
        website.qualification.classification = "hot"
        email.qualification.classification = "hot"
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


def test_sales_pipeline_uses_recorded_progress_followups_and_outcomes(
    icp: IcpConfig, routing: RoutingConfig
) -> None:
    factory = _factory()
    workspace_id = uuid4()
    now = datetime.now(UTC)
    identifiers = (
        "new",
        "draft",
        "sent",
        "contacted",
        "responded",
        "meeting",
        "opportunity",
        "proposal",
        "negotiation",
        "won",
        "lost",
    )
    with factory() as session:
        leads: dict[str, LeadORM] = {}
        for identifier in identifiers:
            _, _, leads[identifier] = process_lead(
                session,
                _payload(f"sales-pipeline-{identifier}"),
                icp,
                routing,
                workspace_id=workspace_id,
            )

        def add_progress(identifier: str, stage: str, event_stage: str = "manual_progress") -> None:
            lead = leads[identifier]
            session.add(
                PipelineEventORM(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    lead_id=lead.id,
                    stage=event_stage,
                    status=stage,
                    details={"stage": stage},
                    created_at=now,
                )
            )

        add_progress("contacted", "contacted")
        add_progress("responded", "responded")
        add_progress("meeting", "scheduled", event_stage="meeting_scheduled")

        for identifier, status in (("draft", "draft"), ("sent", "sent")):
            lead = leads[identifier]
            session.add(
                LeadFollowUpEmailORM(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    lead_id=lead.id,
                    kind="ai_follow_up",
                    recipient=lead.email,
                    subject="Follow-up",
                    status=status,
                    attempts=0,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()

        for identifier, stage in (
            ("opportunity", "opportunity"),
            ("proposal", "proposal"),
            ("negotiation", "negotiation"),
            ("won", "closed_won"),
            ("lost", "closed_lost"),
        ):
            _add_outcome(
                session,
                leads[identifier].id,
                workspace_id,
                stage=stage,
                opportunity=5000,
                revenue=5000 if stage == "closed_won" else 0,
            )

        pipeline = AnalyticsService(session, workspace_id).pipeline()

    counts = {stage["stage"]: stage["count"] for stage in pipeline["sales_stages"]}
    assert counts == {
        "new_lead": 1,
        "contacted": 1,
        "follow_up_drafted": 1,
        "follow_up_sent": 1,
        "responded": 1,
        "meeting_scheduled": 1,
        "opportunity": 1,
        "proposal_sent": 1,
        "negotiation": 1,
        "contract": 0,
        "closed_won": 1,
        "closed_lost": 1,
    }
    values = {stage["stage"]: stage["value"] for stage in pipeline["sales_stages"]}
    assert values["proposal_sent"] == 5000
    assert values["negotiation"] == 5000
    assert values["contract"] == 0


def test_channel_percentages_and_scores_use_recorded_results(
    icp: IcpConfig, routing: RoutingConfig
) -> None:
    factory = _factory()
    workspace_id = uuid4()
    with factory() as session:
        for identifier, score, band in [
            ("low-score", 24, "nurture"),
            ("qualified-score", 80, "warm"),
        ]:
            _, _, lead = process_lead(
                session,
                _payload(identifier),
                icp,
                routing,
                workspace_id=workspace_id,
            )
            lead.qualification.total_score = score
            lead.qualification.classification = band
        session.commit()
        rows = AnalyticsService(session, workspace_id=workspace_id).channels()["channels"]
        assert len(rows) == 1
        row = rows[0]
        assert row["lead_volume"] == 2
        assert row["scored_leads"] == 2
        assert row["qualified_leads"] == 1
        # Two leads are below the sample minimum, so the rate is withheld, not "50%".
        assert row["qualification_rate"] is None
        assert row["rate_sample_minimum"] == 5
        assert row["average_score"] == 52


def test_extended_analytics_uses_recorded_booking_and_close_facts(
    icp: IcpConfig, routing: RoutingConfig
) -> None:
    factory = _factory()
    workspace_id = uuid4()
    received_at = datetime.now(UTC) - timedelta(days=10)
    with factory() as session:
        _, _, lead = process_lead(
            session,
            _payload("extended-booked", received_at=received_at),
            icp,
            routing,
            workspace_id=workspace_id,
        )
        assert lead.qualification is not None
        lead.qualification.created_at = received_at + timedelta(minutes=7)
        _, _, manually_added = process_lead(
            session,
            _payload("extended-manual", received_at=received_at),
            icp,
            routing,
            workspace_id=workspace_id,
        )
        assert manually_added.qualification is not None
        manually_added.source_detail = "manual-control-center"
        manually_added.qualification.classification = "nurture"
        manually_added.qualification.created_at = received_at + timedelta(minutes=12)
        session.add(
            PipelineEventORM(
                id=uuid4(),
                workspace_id=workspace_id,
                lead_id=lead.id,
                stage="manual_progress",
                status="meeting_scheduled",
                details={"stage": "meeting_scheduled"},
                created_at=received_at + timedelta(days=1),
            )
        )
        session.commit()
        _add_outcome(
            session,
            lead.id,
            workspace_id,
            stage="closed_won",
            opportunity=12000,
            revenue=12000,
        )
        _add_outcome(
            session,
            manually_added.id,
            workspace_id,
            stage="proposal",
            opportunity=8000,
            revenue=0,
            data_source="manual",
        )
        service = AnalyticsService(session, workspace_id)
        extended = service.extended()
        dashboard = service.dashboard()
        channels = service.channels()["channels"]
        manual_channel = next(row for row in channels if row["channel"] == "manual")

    assert extended["speed_to_qualify"]["median_minutes"] == 9.5
    assert extended["speed_to_qualify"]["sample_size"] == 2
    assert extended["qualified_to_booked"]["booked"] == 1
    assert extended["qualified_to_booked"]["rate"] == 100
    assert sum(row["closed"] for row in extended["calibration"]) == 1
    assert extended["sales_cycle_by_segment"][0]["sample_size"] == 1
    assert extended["revenue_by_conversion_type"][0]["type"] == "untyped"
    assert extended["revenue_by_conversion_type"][0]["won"] == 1
    assert manual_channel["booked_leads"] == 0
    assert manual_channel["closed_won_count"] == 0
    assert dashboard["active_pipeline_by_origin"] == [
        {"origin": "manual", "value": 8000.0}
    ]
    assert {row["key"] for row in extended["data_gaps"]} >= {
        "override_rate",
        "loss_reasons",
        "revenue_motion",
        "rep_first_response",
    }


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


def test_concurrent_workspace_analyses_remain_attached_to_their_own_leads(
    icp: IcpConfig, routing: RoutingConfig, tmp_path
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-analysis.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    analysis_barrier = Barrier(2)

    class ConcurrentProvider:
        provider_name = "test-concurrent"
        model_name = "workspace-isolation-fixture"
        prompt_version = "test-v1"

        def analyze(self, lead, configuration):
            analysis_barrier.wait(timeout=5)
            output = DeterministicReasoningProvider().analyze(lead, configuration)
            return output.model_copy(
                update={
                    "summary": output.summary.model_copy(
                        update={"who": lead.full_name}
                    )
                }
            )

    provider = ConcurrentProvider()
    workspaces = (uuid4(), uuid4())
    identifiers = ("concurrent-one", "concurrent-two")

    def run_analysis(index: int):
        with factory() as session:
            return process_lead(
                session,
                _payload(identifiers[index]),
                icp,
                routing,
                provider=provider,
                workspace_id=workspaces[index],
            )[2].id

    with ThreadPoolExecutor(max_workers=2) as executor:
        lead_ids = list(executor.map(run_analysis, range(2)))

    with factory() as session:
        records = session.scalars(select(LeadORM).where(LeadORM.id.in_(lead_ids))).all()
        observed = {
            lead.workspace_id: (lead.full_name, lead.analysis.structured_output["summary"]["who"])
            for lead in records
            if lead.analysis is not None
        }

    assert observed == {
        workspaces[0]: ("Lead concurrent-one", "Lead concurrent-one"),
        workspaces[1]: ("Lead concurrent-two", "Lead concurrent-two"),
    }


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
        lead.job_title = None
        lead.full_name = "Name not provided"
        other_lead.email = lead.email
        other_lead.job_title = "Cross-workspace value"
        lead.analysis.requires_manual_review = True
        lead.analysis.status = "manual_review"
        lead.analysis.error_message = "Confidence needs human validation."
        session.commit()

        activity = AnalyticsService(session, workspace_id).agent_activity()
        review = AnalyticsService(session, workspace_id).manual_review()

    assert activity["activity"]
    assert all(item["lead_id"] != str(other_lead.id) for item in activity["activity"])
    assert any(
        item["name"] == "Follow-up Agent" and item["status"] == "implemented"
        for item in activity["capabilities"]
    )
    assert any(
        item["name"] == "Enrichment Agent" and item["status"] == "implemented"
        for item in activity["capabilities"]
    )
    assert any(
        item["name"] == "Analytics Agent" and item["status"] == "implemented"
        for item in activity["capabilities"]
    )
    insights = {item["agent"]: item for item in activity["insights"]}
    enrichment_metrics = {
        metric["key"]: metric["value"] for metric in insights["Enrichment Agent"]["metrics"]
    }
    analytics_metrics = {
        metric["key"]: metric["value"] for metric in insights["Analytics Agent"]["metrics"]
    }
    assert enrichment_metrics["leads_reviewed"] == 1
    assert enrichment_metrics["exact_email_suggestions"] == 0
    assert analytics_metrics["leads_analyzed"] == 1
    assert "SQL-ready" in insights["Analytics Agent"]["summary"]
    assert review["total"] == 1
    assert review["items"][0]["review_reasons"] == [
        "Full name field (Name not provided) is missing or contains a placeholder value."
    ]


def test_enrichment_agent_suggests_only_unambiguous_exact_email_matches(
    icp: IcpConfig, routing: RoutingConfig
) -> None:
    factory = _factory()
    workspace_id = uuid4()
    with factory() as session:
        _, _, target = process_lead(
            session, _payload("enrichment-target"), icp, routing, workspace_id=workspace_id
        )
        _, _, source_one = process_lead(
            session, _payload("enrichment-source-one"), icp, routing, workspace_id=workspace_id
        )
        _, _, source_two = process_lead(
            session, _payload("enrichment-source-two"), icp, routing, workspace_id=workspace_id
        )
        source_one.email = target.email
        source_two.email = target.email
        target.job_title = None
        source_one.job_title = "Director"
        source_two.job_title = "VP of Sales"
        target.phone = None
        source_one.phone = "+1 555 0100"
        source_two.phone = "+1 555 0100"
        target.industry = None
        source_one.industry = "SaaS"
        source_two.industry = "SaaS"
        session.commit()

        activity = AnalyticsService(session, workspace_id).agent_activity()

        assert target.job_title is None
        assert target.phone is None
        assert target.industry is None

    enrichment = next(
        item for item in activity["insights"] if item["agent"] == "Enrichment Agent"
    )
    metrics = {metric["key"]: metric["value"] for metric in enrichment["metrics"]}
    assert metrics["exact_email_suggestions"] == 2
    assert any("read-only" in detail.lower() for detail in enrichment["details"])
    assert any("crm-imported" in detail.lower() for detail in enrichment["details"])


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
                "extended",
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


def test_rates_are_withheld_below_the_sample_minimum() -> None:
    from lead_agent.application.analytics import MIN_RATE_SAMPLE, _rate

    assert _rate(1, 1) is None
    assert _rate(0, MIN_RATE_SAMPLE - 1) is None
    assert _rate(2, MIN_RATE_SAMPLE) == 40.0
