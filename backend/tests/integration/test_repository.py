from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from lead_agent.application.services import process_lead
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import LeadORM, SourceEventORM


def test_repeated_external_submission_returns_existing_lead(tmp_path, icp, routing) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'duplicate.db'}")
    Base.metadata.create_all(engine)
    payload = {
        "external_id": "repeated-001",
        "source": "website",
        "source_detail": "test-form",
        "full_name": "Repeat User",
        "email": "repeat@example.com",
        "company_name": "Acme",
        "industry": "SaaS",
        "employee_count": 50,
        "geography": "United States",
        "product_or_service": "Lead routing",
        "message": "Please share a workflow demo.",
    }
    with Session(engine) as session:
        first = process_lead(session, payload, icp, routing)[2]
        second = process_lead(session, payload, icp, routing)[2]

        assert first.id == second.id
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 1
        assert session.scalar(select(func.count()).select_from(SourceEventORM)) == 1
