from datetime import UTC, datetime
from uuid import uuid4

from lead_agent.domain.models import NormalizedLead
from lead_agent.domain.scoring import qualify_lead


def make_lead(**overrides: object) -> NormalizedLead:
    values: dict[str, object] = {
        "id": uuid4(),
        "source": "website",
        "source_detail": "web-form",
        "external_id": None,
        "full_name": "Maya Thompson",
        "email": "maya@example.com",
        "phone": "+1 555 010 0000",
        "job_title": "VP of Operations",
        "company_name": "Northstar SaaS",
        "industry": "SaaS",
        "employee_count": 180,
        "annual_revenue": None,
        "geography": "United States",
        "budget": 30000,
        "product_or_service": "Lead routing automation",
        "message": (
            "We are ready to implement lead routing automation this month. We need a proposal, "
            "workflow integration, and a demo this week because slow response is causing missed "
            "leads. Our budget is $30,000. Please include the implementation scope, timeline, "
            "reporting workflow, ownership model, and a clear handoff from marketing to sales."
        ),
        "received_at": datetime.now(UTC),
        "raw_payload": {},
    }
    values.update(overrides)
    return NormalizedLead(**values)  # type: ignore[arg-type]


def test_full_fit_lead_is_hot(icp, routing) -> None:
    result = qualify_lead(make_lead(), icp, routing)

    assert result.total_score == 100
    assert result.classification == "hot"
    assert result.routing_queue == "senior_rep"
    assert result.component_scores == {
        "icp_fit": 40,
        "intent_urgency": 35,
        "engagement_quality": 25,
    }
    assert len(result.rationale) == len(result.score_breakdown)
    assert all(" - " in reason for reason in result.rationale)


def test_score_bands_cover_realistic_cases(icp, routing) -> None:
    cases = [
        (make_lead(), "hot"),
        (
            make_lead(
                job_title="Marketing Director",
                message="We want to evaluate pricing for inbound lead qualification next quarter.",
                budget=15000,
            ),
            "warm",
        ),
        (
            make_lead(
                industry="Retail",
                geography="United Arab Emirates",
                job_title="Marketing Specialist",
                budget=None,
                message=(
                    "We need help with a manual process and missed inquiries. Could you explain "
                    "an audit and workflow?"
                ),
            ),
            "needs_qualifying",
        ),
        (
            make_lead(
                industry="Professional Services",
                geography="Unknown",
                job_title=None,
                phone=None,
                employee_count=None,
                budget=None,
                message="Just curious about what you do. Maybe someday.",
            ),
            "nurture",
        ),
    ]

    for lead, expected in cases:
        assert qualify_lead(lead, icp, routing).classification == expected


def test_hard_disqualifier_short_circuits_scoring(icp, routing) -> None:
    result = qualify_lead(
        make_lead(
            job_title="Student",
            product_or_service="School project",
            message="I need help with homework for a school project.",
        ),
        icp,
        routing,
    )

    assert result.hard_disqualified is True
    assert result.total_score == 0
    assert result.classification == "disqualify"
    assert result.disqualifier_code == "student_or_hobbyist"
    assert result.component_scores == {"icp_fit": 0, "intent_urgency": 0, "engagement_quality": 0}
    assert len(result.score_breakdown) == 3
    assert all("hard disqualifier" in reason.lower() for reason in result.rationale[1:4])
