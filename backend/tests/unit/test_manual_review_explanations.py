from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from lead_agent.application.manual_review import lead_manual_review_reasons
from lead_agent.domain.analysis import manual_review_reasons
from lead_agent.domain.configuration import ManualReviewConfig


class EmptySession:
    def get(self, *_args, **_kwargs):
        return None


def test_manual_review_reasons_name_triggering_fields_and_values() -> None:
    reasons = manual_review_reasons(
        email="priya@gmail.com",
        full_name="Unknown",
        company_name="Unknown",
        settings=ManualReviewConfig(
            personal_email=True,
            missing_person_name=True,
            missing_company_name=True,
            low_confidence=True,
            minimum_confidence=0.75,
            contradictory_evidence=True,
            missing_qualification_information=True,
            provider_failure=True,
        ),
        overall_confidence=0.42,
        contradictions=["Budget is pending, but the request says budget is approved."],
        unknown_fields=["budget", "purchase timeline"],
        provider_failure=True,
    )

    explanation = " ".join(reasons)
    for detail in (
        "Email field",
        "priya@gmail.com",
        "Full name field",
        "Company name field",
        "Unknown",
        "Analysis confidence (42%)",
        "workspace threshold (75%)",
        "Contradictory evidence",
        "purchase timeline",
        "Analysis provider:",
    ):
        assert detail in explanation


def test_existing_review_hold_explains_current_fields_when_no_rule_matches() -> None:
    lead = SimpleNamespace(
        workspace_id=uuid4(),
        email="priya@acme.example",
        full_name="Priya Mehta",
        company_name="Acme",
        analysis=SimpleNamespace(
            requires_manual_review=True,
            status="manual_review",
            confidence=0.88,
            structured_output={},
        ),
    )

    reasons = lead_manual_review_reasons(EmptySession(), lead)

    assert len(reasons) == 1
    assert "no currently enabled rule identifies a specific trigger" in reasons[0]
    assert "Full name: Priya Mehta" in reasons[0]
    assert "Company name: Acme" in reasons[0]
    assert "Analysis confidence: 88%" in reasons[0]


def test_routing_reason_does_not_repeat_review_trigger_but_keeps_extra_context(monkeypatch) -> None:
    trigger = "Email field (person@gmail.com) uses a personal email domain; verify the prospect's business affiliation."
    monkeypatch.setattr("lead_agent.application.manual_review.manual_review_reasons", lambda **_: [trigger])
    lead = SimpleNamespace(workspace_id=uuid4(), email="person@gmail.com", full_name="Person", company_name="Company", analysis=SimpleNamespace(requires_manual_review=True, status="manual_review", confidence=0.8, structured_output={}))
    routing = SimpleNamespace(manual_review=True, routing_reason=f"{trigger} Manual representative assignment preserved.")

    reasons = lead_manual_review_reasons(EmptySession(), lead, routing)

    assert reasons == [trigger, "Manual representative assignment preserved."]


def test_completed_review_does_not_reappear_from_stale_analysis() -> None:
    lead = SimpleNamespace(
        manual_review_completed_at=datetime.now(UTC),
        analysis=SimpleNamespace(requires_manual_review=True),
    )
    routing = SimpleNamespace(manual_review=True)

    assert lead_manual_review_reasons(EmptySession(), lead, routing) == []
