from __future__ import annotations

from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.api.routes.leads import complete_lead_manual_review
from lead_agent.application.services import process_lead
from lead_agent.domain.analysis import (
    AnalyzedField,
    EvidenceReference,
    IntentScoreAssessment,
    IntentScoring,
    LeadContextAnalysis,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.notifications.mock import MockNotificationProvider
from lead_agent.infrastructure.repositories import ensure_workspace, get_current_routing


def test_completing_manual_review_rescores_saved_ai_intent(tmp_path, icp, routing) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'manual-review-rescore.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id = uuid4()
    ensure_workspace(session, workspace_id, name="Review Rescore Workspace")

    _, _, lead = process_lead(
        session,
        {
            "external_id": "manual-review-rescore-test",
            "source": "gmail",
            "full_name": "Harmain Construction",
            "email": "harmain-construction@example.test",
            "company_name": "Harmain Construction",
            "product_or_service": "Construction workflow",
            "message": "Looking forward to hopping on a 10-minute Zoom call sometime next week.",
        },
        icp,
        routing,
        workspace_id=workspace_id,
    )
    current = get_current_routing(session, lead.id, workspace_id)
    assert current is not None
    assert lead.analysis is not None
    assert lead.qualification is not None

    stored = LeadContextAnalysis.model_validate(lead.analysis.structured_output)
    stored = stored.model_copy(
        update={
            "provider": "openai",
            "status": "manual_review",
            "requires_manual_review": True,
            "overall_confidence": 0.55,
            "intent_level": "medium",
            "context": stored.context.model_copy(
                update={
                    "urgency": AnalyzedField(
                        value="high", information_kind="inferred", confidence=0.94
                    ),
                    "explicit_timeline": AnalyzedField(
                        value="next week", information_kind="explicit", confidence=1
                    ),
                    "buying_stage": AnalyzedField(
                        value="meeting requested", information_kind="inferred", confidence=0.93
                    ),
                    "specific_request": AnalyzedField(
                        value="10-minute Zoom call next week",
                        information_kind="explicit",
                        confidence=1,
                    ),
                }
            ),
            "intent_scoring": IntentScoring(
                urgency=IntentScoreAssessment(
                    points=15,
                    rationale="The prospect proposed an imminent meeting.",
                    evidence=[
                        EvidenceReference(
                            source_field="message",
                            excerpt="Looking forward to hopping on a 10-minute Zoom call sometime next week.",
                        )
                    ],
                ),
                buying_stage=IntentScoreAssessment(
                    points=10,
                    rationale="The prospect is actively scheduling the next conversation.",
                    evidence=[
                        EvidenceReference(
                            source_field="message",
                            excerpt="Looking forward to hopping on a 10-minute Zoom call sometime next week.",
                        )
                    ],
                ),
                budget_signal=IntentScoreAssessment(
                    points=5,
                    rationale="The saved analysis includes a qualified budget signal.",
                    evidence=[
                        EvidenceReference(
                            source_field="message",
                            excerpt="The saved AI analysis assessed budget intent.",
                        )
                    ],
                ),
                specific_request=IntentScoreAssessment(
                    points=5,
                    rationale="A specific call is requested.",
                    evidence=[
                        EvidenceReference(
                            source_field="message",
                            excerpt="10-minute Zoom call next week.",
                        )
                    ],
                ),
            ),
        }
    )
    lead.analysis.provider = "openai"
    lead.analysis.status = "manual_review"
    lead.analysis.requires_manual_review = True
    lead.analysis.structured_output = stored.model_dump(mode="json")
    lead.qualification.total_score = 42
    lead.qualification.component_scores = {
        **lead.qualification.component_scores,
        "intent_urgency": 0,
    }
    current.manual_review = True
    current.assigned_queue = routing.manual_review_queue
    session.commit()

    workspace = WorkspaceContext(
        workspace_id=workspace_id,
        actor_id="review-manager",
        permissions=frozenset({"leads.view", "manual_review.manage"}),
    )
    try:
        response = complete_lead_manual_review(
            lead.id,
            session=session,
            policies=(icp, routing),
            notification_provider=MockNotificationProvider(),
            workspace=workspace,
        )

        assert response.analysis is not None
        assert response.analysis.status == "completed"
        assert response.qualification is not None
        assert response.qualification.total_score > 42
        assert response.qualification.component_scores["intent_urgency"] > 0
    finally:
        session.close()
        engine.dispose()
