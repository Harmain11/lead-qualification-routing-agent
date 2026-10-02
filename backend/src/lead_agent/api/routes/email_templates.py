from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission
from lead_agent.application.email_templates import DEFAULT_EMAIL_TEMPLATES, get_email_template
from lead_agent.domain.follow_up import FollowUpEmailContent
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import WorkspaceEmailTemplateORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.reasoning.factory import resolve_workspace_reasoning_provider

router = APIRouter(prefix="/v1/settings/email-templates", tags=["email templates"])
TemplateKind = Literal["confirmation", "follow_up"]

class EmailTemplateEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subject: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=10, max_length=5000)
    generation_method: Literal["manual", "ai"] = "manual"

    @field_validator("subject", "body")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("Email template content cannot be blank")
        return value


def _response(kind: str, row: WorkspaceEmailTemplateORM | None) -> dict[str, str]:
    if row is None:
        return {"kind": kind, **DEFAULT_EMAIL_TEMPLATES[kind], "generation_method": "manual"}
    return {"kind": kind, "subject": row.subject, "body": row.body, "generation_method": row.generation_method}


def _load(session: Session, workspace_id, kind: str) -> WorkspaceEmailTemplateORM | None:
    return session.scalar(
        select(WorkspaceEmailTemplateORM).where(
            WorkspaceEmailTemplateORM.workspace_id == workspace_id,
            WorkspaceEmailTemplateORM.kind == kind,
        )
    )


@router.get("")
def get_email_templates(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
) -> list[dict[str, str]]:
    return [get_email_template(session, workspace.workspace_id, kind) for kind in ("confirmation", "follow_up")]


@router.put("/{kind}")
def save_email_template(
    kind: TemplateKind,
    request: EmailTemplateEdit,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
) -> dict[str, str]:
    row = _load(session, workspace.workspace_id, kind)
    now = datetime.now(UTC)
    if row is None:
        row = WorkspaceEmailTemplateORM(
            id=uuid4(),
            workspace_id=workspace.workspace_id,
            kind=kind,
            subject=request.subject,
            body=request.body,
            generation_method=request.generation_method,
            updated_by=workspace.actor_id[:255],
            updated_at=now,
        )
        session.add(row)
    else:
        row.subject = request.subject
        row.body = request.body
        row.generation_method = request.generation_method
        row.updated_by = workspace.actor_id[:255]
        row.updated_at = now
    session.commit()
    session.refresh(row)
    return _response(kind, row)


@router.post("/{kind}/generate")
def generate_email_template(
    kind: TemplateKind,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
) -> dict[str, str]:
    provider = resolve_workspace_reasoning_provider(session, workspace.workspace_id)
    draft = getattr(provider, "draft_follow_up", None)
    if not callable(draft):
        raise HTTPException(status_code=409, detail="Configure an AI drafting provider to generate a template.")
    facts = {
        "lead_name": "there",
        "company": "{{company_name}}",
        "job_title": "{{job_title}}",
        "requested_product_or_service": "{{product_or_service}}",
        "original_message": (
            "Create a reusable workspace email template. Preserve the placeholders "
            "{{lead_name}}, {{company_name}}, {{product_or_service}}, and {{job_title}}. "
            + (
                "This is an acknowledgment of a newly received inquiry. Confirm receipt, "
                "do not claim a person reviewed it, and do not add a sales pitch."
                if kind == "confirmation"
                else "This is a personal sales follow-up that asks one relevant next-step question."
            )
        ),
        "qualification": "lead qualification",
        "qualification_score": 0,
        "next_step_recommendation": "Use only the provided merge fields; invent no facts.",
        "conversion_goal": "{{product_or_service}}",
    }
    try:
        content = FollowUpEmailContent.model_validate(draft(facts))
    except Exception as error:
        raise HTTPException(status_code=502, detail="AI could not generate an email template. Try again.") from error
    subject = content.subject.replace("lead workflow automation", "{{product_or_service}}")
    body = content.body.replace("Hi there,", "Hi {{lead_name}},").replace(
        "your company", "{{company_name}}"
    )
    return {"kind": kind, "subject": subject, "body": body, "generation_method": "ai"}
