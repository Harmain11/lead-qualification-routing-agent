from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.application.workspace_retrieval import build_workspace_record_context
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.calling import CallRecordORM
from lead_agent.infrastructure.db.models import LeadORM, PipelineEventORM
from lead_agent.infrastructure.db.operations import (
    ContactORM,
    LeadContactORM,
    LeadNoteORM,
)
from lead_agent.infrastructure.repositories import ensure_workspace


def _lead(workspace_id, name: str, company: str, lead_number: int) -> LeadORM:
    now = datetime.now(UTC)
    return LeadORM(
        id=uuid4(),
        workspace_id=workspace_id,
        lead_number=lead_number,
        source="website",
        source_detail=None,
        external_id=None,
        full_name=name,
        email=f"{name.lower().replace(' ', '.')}@example.test",
        phone=None,
        job_title="Operations Director",
        company_name=company,
        industry="Software",
        employee_count=None,
        annual_revenue=None,
        geography=None,
        budget=None,
        product_or_service="workflow automation",
        message="We need a pricing proposal for workflow automation.",
        received_at=now,
        raw_payload={},
        normalization_version="test-v1",
        created_at=now,
        updated_at=now,
    )


def test_workspace_retrieval_returns_related_records_and_stays_workspace_scoped(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'workspace-records.db'}")
    Base.metadata.create_all(engine)
    workspace_id, other_workspace_id = uuid4(), uuid4()
    now = datetime.now(UTC)

    with Session(engine) as session:
        ensure_workspace(session, workspace_id, name="Record Search QA")
        ensure_workspace(session, other_workspace_id, name="Other Workspace")
        lead = _lead(workspace_id, "Mina Chen", "Lunar Robotics", 1)
        other_lead = _lead(other_workspace_id, "Mina Chen", "Other Robotics", 1)
        contact = ContactORM(
            id=uuid4(),
            workspace_id=workspace_id,
            company_id=None,
            name="Mina Chen",
            email=lead.email,
            phone="+15551234567",
            job_title="Operations Director",
            timezone="UTC",
            merged_into_id=None,
            created_at=now,
            updated_at=now,
        )
        session.add_all([lead, other_lead, contact])
        session.flush()
        session.add_all(
            [
                LeadContactORM(lead_id=lead.id, contact_id=contact.id),
                LeadNoteORM(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    lead_id=lead.id,
                    body="Mina requested pricing details after the demo.",
                    version=1,
                    author_id="qa-user",
                    updated_by="qa-user",
                    created_at=now,
                    updated_at=now,
                ),
                PipelineEventORM(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    lead_id=lead.id,
                    stage="gmail_message",
                    status="inbound",
                    details={
                        "subject": "Workflow automation pricing",
                        "body": "Please send the pricing proposal we discussed.",
                        "sender": lead.email,
                        "direction": "inbound",
                    },
                    created_at=now,
                ),
                PipelineEventORM(
                    id=uuid4(),
                    workspace_id=other_workspace_id,
                    lead_id=other_lead.id,
                    stage="gmail_message",
                    status="inbound",
                    details={
                        "subject": "Private other workspace message",
                        "body": "Mina pricing details.",
                    },
                    created_at=now,
                ),
                CallRecordORM(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    lead_id=lead.id,
                    connection_id=uuid4(),
                    provider="cloudtalk",
                    external_id=None,
                    request_id=None,
                    request_fingerprint=None,
                    status="completed",
                    direction="outbound",
                    customer_phone="+15551234567",
                    started_at=now,
                    duration_seconds=240,
                    transcript="Mina asked for a pricing proposal after the demo.",
                    recording_url=None,
                    insights={"summary": "Discussed pricing and workflow automation."},
                    message="Discussed workflow automation pricing.",
                    actor_id="qa-user",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        session.commit()

        context = build_workspace_record_context(
            session,
            workspace_id,
            "Find Mina Chen's CloudTalk call and pricing email.",
        )

    assert context["record_counts"]["leads"] == 1
    assert [lead["name"] for lead in context["matched_leads"]] == ["Mina Chen"]
    assert [contact["name"] for contact in context["matched_contacts"]] == ["Mina Chen"]
    assert any("pricing proposal" in (call["transcript_excerpt"] or "") for call in context["calls"])
    assert any(
        event["details"].get("subject") == "Workflow automation pricing"
        for event in context["recent_emails_and_meetings"]
    )
    assert any("requested pricing" in (note["body"] or "") for note in context["lead_notes"])
    assert all(
        event["details"].get("subject") != "Private other workspace message"
        for event in context["recent_emails_and_meetings"]
    )
    engine.dispose()
