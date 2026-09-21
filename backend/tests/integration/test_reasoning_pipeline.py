from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from lead_agent.application.services import process_source_event
from lead_agent.domain.analysis import ReasoningProviderTimeout
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import AnalysisRunORM, SourceEventORM
from lead_agent.infrastructure.intake.adapters import EmailAdapter


class UnavailableProvider:
    provider_name = "unavailable"
    model_name = "offline"
    prompt_version = "test-v1"

    def analyze(self, lead, icp):
        raise ReasoningProviderTimeout("provider unavailable")


def test_provider_outage_persists_lead_analysis_and_manual_review(icp, routing, tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'provider-failure.db'}")
    Base.metadata.create_all(engine)
    event = EmailAdapter().adapt(
        {
            "external_message_id": "provider-failure-001",
            "sender": "buyer@example.com",
            "subject": "Implementation inquiry",
            "body": "We need a workflow implementation quote this month.",
        }
    )

    with Session(engine) as session:
        result = process_source_event(session, event, icp, routing, provider=UnavailableProvider())
        source = session.scalar(select(SourceEventORM))
        analysis = session.scalar(select(AnalysisRunORM))

        assert result.lead.id is not None
        assert result.status == "manual_review"
        assert source is not None and source.error_message
        assert analysis is not None
        assert analysis.status == "provider_error"
        assert analysis.requires_manual_review is True
        assert analysis.structured_output["summary"]["qualification_result"]
