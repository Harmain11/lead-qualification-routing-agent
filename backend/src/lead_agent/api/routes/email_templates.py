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


class EmailTemplateGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subject: str | None = Field(default=None, min_length=1, max_length=180)
    body: str | None = Field(default=None, min_length=10, max_length=5000)


def _response(kind: str, row: WorkspaceEmailTemplateORM | None) -> dict[str, str]:
    if row is None:
        return {"kind": kind, **DEFAULT_EMAIL_TEMPLATES[kind], "generation_method": "manual"}
    return {
        "kind": kind,
        "subject": row.subject,
        "body": row.body,
        "generation_method": row.generation_method,
    }


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
    return [
        get_email_template(session, workspace.workspace_id, kind)
        for kind in ("confirmation", "follow_up")
    ]


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
    request: EmailTemplateGenerationRequest | None = None,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
) -> dict[str, str]:
    provider = resolve_workspace_reasoning_provider(session, workspace.workspace_id)
    draft = getattr(provider, "draft_follow_up", None)
    if not callable(draft):
        raise HTTPException(
            status_code=409,
            detail="Configure an AI drafting provider to generate a template.",
        )
    current = _response(kind, _load(session, workspace.workspace_id, kind))
    sample_subject = (
        request.subject
        if request is not None and request.subject is not None
        else current["subject"]
    )
    sample_body = (
        request.body if request is not None and request.body is not None else current["body"]
    )
    supported_placeholders = (
        "{{lead_name}}",
        "{{company_name}}",
        "{{product_or_service}}",
        "{{job_title}}",
    )
    required_placeholders = [
        placeholder
        for placeholder in supported_placeholders
        if placeholder in f"{sample_subject}\n{sample_body}"
    ]
    facts = {
        "template_generation": True,
        "email_purpose": kind,
        "style_reference": {"subject": sample_subject, "body": sample_body},
        "required_placeholders": required_placeholders,
        "lead_name": "{{lead_name}}",
        "company": "{{company_name}}",
        "job_title": "{{job_title}}",
        "requested_product_or_service": "{{product_or_service}}",
        "original_message": "Reusable workspace template; no individual lead message is available.",
        "qualification": "not applicable",
        "qualification_score": 0,
        "next_step_recommendation": "Use only the provided merge fields; invent no facts.",
        "conversion_goal": "{{product_or_service}}",
    }
    try:
        content = FollowUpEmailContent.model_validate(draft(facts))
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail="AI could not generate an email template. Try again.",
        ) from error
    generated_text = f"{content.subject}\n{content.body}"
    if any(placeholder not in generated_text for placeholder in required_placeholders):
        raise HTTPException(
            status_code=502,
            detail="AI did not preserve all merge fields from the sample. Try again.",
        )
    if " ".join(content.body.split()).casefold() == " ".join(sample_body.split()).casefold():
        raise HTTPException(
            status_code=502,
            detail="AI returned the sample unchanged. Try generating it again.",
        )
    return {
        "kind": kind,
        "subject": content.subject,
        "body": content.body,
        "generation_method": "ai",
    }
