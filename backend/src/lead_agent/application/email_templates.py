from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.infrastructure.db.models import LeadORM, WorkspaceEmailTemplateORM

DEFAULT_EMAIL_TEMPLATES: dict[str, dict[str, str]] = {
    "confirmation": {
        "subject": "We received your inquiry",
        "body": (
            "Hi {{lead_name}},\n\nThanks for reaching out about {{product_or_service}}. "
            "We have received your inquiry and a member of our team will review it.\n\n"
            "Best,\nThe team"
        ),
    },
    "follow_up": {
        "subject": "Following up on {{product_or_service}}",
        "body": (
            "Hi {{lead_name}},\n\nThanks for reaching out to us about "
            "{{product_or_service}}. I read your note and would be glad to learn more about "
            "what {{company_name}} is looking for.\n\nWould a brief conversation about your "
            "needs be useful?\n\nBest,\nThe team"
        ),
    },
}


def get_email_template(
    session: Session, workspace_id, kind: str
) -> dict[str, str]:
    row = session.scalar(
        select(WorkspaceEmailTemplateORM).where(
            WorkspaceEmailTemplateORM.workspace_id == workspace_id,
            WorkspaceEmailTemplateORM.kind == kind,
        )
    )
    if row is None:
        return {"kind": kind, **DEFAULT_EMAIL_TEMPLATES[kind], "generation_method": "manual"}
    return {
        "kind": kind,
        "subject": row.subject,
        "body": row.body,
        "generation_method": row.generation_method,
    }


def render_email_template(template: dict[str, str], lead: LeadORM) -> dict[str, str]:
    replacements = {
        "lead_name": lead.full_name.strip().split()[0] if lead.full_name.strip() else "there",
        "company_name": lead.company_name,
        "product_or_service": lead.product_or_service,
        "job_title": lead.job_title or "",
    }

    def render(value: str) -> str:
        for key, replacement in replacements.items():
            value = value.replace("{{" + key + "}}", replacement)
        return value

    return {**template, "subject": render(template["subject"]), "body": render(template["body"])}
