from __future__ import annotations

from dataclasses import replace

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from lead_agent.application.services import process_source_event
import lead_agent.application.services as services
from lead_agent.domain.intake import SourceEvent
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import LeadORM, SourceEventORM
from lead_agent.infrastructure.intake.adapters import EmailAdapter


def test_replaying_an_event_returns_the_existing_lead(icp, routing, tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'intake.db'}")
    Base.metadata.create_all(engine)
    event = EmailAdapter().adapt(
        {
            "external_message_id": "mail-replay-001",
            "sender": "buyer@example.com",
            "subject": "Workflow inquiry",
            "body": "Please send a demo of the workflow.",
        }
    )

    with Session(engine) as session:
        first = process_source_event(session, event, icp, routing)
        second = process_source_event(session, event, icp, routing)

        assert first.lead.id == second.lead.id
        assert second.duplicate is True
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 1
        assert session.scalar(select(func.count()).select_from(SourceEventORM)) == 1


def test_same_person_with_two_different_inquiries_creates_two_leads(icp, routing, tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'distinct.db'}")
    Base.metadata.create_all(engine)
    adapter = EmailAdapter()
    first = adapter.adapt(
        {
            "external_message_id": "mail-distinct-001",
            "sender": "buyer@example.com",
            "subject": "First inquiry",
            "body": "We need a workflow audit.",
        }
    )
    second = adapter.adapt(
        {
            "external_message_id": "mail-distinct-002",
            "sender": "buyer@example.com",
            "subject": "Second inquiry",
            "body": "We need an implementation quote.",
        }
    )

    with Session(engine) as session:
        process_source_event(session, first, icp, routing)
        process_source_event(session, second, icp, routing)
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 2


def test_failed_processing_is_recorded_and_can_be_retried(icp, routing, tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'retry.db'}")
    Base.metadata.create_all(engine)
    failed_event = SourceEvent.create(
        channel="email",
        external_id="mail-retry-001",
        raw_payload={"body": "bad"},
        normalized_payload={"source": "email"},
    )

    with Session(engine) as session:
        try:
            process_source_event(session, failed_event, icp, routing)
        except ValueError:
            pass
        else:
            raise AssertionError("expected normalization failure")

        failed = session.scalar(
            select(SourceEventORM).where(SourceEventORM.external_event_id == "mail-retry-001")
        )
        assert failed is not None
        assert failed.status == "failed"
        assert failed.processing_attempts == 1
        assert failed.error_message

        fixed_event = replace(
            failed_event,
            normalized_payload={
                "source": "email",
                "source_detail": "retry-fixture",
                "external_id": "mail-retry-001",
                "full_name": "Retry Buyer",
                "email": "buyer@example.com",
                "company_name": "Example Co",
                "product_or_service": "Lead routing",
                "message": "Please send an implementation quote.",
            },
        )
        result = process_source_event(session, fixed_event, icp, routing)
        assert result.status == "processed"
        assert result.duplicate is False
        retried = session.get(SourceEventORM, failed.id)
        assert retried is not None
        assert retried.processing_attempts == 2


def test_email_delivery_failure_does_not_fail_committed_intake(
    icp, routing, tmp_path, monkeypatch
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'email-failure.db'}")
    Base.metadata.create_all(engine)
    event = EmailAdapter().adapt(
        {
            "external_message_id": "mail-email-failure-001",
            "sender": "buyer@example.com",
            "subject": "Delivery resilience",
            "body": "Please send a demo of the workflow.",
        }
    )

    def fail_delivery(*_args, **_kwargs):
        raise RuntimeError("simulated Gmail ledger lock")

    monkeypatch.setattr(services, "_deliver_lead_confirmation_email", fail_delivery)

    with Session(engine) as session:
        result = process_source_event(session, event, icp, routing)
        persisted = session.scalar(
            select(SourceEventORM).where(
                SourceEventORM.external_event_id == "mail-email-failure-001"
            )
        )

        assert result.status == "processed"
        assert persisted is not None
        assert persisted.status == "processed"
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 1
