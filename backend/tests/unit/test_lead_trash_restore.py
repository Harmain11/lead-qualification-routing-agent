from __future__ import annotations

from uuid import uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from lead_agent.api.routes.leads import _delete_lead, restore_lead_from_trash
from lead_agent.application.services import process_lead
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import LeadORM, LeadSuppressionORM, PipelineEventORM
from lead_agent.infrastructure.repositories import ensure_workspace


def test_restoring_trashed_lead_returns_same_complete_record_immediately(
    tmp_path, icp, routing
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lead-trash-restore.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id = uuid4()
    ensure_workspace(session, workspace_id, name="Trash Restore Workspace")

    _, _, lead = process_lead(
        session,
        {
            "external_id": "trash-restore-source-record",
            "source": "gmail",
            "source_detail": "inbound email",
            "full_name": "Restore Test Lead",
            "email": "restore@example.test",
            "company_name": "Restore Test Co",
            "product_or_service": "Lead workflow",
            "message": "Please arrange a call next week to discuss pricing.",
        },
        icp,
        routing,
        workspace_id=workspace_id,
    )
    session.commit()
    lead_id = lead.id
    original_score = lead.qualification.total_score
    original_analysis_id = lead.analysis.id
    original_event_count = session.scalar(
        select(func.count(PipelineEventORM.id)).where(PipelineEventORM.lead_id == lead_id)
    )

    workspace = WorkspaceContext(
        workspace_id=workspace_id,
        actor_id="trash-manager",
        permissions=frozenset({"leads.view", "leads.edit"}),
    )
    try:
        _delete_lead(session, lead, actor_id=workspace.actor_id)
        session.commit()
        suppression = session.scalar(
            select(LeadSuppressionORM).where(
                LeadSuppressionORM.workspace_id == workspace_id
            )
        )
        assert suppression is not None
        session.expunge_all()
        assert session.get(LeadORM, lead_id) is None

        restored = restore_lead_from_trash(suppression.id, session, workspace)
        session.expunge_all()
        restored_lead = session.get(LeadORM, lead_id)

        assert restored["status"] == "restored"
        assert restored_lead is not None
        assert restored_lead.email == "restore@example.test"
        assert restored_lead.qualification.total_score == original_score
        assert restored_lead.analysis.id == original_analysis_id
        assert session.scalar(
            select(func.count(PipelineEventORM.id)).where(PipelineEventORM.lead_id == lead_id)
        ) == original_event_count + 1
        restore_event = session.scalar(
            select(PipelineEventORM).where(
                PipelineEventORM.lead_id == lead_id,
                PipelineEventORM.stage == "trash",
                PipelineEventORM.status == "restored",
            )
        )
        assert restore_event is not None
        assert restore_event.details["actor_id"] == workspace.actor_id
        assert session.scalar(
            select(func.count(LeadSuppressionORM.id)).where(
                LeadSuppressionORM.workspace_id == workspace_id
            )
        ) == 0
    finally:
        session.close()
        engine.dispose()
