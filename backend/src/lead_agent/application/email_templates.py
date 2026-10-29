from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.domain.operations import WorkspaceBusinessProfile
from lead_agent.infrastructure.db.models import LeadORM, WorkspaceEmailTemplateORM

_SIGNOFF_LINE = re.compile(
    r"^\s*(?:best(?: regards)?|kind regards|warm regards|regards|sincerely|"
    r"thanks|thank you)[,!:]?\s*$",
    re.IGNORECASE,
)
_PLACEHOLDER_LINE = re.compile(
    r"^\s*(?:\[(?:your\s+)?(?:name|title|company(?:\s+name)?|email|phone)\]|"
    r"\{\{\s*(?:sender|sender_name|your_name|signature)\s*\}\})\s*$",
    re.IGNORECASE,
)
_GENERIC_SENDER_LINE = re.compile(
    r"^\s*(?:the team|your team|sales team|the sales team)\s*$",
    re.IGNORECASE,
)

DEFAULT_EMAIL_TEMPLATES: dict[str, dict[str, str]] = {
    "confirmation": {
        "subject": "We received your inquiry",
        "body": (
            "Hi {{lead_name}},\n\nThanks for reaching out about {{product_or_service}}. "
            "We have received your inquiry and a member of our team will review it.\n\n"
            "Best regards,"
        ),
    },
    "follow_up": {
        "subject": "Following up on {{product_or_service}}",
        "body": (
            "Hi {{lead_name}},\n\nThanks for reaching out to us about "
            "{{product_or_service}}. I read your note and would be glad to learn more about "
            "what {{company_name}} is looking for.\n\nWould a brief conversation about your "
            "needs be useful?\n\nBest regards,"
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


def lead_signoff(lead: LeadORM) -> str:
    """Sign as the assigned rep; with no owner, close without inventing a sender."""
    current = [item for item in getattr(lead, "routing_decisions", []) or [] if item.is_current]
    route = max(current, key=lambda item: item.created_at, default=None)
    name = (getattr(route, "assigned_rep_name", None) or "").strip()
    return f"Best regards,\n{name}" if name else "Best regards,"


def apply_workspace_email_signature(
    body: str,
    profile: WorkspaceBusinessProfile,
    *,
    fallback_business_name: str = "",
) -> str:
    """Remove generated/template sender placeholders and append workspace identity."""
    lines = [
        line
        for line in body.splitlines()
        if not _PLACEHOLDER_LINE.fullmatch(line)
    ]

    # Remove a model-generated sign-off block so it cannot conflict with the
    # workspace's configured sender signature.
    for index in range(len(lines) - 1, -1, -1):
        if not _SIGNOFF_LINE.fullmatch(lines[index]):
            continue
        suffix = [line.strip() for line in lines[index + 1 :] if line.strip()]
        if all(
            _PLACEHOLDER_LINE.fullmatch(line)
            or _GENERIC_SENDER_LINE.fullmatch(line)
            or (len(line) <= 160 and not re.search(r"[.!?]", line))
            for line in suffix
        ):
            lines = lines[:index]
            break

    while lines and (
        not lines[-1].strip()
        or _GENERIC_SENDER_LINE.fullmatch(lines[-1])
        or _PLACEHOLDER_LINE.fullmatch(lines[-1])
    ):
        lines.pop()

    clean_body = "\n".join(lines).strip()
    signature = profile.email_signature.strip()
    if any(_PLACEHOLDER_LINE.fullmatch(line) for line in signature.splitlines()):
        signature = ""
    if not signature:
        business_name = profile.business_name.strip() or fallback_business_name.strip()
        if business_name:
            signature = f"Best regards,\n{business_name}"
    if not signature:
        return clean_body
    return f"{clean_body}\n\n{signature}" if clean_body else signature


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
