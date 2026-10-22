from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.application.services import process_lead, reprocess_lead
from lead_agent.domain.analysis import ReasoningOutput
from lead_agent.domain.configuration import IcpConfig
from lead_agent.domain.models import NormalizedLead
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.operations import LeadNoteORM
from lead_agent.infrastructure.reasoning.deterministic import DeterministicReasoningProvider


class CountingProvider:
    provider_name = "openai"
    model_name = "cache-test-model"
    prompt_version = "cache-test-v1"

    def __init__(self) -> None:
        self.calls = 0
        self.seen_messages: list[str] = []
        self.delegate = DeterministicReasoningProvider()

    def analyze(
        self,
        lead: NormalizedLead,
        icp: IcpConfig,
        business_profile=None,
    ) -> ReasoningOutput:
        self.calls += 1
        self.seen_messages.append(lead.message)
        return self.delegate.analyze(lead, icp, business_profile)


def _payload() -> dict[str, object]:
    return {
        "source": "gmail",
        "source_detail": "Gmail",
        "full_name": "Mira Example",
        "email": "mira@example.test",
        "phone": "+1-202-555-0161",
        "job_title": "Operations Director",
        "company_name": "Example Company",
        "industry": "Technology",
        "employee_count": 25,
        "annual_revenue": 1_000_000,
        "geography": "United States",
        "budget": 25_000,
        "product_or_service": "Operations software",
        "message": "We are evaluating operations software for our team.",
        "received_at": "2026-10-22T09:00:00+00:00",
        "raw_payload": {
            "gmail_thread_id": "thread-cache-test",
            "gmail_thread_messages": [
                {
                    "message_id": "message-cache-test",
                    "body": "We are evaluating operations software for our team.",
                }
            ],
        },
    }


def test_reasoning_cache_reuses_only_matching_workspace_input_and_configuration(
    tmp_path, icp, routing
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'reasoning-cache.db'}")
    Base.metadata.create_all(engine)
    provider = CountingProvider()
    workspace_id = uuid4()
    payload = _payload()

    with Session(engine) as session:
        _, _, lead = process_lead(
            session,
            payload,
            icp,
            routing,
            provider=provider,
            workspace_id=workspace_id,
        )
        assert lead.analysis is not None
        assert lead.analysis.analysis_request_fingerprint
        assert "analysis_request_fingerprint" not in lead.analysis.structured_output
        assert provider.calls == 1

        # Exact repeat reuses the validated provider output.
        reprocess_lead(
            session, lead, deepcopy(payload), icp, routing, provider=provider
        )
        assert provider.calls == 1

        # A changed message or thread invalidates the result; the new exact
        # input can then be reused on its next processing pass.
        changed_thread = deepcopy(payload)
        changed_thread["raw_payload"] = {
            "gmail_thread_id": "thread-cache-test",
            "gmail_thread_messages": [
                {
                    "message_id": "message-cache-test",
                    "body": "We are also evaluating a different workflow.",
                }
            ],
        }
        reprocess_lead(
            session, lead, changed_thread, icp, routing, provider=provider
        )
        assert provider.calls == 2
        reprocess_lead(
            session, lead, deepcopy(changed_thread), icp, routing, provider=provider
        )
        assert provider.calls == 2

        # Model, prompt, and qualification-policy changes cannot reuse the old
        # provider result.
        provider.model_name = "cache-test-model-v2"
        reprocess_lead(
            session, lead, deepcopy(changed_thread), icp, routing, provider=provider
        )
        assert provider.calls == 3

        provider.prompt_version = "cache-test-v2"
        reprocess_lead(
            session, lead, deepcopy(changed_thread), icp, routing, provider=provider
        )
        assert provider.calls == 4

        changed_routing = routing.model_copy(
            update={"policy_version": f"{routing.policy_version}-changed"}
        )
        reprocess_lead(
            session,
            lead,
            deepcopy(changed_thread),
            icp,
            changed_routing,
            provider=provider,
        )
        assert provider.calls == 5

        changed_icp = icp.model_copy(
            update={"profile_version": f"{icp.profile_version}-changed"}
        )
        reprocess_lead(
            session,
            lead,
            deepcopy(changed_thread),
            changed_icp,
            changed_routing,
            provider=provider,
        )
        assert provider.calls == 6

        # The same content in another workspace is not a cache hit.
        process_lead(
            session,
            deepcopy(changed_thread),
            changed_icp,
            changed_routing,
            provider=provider,
            workspace_id=uuid4(),
        )
        assert provider.calls == 7

    engine.dispose()


def test_saved_team_notes_are_analysis_input_and_invalidate_cached_reasoning(
    tmp_path, icp, routing
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'reasoning-notes.db'}")
    Base.metadata.create_all(engine)
    provider = CountingProvider()
    workspace_id = uuid4()
    payload = _payload()

    with Session(engine) as session:
        _, _, lead = process_lead(
            session,
            payload,
            icp,
            routing,
            provider=provider,
            workspace_id=workspace_id,
        )
        assert provider.calls == 1
        assert "Team notes saved on this lead" not in provider.seen_messages[-1]

        session.add(
            LeadNoteORM(
                workspace_id=workspace_id,
                lead_id=lead.id,
                body="They asked to revisit the project budget next month.",
                author_id="qa-user",
                updated_by="qa-user",
            )
        )
        session.commit()

        reprocess_lead(
            session, lead, deepcopy(payload), icp, routing, provider=provider
        )
        assert provider.calls == 2
        assert "revisit the project budget next month" in provider.seen_messages[-1]
        assert lead.message == payload["message"]

        reprocess_lead(
            session, lead, deepcopy(payload), icp, routing, provider=provider
        )
        assert provider.calls == 2

    engine.dispose()
