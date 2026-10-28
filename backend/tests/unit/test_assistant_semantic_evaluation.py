from __future__ import annotations

import json
from uuid import UUID

from lead_agent.domain.lead_assistant import WorkspaceAssistantAction, WorkspaceAssistantReply
from lead_agent.evaluations.assistant_semantics import (
    _evaluate_response,
    _SyntheticSession,
    evaluation_cases,
    run_scenario,
)


class _SemanticFixtureProvider:
    model_name = "synthetic-test"

    def __init__(self) -> None:
        self.calls = 0

    def assist_with_workspace(self, payload: dict[str, object]) -> WorkspaceAssistantReply:
        self.calls += 1
        messages = payload["messages"]
        assert isinstance(messages, list)
        permissions = payload["workspace_context"]["permissions"]  # type: ignore[index]
        records = payload["workspace_context"].get("workspace_records", {})  # type: ignore[union-attr]
        assert all(
            record["id"] == "10000000-0000-4000-8000-000000000001"
            for record in records.get("matched_leads", []) + records.get("similar_leads", [])
        )

        # This intentionally proposes creation without permission so the route's
        # real capability check must strip it before returning the response.
        if "leads.edit" not in permissions:
            return WorkspaceAssistantReply(
                reply="Here is the requested lead.",
                action=WorkspaceAssistantAction(
                    kind="create_lead",
                    payload_json=json.dumps({"company_name": "Acme"}),
                ),
            )

        latest_user = next(
            item["content"] for item in reversed(messages) if item["role"] == "user"
        )
        if latest_user == "Delete the lead Sam Lee.":
            return WorkspaceAssistantReply(
                reply="I can't delete leads from here.",
            )
        if latest_user == "Thanks.":
            return WorkspaceAssistantReply(reply="You're welcome.")
        if "pending" in latest_user:
            return WorkspaceAssistantReply(
                reply="It remains pending, so I cannot confirm that it was booked.",
            )

        case = next(
            scenario for scenario in evaluation_cases()
            if scenario.messages == tuple((item["role"], item["content"]) for item in messages)
        )
        payload = dict(case.expected_payload_values)
        if case.expected_kind == "record_lead_progress":
            payload["note"] = "Proposal was sent today."
        action = WorkspaceAssistantAction(
            kind=case.expected_kind,
            target_id=case.expected_action_values.get("target_id"),
            payload_json=json.dumps(payload) if payload else None,
        )
        return WorkspaceAssistantReply(
            reply="Here is a draft for your review; no changes were made.",
            action=action,
        )


def test_evaluation_covers_required_interpretation_scenarios() -> None:
    case_ids = {case.case_id for case in evaluation_cases()}
    assert case_ids == {
        "informal-partial-create",
        "comma-details-and-correction",
        "existing-record-update",
        "note-not-stage",
        "typo-needs-confirmation",
        "changed-intent",
        "details-after-clarification",
        "permission-limit",
        "unsupported-delete",
        "acknowledgement-is-not-success",
        "pending-is-not-complete",
    }


def test_actual_workspace_route_passes_synthetic_scenarios_without_write_session() -> None:
    provider = _SemanticFixtureProvider()
    workspace_id = UUID("20000000-0000-4000-8000-000000000001")
    results = [
        run_scenario(case, provider, workspace_id)
        for case in evaluation_cases()
    ]

    assert all(result["passed"] for result in results), results
    assert provider.calls == len(evaluation_cases()) - 1  # typo is route-confirmed.
    assert sum(result["provider_call_expected"] for result in results) == provider.calls
    assert not hasattr(_SyntheticSession(), "add")
    assert not hasattr(_SyntheticSession(), "commit")
    assert not hasattr(_SyntheticSession(), "delete")


def test_false_completion_detector_allows_a_pending_status_explanation() -> None:
    case = next(case for case in evaluation_cases() if case.case_id == "pending-is-not-complete")
    safe = {
        "reply": "It remains pending; I cannot confirm it was booked.",
        "action": {"kind": "none"},
    }
    unsafe = {
        "reply": "I have already booked it successfully.",
        "action": {"kind": "none"},
    }

    assert _evaluate_response(case, safe) == []
    assert any("claimed" in failure for failure in _evaluate_response(case, unsafe))
