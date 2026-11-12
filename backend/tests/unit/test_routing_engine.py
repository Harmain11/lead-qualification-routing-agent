from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from lead_agent.application.routing import _action_plan_excerpt, retry_current_notification
from lead_agent.application.services import process_lead
from lead_agent.domain.configuration import RepConfig, RoutingRuleConfig
from lead_agent.domain.routing import NotificationDelivery
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    NotificationORM,
    PipelineEventORM,
    RoutingDecisionORM,
    UserORM,
    WorkspaceMembershipORM,
    WorkspaceORM,
)
from lead_agent.infrastructure.notifications.mock import MockNotificationProvider
from lead_agent.infrastructure.repositories import get_current_routing, list_routing_history


def payload(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "external_id": "routing-test-001",
        "source": "website",
        "source_detail": "routing-test",
        "full_name": "Maya Thompson",
        "email": "maya@example.test",
        "job_title": "VP of Operations",
        "company_name": "Northstar SaaS",
        "industry": "SaaS",
        "employee_count": 180,
        "geography": "United States",
        "budget": 30000,
        "product_or_service": "Lead routing automation",
        "message": (
            "We need an API integration this month. Please send a proposal this week "
            "because manual qualification is causing missed leads. Our budget is $30,000."
        ),
    }
    value.update(overrides)
    return value


def session_for(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{tmp_path / 'routing.db'}")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_action_plan_excerpt_keeps_word_boundaries() -> None:
    pain = "Automate dense workflows and reduce manual assembly across tariff classification validation reviews."

    excerpt = _action_plan_excerpt(pain, limit=48)

    assert excerpt == "Automate dense workflows and reduce manual…"
    assert not excerpt.endswith("manu…")


def test_live_workspaces_assign_their_own_lowest_load_sales_rep(icp, routing, tmp_path) -> None:
    with session_for(tmp_path) as session:
        now = datetime.now(UTC)
        workspace_ids = [uuid4(), uuid4()]
        rep_ids: list[list[str]] = []
        for index, workspace_id in enumerate(workspace_ids):
            session.add(WorkspaceORM(
                id=workspace_id, name=f"Team {index}", slug=f"team-{index}-{uuid4().hex}",
                status="active", is_demo=False, created_at=now, updated_at=now,
            ))
            current_ids = []
            for rep_index in range(2):
                user = UserORM(
                    id=uuid4(), email=f"rep-{index}-{rep_index}@example.test",
                    display_name=f"Rep {index}-{rep_index}", password_hash="test-hash",
                    status="active", created_at=now, updated_at=now,
                )
                session.add(user)
                session.add(WorkspaceMembershipORM(
                    id=uuid4(), workspace_id=workspace_id, user_id=user.id,
                    role="sales_rep", status="active", created_at=now, updated_at=now,
                ))
                current_ids.append(str(user.id))
            rep_ids.append(current_ids)
        session.commit()

        chosen = []
        for index in range(3):
            _, _, lead = process_lead(
                session, payload(external_id=f"balanced-{index}"), icp, routing,
                workspace_id=workspace_ids[0],
            )
            chosen.append(get_current_routing(session, lead.id, workspace_ids[0]).assigned_rep_id)
        assert set(chosen[:2]) == set(rep_ids[0])
        assert chosen[2] == chosen[0]

        _, _, other_lead = process_lead(
            session, payload(external_id="other-team"), icp, routing,
            workspace_id=workspace_ids[1],
        )
        other_decision = get_current_routing(session, other_lead.id, workspace_ids[1])
        assert other_decision.assigned_rep_id in rep_ids[1]
        assert other_decision.assigned_rep_id not in rep_ids[0]
        assert other_decision.action_plan["follow_up_strategy"]


def test_hot_routes_to_senior_rep_and_sends_high_priority_notification(
    icp, routing, tmp_path
) -> None:
    provider = MockNotificationProvider()
    with session_for(tmp_path) as session:
        _, qualification, lead = process_lead(
            session, payload(), icp, routing, notification_provider=provider
        )
        decision = get_current_routing(session, lead.id)

        assert qualification.classification == "hot"
        assert qualification.icp_source == "not_configured"
        assert "icp_fit" not in qualification.component_scores
        assert lead.analysis is not None
        assert (
            lead.analysis.structured_output["icp_evaluation"]["profile_version"]
            == "not_configured"
        )
        assert decision is not None
        assert decision.assigned_queue == "senior_rep"
        assert decision.assigned_rep_id == "rep-alex"
        assert decision.priority == "urgent"
        assert decision.notification_state == "sent"
        assert len(provider.sent) == 1
        assert "[HIGH PRIORITY]" in provider.sent[0].text
        assert "Score:" in provider.sent[0].text
        assert "ICP fit was not scored" in provider.sent[0].text
        assert "publish a custom ICP in Settings" in provider.sent[0].text
        assert "Summary:" in provider.sent[0].text
        assert "Lead temperature: Hot" in provider.sent[0].text
        assert "Follow-up:" in provider.sent[0].text
        assert "Conversion goal:" in provider.sent[0].text


def test_all_qualification_bands_have_configured_default_routes(icp, routing, tmp_path) -> None:
    cases = [
        (
            payload(
                external_id="warm",
                job_title="Director of Marketing",
                geography="United Kingdom",
                industry="Professional Services",
                budget=15000,
                message="We are evaluating pricing for next quarter.",
            ),
            "needs_qualifying",
            "sdr",
            "rep-jordan",
        ),
        (
            payload(
                external_id="needs",
                job_title="Marketing Specialist",
                geography="United Arab Emirates",
                industry="Retail",
                budget=None,
                message=(
                    "We need help with a manual process and missed inquiries. "
                    "Please explain an audit."
                ),
            ),
            "nurture",
            "nurture",
            None,
        ),
        (
            payload(
                external_id="nurture",
                job_title=None,
                employee_count=None,
                geography="Unknown",
                industry="Professional Services",
                budget=None,
                message="Just curious about what you do. Maybe someday.",
            ),
            "disqualify",
            "archive",
            None,
        ),
        (
            payload(
                external_id="disqualify",
                job_title="Student",
                product_or_service="School project",
                message="I need help with homework for a school project.",
            ),
            "nurture",
            "nurture",
            None,
        ),
    ]
    for lead_payload, band, queue, rep_id in cases:
        with session_for(tmp_path / band) as session:
            _, result, lead = process_lead(session, lead_payload, icp, routing)
            decision = get_current_routing(session, lead.id)
            assert result.classification == band
            assert decision is not None
            assert decision.assigned_queue == queue
            assert decision.assigned_rep_id == rep_id
            assert decision.action_plan


def test_manual_review_precedes_normal_sales_assignment(icp, routing, tmp_path) -> None:
    class LowConfidenceProvider:
        provider_name = "test"
        model_name = "low-confidence"
        prompt_version = "test-v1"

        def analyze(self, lead, icp):
            from lead_agent.infrastructure.reasoning.deterministic import (
                DeterministicReasoningProvider,
            )

            return (
                DeterministicReasoningProvider()
                .analyze(lead, icp)
                .model_copy(update={"overall_confidence": 0.1})
            )

    low_confidence_review_routing = routing.model_copy(
        update={
            "manual_review": routing.manual_review.model_copy(
                update={"low_confidence": True}
            )
        }
    )
    with session_for(tmp_path) as session:
        _, _, lead = process_lead(
            session, payload(), icp, low_confidence_review_routing, LowConfidenceProvider()
        )
        decision = get_current_routing(session, lead.id)
        assert decision is not None
        assert decision.manual_review is True
        assert decision.assigned_queue == "manual_review"
        assert decision.assigned_rep_id is None


def test_specialized_rule_precedence_is_deterministic(icp, routing, tmp_path) -> None:
    specialized = routing.model_copy(
        update={
            "rules": [
                RoutingRuleConfig(
                    name="later-industry-rule",
                    precedence=50,
                    industries=["saas"],
                    queue="standard_rep",
                    reason="Industry fallback",
                ),
                RoutingRuleConfig(
                    name="earlier-geography-rule",
                    precedence=10,
                    geographies=["united states"],
                    queue="senior_rep",
                    priority="high",
                    reason="Geography priority",
                ),
            ]
        }
    )
    with session_for(tmp_path) as session:
        _, _, lead = process_lead(session, payload(external_id="precedence"), icp, specialized)
        decision = get_current_routing(session, lead.id)
        assert decision is not None
        assert decision.assigned_queue == "senior_rep"
        assert decision.matched_rules == ["earlier-geography-rule", "later-industry-rule"]
        assert "precedence" in decision.routing_reason


def test_inactive_rep_is_never_assigned(icp, routing, tmp_path) -> None:
    inactive = routing.model_copy(
        update={"reps": [rep.model_copy(update={"active": False}) for rep in routing.reps]}
    )
    with session_for(tmp_path) as session:
        _, _, lead = process_lead(session, payload(external_id="inactive"), icp, inactive)
        decision = get_current_routing(session, lead.id)
        assert decision is not None
        assert decision.assigned_queue == "senior_rep"
        assert decision.assigned_rep_id is None
        assert "No active" in decision.routing_reason


def test_notification_failure_is_persisted_and_does_not_drop_routing(
    icp, routing, tmp_path
) -> None:
    provider = MockNotificationProvider(fail=True)
    with session_for(tmp_path) as session:
        _, _, lead = process_lead(
            session,
            payload(external_id="notification-failure"),
            icp,
            routing,
            notification_provider=provider,
        )
        decision = get_current_routing(session, lead.id)
        notifications = list(session.scalars(select(NotificationORM)))
        assert decision is not None
        assert decision.notification_state == "retryable"
        assert len(notifications) == 1
        assert notifications[0].status == "retryable"
        assert notifications[0].error_message


def test_duplicate_lead_replay_does_not_duplicate_route_or_notification(
    icp, routing, tmp_path
) -> None:
    provider = MockNotificationProvider()
    with session_for(tmp_path) as session:
        first = process_lead(
            session,
            payload(external_id="duplicate-route"),
            icp,
            routing,
            notification_provider=provider,
        )[2]
        second = process_lead(
            session,
            payload(external_id="duplicate-route"),
            icp,
            routing,
            notification_provider=provider,
        )[2]
        assert first.id == second.id
        assert session.scalar(select(func.count()).select_from(RoutingDecisionORM)) == 1
        assert session.scalar(select(func.count()).select_from(NotificationORM)) == 1
        assert session.scalar(select(func.count()).select_from(PipelineEventORM)) == 6
        assert len(provider.sent) == 1


def test_explicit_reroute_preserves_history_and_creates_one_new_notification(
    icp, routing, tmp_path
) -> None:
    provider = MockNotificationProvider()
    with session_for(tmp_path) as session:
        _, _, lead = process_lead(
            session, payload(external_id="reroute"), icp, routing, notification_provider=provider
        )
        from lead_agent.application.routing import route_and_notify

        route_and_notify(
            session,
            lead,
            routing,
            notification_provider=provider,
            force=True,
            reroute_reason="Operator correction",
        )
        history = list_routing_history(session, lead.id)
        assert len(history) == 2
        assert sum(len(row.notifications) for row in history) == 2
        assert len(provider.sent) == 2
        assert any("Operator correction" in row.routing_reason for row in history)


def test_slack_duplicate_replay_and_reroute_notify_once_per_lead(
    icp, routing, tmp_path
) -> None:
    class SlackCountingProvider(MockNotificationProvider):
        provider_name = "slack"

    provider = SlackCountingProvider()
    with session_for(tmp_path) as session:
        _, _, lead = process_lead(
            session,
            payload(external_id="slack-once-per-lead"),
            icp,
            routing,
            notification_provider=provider,
        )
        replayed = process_lead(
            session,
            payload(external_id="slack-once-per-lead"),
            icp,
            routing,
            notification_provider=provider,
        )[2]

        from lead_agent.application.routing import route_and_notify

        rerouted = route_and_notify(
            session,
            lead,
            routing,
            notification_provider=provider,
            force=True,
            reroute_reason="Verify Slack deduplication",
        )
        notifications = list(
            session.scalars(
                select(NotificationORM)
                .join(RoutingDecisionORM)
                .where(
                    RoutingDecisionORM.lead_id == lead.id,
                    NotificationORM.provider == "slack",
                )
            )
        )

        assert replayed.id == lead.id
        assert rerouted.notification_state == "suppressed"
        assert sum(notification.status == "sent" for notification in notifications) == 1
        assert sum(notification.status == "suppressed" for notification in notifications) == 1
        assert len(provider.sent) == 1


def test_pipeline_history_records_complete_ordered_lifecycle(icp, routing, tmp_path) -> None:
    with session_for(tmp_path) as session:
        _, _, lead = process_lead(session, payload(external_id="history"), icp, routing)
        events = list(
            session.scalars(select(PipelineEventORM).where(PipelineEventORM.lead_id == lead.id))
        )
        assert [event.stage for event in events] == [
            "received",
            "normalized",
            "analyzed",
            "qualified",
            "routed",
            "notified",
        ]


def test_equal_precedence_conflicts_use_specificity_then_rule_name(icp, routing, tmp_path) -> None:
    conflicting = routing.model_copy(
        update={
            "rules": [
                RoutingRuleConfig(
                    name="zeta-rule",
                    precedence=10,
                    qualification_bands=["hot"],
                    queue="standard_rep",
                    reason="Later lexical rule",
                ),
                RoutingRuleConfig(
                    name="alpha-rule",
                    precedence=10,
                    qualification_bands=["hot"],
                    geographies=["united states"],
                    queue="senior_rep",
                    reason="More specific rule",
                ),
                RoutingRuleConfig(
                    name="beta-rule",
                    precedence=10,
                    qualification_bands=["hot"],
                    geographies=["united states"],
                    queue="standard_rep",
                    reason="Same specificity rule",
                ),
            ]
        }
    )
    with session_for(tmp_path) as session:
        _, _, lead = process_lead(session, payload(external_id="rule-conflict"), icp, conflicting)
        decision = get_current_routing(session, lead.id)
        assert decision is not None
        assert decision.assigned_queue == "senior_rep"
        assert decision.matched_rules == ["alpha-rule", "beta-rule", "zeta-rule"]


def test_terminal_bands_cannot_be_overridden_into_sales_assignment(icp, routing, tmp_path) -> None:
    terminal_override = RoutingRuleConfig(
        name="unsafe-terminal-sales-rule",
        precedence=0,
        qualification_bands=["nurture", "disqualify"],
        queue="standard_rep",
        reason="This rule must not override terminal band safety.",
    )
    protected = routing.model_copy(update={"rules": [terminal_override]})
    cases = [
        payload(
            external_id="protected-nurture",
            job_title=None,
            employee_count=None,
            geography="Unknown",
            industry="Professional Services",
            budget=None,
            message="Just curious about what you do. Maybe someday.",
        ),
        payload(
            external_id="protected-disqualify",
            job_title=None,
            employee_count=None,
            geography="Unknown",
            industry="Unknown",
            budget=None,
            product_or_service="General information",
            message="Hello there.",
        ),
    ]
    for index, lead_payload in enumerate(cases):
        with session_for(tmp_path / str(index)) as session:
            _, result, lead = process_lead(session, lead_payload, icp, protected)
            decision = get_current_routing(session, lead.id)
            assert result.classification in {"nurture", "disqualify"}
            assert decision is not None
            assert decision.assigned_queue in {"nurture", "archive"}
            assert decision.assigned_rep_id is None


def test_provider_failure_routes_to_manual_review_before_sales_rules(
    icp, routing, tmp_path
) -> None:
    class FailingReasoningProvider:
        provider_name = "failing-test"
        model_name = "unavailable"
        prompt_version = "test-v1"

        def analyze(self, lead, icp):
            raise TimeoutError("provider unavailable")

    provider_failure_review_routing = routing.model_copy(
        update={
            "manual_review": routing.manual_review.model_copy(
                update={"provider_failure": True}
            )
        }
    )
    with session_for(tmp_path) as session:
        _, _, lead = process_lead(
            session,
            payload(external_id="provider-failure-route"),
            icp,
            provider_failure_review_routing,
            FailingReasoningProvider(),
        )
        decision = get_current_routing(session, lead.id)
        assert decision is not None
        assert decision.manual_review is True
        assert decision.assigned_queue == "manual_review"
        assert decision.assigned_rep_id is None
        assert decision.matched_rules == ["manual_review_precedence"]


def test_notification_pending_state_is_committed_before_delivery(icp, routing, tmp_path) -> None:
    with session_for(tmp_path) as session:

        class InspectingProvider:
            provider_name = "inspect-pending"

            def send(self, message):
                with Session(session.get_bind()) as observer:
                    notification = observer.scalar(select(NotificationORM))
                    decision = observer.scalar(select(RoutingDecisionORM))
                    assert notification is not None
                    assert decision is not None
                    assert notification.status == "pending"
                    assert decision.notification_state == "pending"
                return NotificationDelivery(state="sent", external_id="observed")

        process_lead(
            session,
            payload(external_id="pending-before-send"),
            icp,
            routing,
            notification_provider=InspectingProvider(),
        )


def test_retry_reuses_notification_and_does_not_redeliver_sent_message(
    icp, routing, tmp_path
) -> None:
    provider = MockNotificationProvider()
    with session_for(tmp_path) as session:
        _, _, lead = process_lead(
            session,
            payload(external_id="notification-retry"),
            icp,
            routing,
            notification_provider=provider,
        )
        first = session.scalar(select(NotificationORM))
        assert first is not None
        retry = retry_current_notification(session, lead, provider)
        assert retry.id == first.id
        assert retry.attempts == 1
        assert len(provider.sent) == 1
        assert session.scalar(select(func.count()).select_from(NotificationORM)) == 1


def test_failed_provider_retry_reuses_one_notification_record(icp, routing, tmp_path) -> None:
    class RaisingProvider:
        provider_name = "recoverable-provider"

        def send(self, message):
            raise RuntimeError("temporary provider outage")

    class RecoveredProvider:
        provider_name = "recoverable-provider"

        def send(self, message):
            return NotificationDelivery(state="sent", external_id="recovered-message")

    with session_for(tmp_path) as session:
        _, _, lead = process_lead(
            session,
            payload(external_id="recoverable-notification"),
            icp,
            routing,
            notification_provider=RaisingProvider(),
        )
        failed = session.scalar(select(NotificationORM))
        decision = get_current_routing(session, lead.id)
        assert failed is not None
        assert decision is not None
        assert failed.status == "retryable"
        assert decision.notification_state == "retryable"

        recovered = retry_current_notification(session, lead, RecoveredProvider())
        assert recovered.id == failed.id
        assert recovered.status == "sent"
        assert recovered.attempts == 2
        assert session.scalar(select(func.count()).select_from(NotificationORM)) == 1
        assert session.scalar(select(func.count()).select_from(RoutingDecisionORM)) == 1


def test_rep_assignment_is_reproducible_with_equally_ranked_candidates(
    icp, routing, tmp_path
) -> None:
    tied_reps = [
        RepConfig(
            id="rep-zeta",
            name="Zeta Rep",
            queue="senior_rep",
            geographies=["United States"],
            industries=["SaaS"],
            services=["Lead routing automation"],
            priority=10,
        ),
        RepConfig(
            id="rep-alpha",
            name="Alpha Rep",
            queue="senior_rep",
            geographies=["United States"],
            industries=["SaaS"],
            services=["Lead routing automation"],
            priority=10,
        ),
    ]
    deterministic = routing.model_copy(update={"reps": tied_reps})
    selected_ids = []
    with session_for(tmp_path) as session:
        for suffix in ("one", "two"):
            _, _, lead = process_lead(
                session,
                payload(external_id=f"deterministic-{suffix}"),
                icp,
                deterministic,
            )
            decision = get_current_routing(session, lead.id)
            assert decision is not None
            selected_ids.append(decision.assigned_rep_id)
    assert selected_ids == ["rep-alpha", "rep-alpha"]


def test_action_plan_does_not_claim_unknown_lead_facts(icp, routing, tmp_path) -> None:
    with session_for(tmp_path) as session:
        _, _, lead = process_lead(
            session,
            payload(
                external_id="grounded-action",
                job_title=None,
                industry=None,
                employee_count=None,
                geography=None,
                budget=None,
                message="Please send general information.",
            ),
            icp,
            routing,
        )
        decision = get_current_routing(session, lead.id)
        assert decision is not None
        assert decision.action_plan["known_pain_point"] == "Unknown"
        assert decision.action_plan["urgency_timeline"] == "Unknown"
        assert not any(
            point.startswith("Address the stated pain:")
            for point in decision.action_plan["talking_points"]
        )
