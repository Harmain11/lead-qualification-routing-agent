from datetime import UTC, datetime
from uuid import uuid4

import pytest

from lead_agent.domain.analysis import (
    EvidenceReference,
    IntentScoreAssessment,
    IntentScoring,
    LeadContextAnalysis,
)
from lead_agent.domain.models import NormalizedLead, RecordedLeadEvidence
from lead_agent.domain.normalization import normalize_lead
from lead_agent.domain.scoring import qualify_lead
from lead_agent.infrastructure.reasoning.deterministic import DeterministicReasoningProvider


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


def ai_analysis(
    lead: NormalizedLead,
    icp,
    *,
    urgency: int = 0,
    buying_stage: int = 0,
    budget_signal: int = 0,
    specific_request: int = 0,
) -> LeadContextAnalysis:
    def assessment(points: int, label: str) -> IntentScoreAssessment:
        return IntentScoreAssessment(
            points=points,
            rationale=f"AI assessment for {label}.",
            evidence=(
                [EvidenceReference(source_field="message", excerpt="Prospect's supplied message.")]
                if points
                else []
            ),
        )

    output = DeterministicReasoningProvider().analyze(lead, icp)
    values = output.model_dump()
    values.update(
        provider="openai",
        model="test-model",
        prompt_version="test-v1",
        intent_scoring=IntentScoring(
            urgency=assessment(urgency, "urgency"),
            buying_stage=assessment(buying_stage, "buying stage"),
            budget_signal=assessment(budget_signal, "budget signal"),
            specific_request=assessment(specific_request, "specific request"),
        ),
    )
    return LeadContextAnalysis(**values)


def test_full_fit_lead_is_hot(icp, routing) -> None:
    lead = make_lead()
    result = qualify_lead(
        lead,
        icp,
        routing,
        ai_analysis(lead, icp, urgency=15, buying_stage=10, budget_signal=5, specific_request=5),
    )

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

    for index, (lead, expected) in enumerate(cases):
        scores = (
            {"urgency": 15, "buying_stage": 10, "budget_signal": 5, "specific_request": 5}
            if index == 0
            else {"buying_stage": 5, "specific_request": 5}
            if index == 1
            else {"buying_stage": 5, "specific_request": 5}
            if index == 2
            else {}
        )
        assert qualify_lead(
            lead, icp, routing, ai_analysis(lead, icp, **scores)
        ).classification == expected


def test_ai_intent_assessment_can_raise_or_lower_qualification(icp, routing) -> None:
    weak_message = "I am gathering information about your service."
    baseline_lead = make_lead(message=weak_message, budget=None)
    baseline = qualify_lead(
        baseline_lead, icp, routing, ai_analysis(baseline_lead, icp)
    )
    meeting_update = make_lead(
        message=weak_message,
        budget=None,
        recorded_evidence=(
            RecordedLeadEvidence(
                source="manual_progress:meeting_scheduled",
                text="Sales stage meeting_scheduled: A meeting was confirmed.",
                observed_at=datetime.now(UTC),
            ),
        ),
    )
    raised = qualify_lead(
        meeting_update,
        icp,
        routing,
        ai_analysis(meeting_update, icp, urgency=15, buying_stage=10, specific_request=5),
    )
    assert raised.total_score > baseline.total_score

    cooled = make_lead(
        recorded_evidence=(
            RecordedLeadEvidence(
                source="team_note",
                text="Prospect is no longer interested and declined the proposal.",
                observed_at=datetime.now(UTC),
            ),
        ),
        original_message=make_lead().message,
    )
    lowered = qualify_lead(cooled, icp, routing, ai_analysis(cooled, icp))
    assert lowered.total_score < raised.total_score


def test_ai_meeting_assessment_counts_interest_without_inventing_a_sale(icp, routing) -> None:
    baseline_lead = make_lead(message="Tell me more.", budget=None)
    baseline = qualify_lead(
        baseline_lead, icp, routing, ai_analysis(baseline_lead, icp)
    )
    request_lead = make_lead(message="Can we do it at 2 pm tomorrow?", budget=None)
    requested = qualify_lead(
        request_lead,
        icp,
        routing,
        ai_analysis(request_lead, icp, urgency=15, buying_stage=5, specific_request=5),
    )
    assert requested.total_score > baseline.total_score
    assert next(line for line in requested.score_breakdown if line.key == "buying_stage").points > 0
    assert next(line for line in requested.score_breakdown if line.key == "urgency").points > 0

    date_only_lead = make_lead(message="Tomorrow at 2 pm is our internal meeting.", budget=None)
    date_only = qualify_lead(
        date_only_lead,
        icp,
        routing,
        ai_analysis(date_only_lead, icp),
    )
    assert next(line for line in date_only.score_breakdown if line.key == "buying_stage").points == 0


@pytest.mark.parametrize(
    "message",
    [
        "Can we meet this Friday at 2 pm?",
        "How does next Tuesday at 10:30 am sound for a call?",
        "Would November 6 at 11 am work to discuss the proposal?",
    ],
)
def test_concrete_timed_meeting_requests_are_urgent_beyond_tomorrow(icp, routing, message: str) -> None:
    lead = make_lead(message=message, budget=None)
    result = qualify_lead(
        lead,
        icp,
        routing,
        ai_analysis(lead, icp, urgency=15, buying_stage=5, specific_request=5),
    )

    assert next(line for line in result.score_breakdown if line.key == "urgency").points == 15


def test_intent_points_are_not_inferred_without_ai_analysis(icp, routing) -> None:
    result = qualify_lead(
        make_lead(message="Can we meet tomorrow at 2 pm?", budget=30000), icp, routing
    )
    assert result.component_scores["intent_urgency"] == 0


def test_team_note_length_does_not_inflate_message_detail_score(icp, routing) -> None:
    original = "Please send information about your service."
    note = "Routine follow-up note. " * 80
    baseline = qualify_lead(make_lead(message=original), icp, routing)
    with_note = qualify_lead(
        make_lead(
            message=f"{original}\n\nTeam notes:\n{note}",
            original_message=original,
            recorded_evidence=(
                RecordedLeadEvidence(
                    source="team_note",
                    text=note,
                    observed_at=datetime.now(UTC),
                ),
            ),
        ),
        icp,
        routing,
    )
    baseline_detail = next(
        line for line in baseline.score_breakdown if line.key == "message_detail"
    )
    note_detail = next(line for line in with_note.score_breakdown if line.key == "message_detail")
    assert note_detail.points == baseline_detail.points


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


def test_unconfigured_icp_is_skipped_and_remaining_score_is_normalized(icp, routing) -> None:
    lead = make_lead(
        job_title="Student",
        product_or_service="School project",
        message="I need help with homework for a school project.",
    )

    result = qualify_lead(lead, icp, routing, include_icp=False)

    assert result.hard_disqualified is False
    assert "icp_fit" not in result.component_scores
    assert {line.component for line in result.score_breakdown} == {
        "intent_urgency",
        "engagement_quality",
    }
    assert sum(line.max_points for line in result.score_breakdown) == 60
    assert result.total_score == round(
        sum(line.points for line in result.score_breakdown) * 100 / 60
    )
    assert any("no published custom ICP" in reason for reason in result.rationale)


def test_missing_manual_details_do_not_count_as_contact_completeness(icp, routing) -> None:
    result = qualify_lead(
        make_lead(
            full_name="Name not provided",
            email="",
            phone=None,
            job_title=None,
            company_name="Company not provided",
        ),
        icp,
        routing,
    )

    completeness = next(
        line for line in result.score_breakdown if line.key == "contact_completeness"
    )
    assert completeness.points == 0


def test_incomplete_manual_lead_is_not_hard_disqualified_for_missing_fields(
    icp, routing
) -> None:
    lead = normalize_lead(
        {
            "source_detail": "manual-control-center",
            "full_name": "Kathi Sitek",
        },
        uuid4(),
    )

    result = qualify_lead(lead, icp, routing)

    assert result.hard_disqualified is False
    assert result.disqualifier_code is None
