from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from lead_agent.api.schemas import outcome_response, routing_response
from lead_agent.application.field_privacy import assert_field_writes, filter_sensitive_mapping
from lead_agent.domain.rbac import role_permissions
from lead_agent.domain.workspaces import WorkspaceContext


def _context(role: str) -> WorkspaceContext:
    return WorkspaceContext(
        workspace_id=uuid4(),
        role=role,
        permissions=role_permissions(role),
    )


def test_owner_can_edit_sensitive_communications_but_viewer_cannot_edit_contact_fields():
    assert_field_writes(_context("owner"), ["message"])
    with pytest.raises(HTTPException, match="fields.contact.edit"):
        assert_field_writes(_context("viewer"), ["email"])


def test_financial_outcome_values_are_redacted_without_financial_field_access():
    row = SimpleNamespace(
        pipeline_stage="won",
        conversion_type="sale",
        opportunity_value=12500,
        send_direct_message=False,
        credited_user_id=None,
        credited_user=None,
        closed_revenue=10000,
        currency="USD",
        closed_at=None,
        win_notifications=[],
    )
    redacted = outcome_response(row, include_financial=False)
    assert redacted is not None
    assert redacted.opportunity_value is None
    assert redacted.closed_revenue is None


def test_routing_evidence_is_redacted_without_communications_access():
    row = SimpleNamespace(
        id=uuid4(),
        lead_id=uuid4(),
        qualification_id=uuid4(),
        qualification_band="qualified",
        assigned_queue="sales",
        assigned_rep_id=None,
        assigned_rep_name=None,
        routing_reason="High fit",
        matched_rules=[],
        action_plan={
            "immediate_action": "reply",
            "response_priority": "high",
            "talking_points": ["pricing"],
            "unanswered_questions": [],
            "known_pain_point": "manual work",
            "urgency_timeline": "this week",
            "potential_objections": [],
            "next_step_recommendation": "meeting",
            "supporting_evidence": ["private message excerpt"],
            "follow_up_strategy": [],
            "conversion_goal": "book meeting",
        },
        priority="high",
        sla_target_at=None,
        notification_state="pending",
        manual_review=False,
        created_at=datetime.now(UTC),
        notifications=[],
    )
    redacted = routing_response(row, include_sensitive_evidence=False)
    assert redacted is not None
    assert redacted.action_plan.supporting_evidence == []


def test_sensitive_mapping_filters_contact_financial_and_call_content():
    payload = {
        "id": "safe",
        "email": "private@example.test",
        "budget": 25000,
        "transcript": "private call",
        "recording_url": "https://recording.invalid/private",
        "analysis": {"summary": "private"},
    }

    assert filter_sensitive_mapping(payload, frozenset()) == {"id": "safe"}
    assert filter_sensitive_mapping(
        payload,
        frozenset(
            {
                "fields.contact.view",
                "fields.financial.view",
                "calls.transcript.view",
            }
        ),
    ) == {
        "id": "safe",
        "email": "private@example.test",
        "budget": 25000,
        "transcript": "private call",
    }


def test_sensitive_mapping_recursively_filters_nested_export_content():
    payload = {
        "id": "safe",
        "details": {
            "status": "completed",
            "email": "private@example.test",
            "nested": [
                {
                    "body": "private message",
                    "budget": 25000,
                    "result": "safe result",
                }
            ],
        },
        "output": {
            "transcript": "private call",
            "recording_url": "https://recording.invalid/private",
            "status": "safe",
        },
        "error_message": "private provider response",
    }

    assert filter_sensitive_mapping(payload, frozenset()) == {
        "id": "safe",
        "details": {
            "status": "completed",
            "nested": [{"result": "safe result"}],
        },
        "output": {"status": "safe"},
    }
