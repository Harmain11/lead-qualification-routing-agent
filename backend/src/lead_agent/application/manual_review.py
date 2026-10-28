from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from lead_agent.domain.analysis import manual_review_reasons
from lead_agent.domain.configuration import ManualReviewConfig, ManualReviewSettings
from lead_agent.infrastructure.db.models import (
    LeadORM,
    RoutingDecisionORM,
    WorkspaceRoutingConfigORM,
)


def workspace_manual_review_config(
    session: Session,
    workspace_id: UUID,
    base: ManualReviewConfig | None = None,
) -> ManualReviewConfig:
    row = session.get(WorkspaceRoutingConfigORM, workspace_id)
    defaults = base or ManualReviewConfig()
    if row is None or not row.manual_review_settings:
        return defaults
    stored = ManualReviewSettings.model_validate(row.manual_review_settings)
    return defaults.model_copy(update=stored.model_dump())


def workspace_manual_review_settings(
    session: Session, workspace_id: UUID
) -> ManualReviewSettings:
    row = session.get(WorkspaceRoutingConfigORM, workspace_id)
    return ManualReviewSettings.model_validate(
        row.manual_review_settings if row is not None else {}
    )


def lead_manual_review_reasons(
    session: Session,
    lead: LeadORM,
    routing: RoutingDecisionORM | None = None,
) -> list[str]:
    analysis = lead.analysis
    is_held = bool(
        (analysis is not None and analysis.requires_manual_review)
        or (routing is not None and routing.manual_review)
    )
    if not is_held:
        return []

    settings = workspace_manual_review_config(
        session, lead.workspace_id, ManualReviewConfig()
    )
    structured = analysis.structured_output if analysis else {}
    reasons = manual_review_reasons(
        email=lead.email,
        full_name=lead.full_name,
        company_name=lead.company_name,
        settings=settings,
        overall_confidence=float(analysis.confidence) if analysis else None,
        contradictions=structured.get("contradictions", []),
        unknown_fields=structured.get("unknown_fields", []),
        provider_failure=bool(analysis and analysis.status == "provider_error"),
    )
    if routing and routing.manual_review:
        route_reason = routing.routing_reason.replace(
            " No rep assignment is applicable.", ""
        ).strip()
        generic_reasons = (
            "Lead matches an enabled manual-review condition.",
            "Manual-review precedence: analysis uncertainty or provider failure requires human review.",
        )
        if (
            route_reason
            and not any(route_reason.startswith(reason) for reason in generic_reasons)
            and route_reason not in reasons
        ):
            reasons.append(route_reason)

    if not reasons:
        evidence = [
            f"Email: {lead.email or 'not provided'}",
            f"Full name: {lead.full_name or 'not provided'}",
            f"Company name: {lead.company_name or 'not provided'}",
        ]
        if analysis:
            evidence.append(f"Analysis confidence: {float(analysis.confidence):.0%}")
            contradictions = structured.get("contradictions", [])
            unknown_fields = structured.get("unknown_fields", [])
            if contradictions:
                evidence.append("Contradictions: " + "; ".join(contradictions))
            if unknown_fields:
                evidence.append("Missing qualification fields: " + ", ".join(unknown_fields))
        reasons.append(
            "This lead remains on a human-review hold, but no currently enabled rule "
            "identifies a specific trigger. Current evidence: " + "; ".join(evidence) + "."
        )
    return list(dict.fromkeys(reasons))
