from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission
from lead_agent.domain.analysis import ReasoningProviderError
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import PipelineEventORM
from lead_agent.infrastructure.db.operations import LeadNoteORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.reasoning.factory import resolve_workspace_reasoning_provider
from lead_agent.infrastructure.repositories import get_lead

router = APIRouter(prefix="/v1/leads", tags=["lead assistant"])


class AssistantMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=3000)


class AssistantChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[AssistantMessage] = Field(min_length=1, max_length=16)


@router.post("/{lead_id}/assistant/chat")
def chat_with_lead_assistant(
    lead_id: UUID,
    request: AssistantChatRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.execute")),
) -> dict[str, object]:
    lead = get_lead(session, lead_id, workspace.workspace_id)
    if lead is None:
        raise HTTPException(404, "Lead not found")

    provider = resolve_workspace_reasoning_provider(session, workspace.workspace_id)
    assistant_method = getattr(provider, "assist_with_lead", None)
    if not callable(assistant_method):
        raise HTTPException(
            503,
            "Connect and verify a workspace AI provider before using the lead assistant.",
        )

    events = list(
        session.scalars(
            select(PipelineEventORM)
            .where(
                PipelineEventORM.lead_id == lead_id,
                PipelineEventORM.workspace_id == workspace.workspace_id,
            )
            .order_by(PipelineEventORM.created_at.desc())
            .limit(20)
        )
    )
    notes = list(
        session.scalars(
            select(LeadNoteORM)
            .where(
                LeadNoteORM.lead_id == lead_id,
                LeadNoteORM.workspace_id == workspace.workspace_id,
            )
            .order_by(LeadNoteORM.created_at.desc())
            .limit(30)
        )
    )
    payload = {
        "lead": {
            "id": str(lead.id),
            "full_name": lead.full_name,
            "email": lead.email,
            "phone": lead.phone,
            "job_title": lead.job_title,
            "company_name": lead.company_name,
            "industry": lead.industry,
            "employee_count": lead.employee_count,
            "annual_revenue": (
                float(lead.annual_revenue)
                if lead.annual_revenue is not None
                else None
            ),
            "geography": lead.geography,
            "budget": float(lead.budget) if lead.budget is not None else None,
            "product_or_service": lead.product_or_service,
            "original_inquiry_or_context": lead.message,
            "source": lead.source,
            "received_at": lead.received_at.isoformat(),
            "qualification": (
                {
                    "score": lead.qualification.total_score,
                    "classification": lead.qualification.classification,
                    "rationale": lead.qualification.rationale,
                }
                if lead.qualification
                else None
            ),
            "outcome": (
                {
                    "pipeline_stage": lead.outcome.pipeline_stage,
                    "conversion_type": lead.outcome.conversion_type,
                    "closed_revenue": (
                        float(lead.outcome.closed_revenue)
                        if lead.outcome.closed_revenue is not None
                        else None
                    ),
                    "credited_name": lead.outcome.credited_name,
                }
                if lead.outcome
                else None
            ),
        },
        "recent_history": [
            {
                "stage": event.stage,
                "status": event.status,
                "created_at": event.created_at.isoformat(),
                "details": event.details,
            }
            for event in reversed(events)
        ],
        "team_notes": [
            {
                "body": note.body,
                "updated_at": note.updated_at.isoformat(),
            }
            for note in reversed(notes)
        ],
        "messages": [message.model_dump() for message in request.messages],
    }
    try:
        result = assistant_method(payload)
    except ReasoningProviderError as error:
        raise HTTPException(502, str(error)) from None
    except Exception:
        raise HTTPException(502, "The lead assistant could not complete this request.") from None
    return result.model_dump(mode="json")
