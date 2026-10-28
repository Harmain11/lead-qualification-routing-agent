from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.application.services import process_lead
from lead_agent.domain.workspaces import DEMO_WORKSPACE_ID
from lead_agent.infrastructure.db.models import LeadOutcomeORM
from lead_agent.infrastructure.db.session import build_session_factory
from lead_agent.infrastructure.repositories import ensure_workspace
from lead_agent.settings.config import get_settings

SEED_LEADS: list[dict[str, object]] = [
    {
        "external_id": "seed-hot-001",
        "source": "website",
        "source_detail": "demo-web-form",
        "full_name": "Maya Thompson",
        "email": "maya.thompson@example.test",
        "phone": "+1 555 010 1001",
        "job_title": "VP of Operations",
        "company_name": "Northstar SaaS",
        "industry": "SaaS",
        "employee_count": 180,
        "geography": "United States",
        "budget": 30000,
        "product_or_service": "Lead routing automation",
        "message": (
            "We are ready to implement lead routing automation this month. We need a proposal, "
            "Salesforce workflow integration, and a demo this week because slow response is "
            "causing missed leads. Our budget is $30,000. Please include the implementation "
            "scope, timeline, reporting workflow, ownership model, and a clear handoff from "
            "marketing to sales."
        ),
    },
    {
        "external_id": "seed-warm-001",
        "source": "website",
        "source_detail": "demo-web-form",
        "full_name": "Oliver Grant",
        "email": "oliver.grant@example.test",
        "job_title": "Director of Marketing",
        "company_name": "Brightline Services",
        "industry": "Professional Services",
        "employee_count": 90,
        "geography": "United Kingdom",
        "budget": 15000,
        "product_or_service": "Inbound lead qualification",
        "message": (
            "We have a growing volume of inbound inquiries and want to understand pricing and "
            "options. Please send an overview; we may evaluate this next quarter."
        ),
    },
    {
        "external_id": "seed-needs-qualifying-001",
        "source": "manual",
        "source_detail": "demo-import",
        "full_name": "Aisha Khan",
        "email": "aisha.khan@example.test",
        "job_title": "Marketing Specialist",
        "company_name": "Harbor Retail",
        "industry": "Retail",
        "employee_count": 25,
        "geography": "United Arab Emirates",
        "product_or_service": "Lead management",
        "message": (
            "We need help with a manual process and missed inquiries. Could you explain how an "
            "audit and workflow might work? Our team is researching options."
        ),
    },
    {
        "external_id": "seed-nurture-001",
        "source": "website",
        "source_detail": "demo-web-form",
        "full_name": "Leo Martin",
        "email": "leo.martin@example.test",
        "company_name": "Small Idea Studio",
        "industry": "Professional Services",
        "geography": "Unknown",
        "product_or_service": "General advice",
        "message": "Just curious about what you do. Maybe someday.",
    },
    {
        "external_id": "seed-disqualified-001",
        "source": "website",
        "source_detail": "demo-web-form",
        "full_name": "Sam Student",
        "email": "sam.student@example.test",
        "job_title": "Student",
        "company_name": "University Project",
        "industry": "Education",
        "product_or_service": "School project",
        "message": "I need help with a school project and homework about lead scoring.",
    },
]

DEMO_PROFILES: list[dict[str, Any]] = [
    {
        "name": "Maya Thompson",
        "company": "Northstar Cloud",
        "source": "website",
        "title": "VP of Operations",
        "service": "Lead routing automation",
        "industry": "SaaS",
        "geography": "United States",
        "employees": 260,
        "budget": 48000,
        "message": (
            "We need Salesforce lead routing live in 30 days. I own the budget and need a "
            "technical discovery call this week. Slow response is costing qualified opportunities."
        ),
        "stage": "closed_won",
        "value": 48000,
        "revenue": 48000,
        "days_ago": 27,
    },
    {
        "name": "Theo Martins",
        "company": "Aperture Data",
        "source": "email",
        "title": "Chief Revenue Officer",
        "service": "AI lead qualification",
        "industry": "Technology",
        "geography": "United States",
        "employees": 420,
        "budget": 75000,
        "message": (
            "Our board approved the initiative. Please propose an AI qualification pilot, "
            "HubSpot integration, security requirements and a launch plan for this quarter."
        ),
        "stage": "negotiation",
        "value": 75000,
        "revenue": 0,
        "days_ago": 18,
    },
    {
        "name": "Amara Okafor",
        "company": "Helio Health",
        "source": "linkedin",
        "title": "Director of Growth",
        "service": "Inbound lead qualification",
        "industry": "Healthcare",
        "geography": "United Kingdom",
        "employees": 160,
        "budget": 24000,
        "message": (
            "We are comparing vendors for inbound qualification and want a demo next week. "
            "We need GDPR-safe workflows and clearer SDR handoffs."
        ),
        "stage": "proposal",
        "value": 24000,
        "revenue": 0,
        "days_ago": 12,
    },
    {
        "name": "Kenji Sato",
        "company": "Vector Works",
        "source": "clutch",
        "title": "Operations Director",
        "service": "Revenue operations automation",
        "industry": "Professional Services",
        "geography": "Singapore",
        "employees": 95,
        "budget": 18000,
        "message": (
            "We need pricing and an implementation outline for automating lead triage. "
            "We plan to select a partner next quarter."
        ),
        "stage": "opportunity",
        "value": 18000,
        "revenue": 0,
        "days_ago": 8,
    },
    {
        "name": "Nora Reyes",
        "company": "Fieldstone Advisory",
        "source": "website",
        "title": "Marketing Director",
        "service": "Lead management",
        "industry": "Professional Services",
        "geography": "Canada",
        "employees": 80,
        "budget": 15000,
        "message": (
            "We have missed inbound inquiries and want to review options and pricing before "
            "next quarter. Please share a relevant case study."
        ),
        "stage": "closed_won",
        "value": 15000,
        "revenue": 15000,
        "days_ago": 7,
    },
    {
        "name": "Owen Brooks",
        "company": "Common Thread Retail",
        "source": "email",
        "title": "Marketing Manager",
        "service": "Lead management",
        "industry": "Retail",
        "geography": "Australia",
        "employees": 55,
        "message": (
            "Can you explain how your workflow handles duplicate inquiries? We are researching "
            "options and have not set a timeline or budget."
        ),
        "stage": "qualified",
        "value": 0,
        "revenue": 0,
        "days_ago": 6,
    },
    {
        "name": "Priya Mehta",
        "company": "Unknown",
        "source": "linkedin",
        "title": "Revenue Operations Manager",
        "service": "CRM workflow audit",
        "industry": "Logistics",
        "geography": "United Arab Emirates",
        "employees": 130,
        "message": (
            "Our lead handoff is manual. I am collecting requirements and would like an audit "
            "outline, but budget approval is still pending."
        ),
        "stage": "qualified",
        "value": 0,
        "revenue": 0,
        "days_ago": 5,
        "manual_review": True,
    },
    {
        "name": "Elias Reed",
        "company": "Morrow Studio",
        "source": "clutch",
        "title": "Founder",
        "service": "General automation",
        "industry": "Professional Services",
        "geography": "United States",
        "employees": 9,
        "message": (
            "I am curious about automation and may explore something later this year. "
            "Please send general information."
        ),
        "stage": "nurture",
        "value": 0,
        "revenue": 0,
        "days_ago": 4,
    },
    {
        "name": "Sara Malik",
        "company": "Atlas Security",
        "source": "website",
        "title": "VP Sales",
        "service": "Technical lead routing",
        "industry": "Technology",
        "geography": "United Kingdom",
        "employees": 310,
        "budget": 52000,
        "message": (
            "We need an API-first routing platform with SSO and webhooks this month. "
            "I am the decision maker; schedule technical discovery immediately."
        ),
        "stage": "opportunity",
        "value": 52000,
        "revenue": 0,
        "days_ago": 3,
    },
    {
        "name": "Lucas Moreau",
        "company": "Vertex Media",
        "source": "email",
        "title": "Procurement Lead",
        "service": "Inbound lead qualification",
        "industry": "Media",
        "geography": "Canada",
        "employees": 145,
        "budget": 20000,
        "message": (
            "Please provide pricing, delivery timeline and references. We are shortlisting "
            "providers in the next 30 days."
        ),
        "stage": "proposal",
        "value": 20000,
        "revenue": 0,
        "days_ago": 2,
    },
    {
        "name": "Ivy Chen",
        "company": "Early Signal",
        "source": "linkedin",
        "title": "Analyst",
        "service": "Lead scoring",
        "industry": "Technology",
        "geography": "Singapore",
        "employees": 35,
        "message": (
            "I am researching lead scoring tools for a possible future project. "
            "No timeline or budget has been approved."
        ),
        "stage": "nurture",
        "value": 0,
        "revenue": 0,
        "days_ago": 1,
    },
    {
        "name": "Jamie Student",
        "company": "University Project",
        "source": "clutch",
        "title": "Student",
        "service": "School project",
        "industry": "Education",
        "geography": "United States",
        "employees": 1,
        "message": "I need free help with my homework and school project about sales lead scoring.",
        "stage": "disqualified",
        "value": 0,
        "revenue": 0,
        "days_ago": 1,
    },
]


def seed_database(session: Session) -> None:
    settings = get_settings()
    if settings.app_env == "production":
        raise RuntimeError("seed commands are disabled in production")
    icp, routing = settings.load_policies()
    for payload in SEED_LEADS:
        process_lead(session, payload, icp, routing)


def seed_demo_database(session: Session, workspace_id: UUID = DEMO_WORKSPACE_ID) -> None:
    """Seed representative demo records into the requested non-production workspace."""
    settings = get_settings()
    if settings.app_env == "production":
        raise RuntimeError("demo seed commands are disabled in production")
    icp, routing = settings.load_policies()
    workspace = ensure_workspace(
        session,
        workspace_id,
        name=(
            "Ingenuity Revenue Operations Demo"
            if workspace_id == DEMO_WORKSPACE_ID
            else "Default Workspace"
        ),
        is_demo=workspace_id == DEMO_WORKSPACE_ID,
    )
    if workspace_id == DEMO_WORKSPACE_ID:
        workspace.is_demo = True
    session.commit()
    now = datetime.now(UTC)
    for index, profile in enumerate(DEMO_PROFILES):
        external_id = f"phase6-demo-{index + 1:02d}"
        existing = session.scalar(
            select(LeadOutcomeORM)
            .join(LeadOutcomeORM.lead)
            .where(
                LeadOutcomeORM.lead.has(
                    workspace_id=workspace_id,
                    external_id=external_id,
                ),
            )
        )
        if existing is not None:
            if existing.workspace_id != workspace_id:
                existing.workspace_id = workspace_id
                session.commit()
            continue
        received_at = now - timedelta(days=int(profile["days_ago"]), hours=index % 5)
        payload: dict[str, object] = {
            "external_id": external_id,
            "source": profile["source"],
            "source_detail": "phase6-explicit-demo-data",
            "full_name": profile["name"],
            "email": f"phase6-{index + 1:02d}@example.test",
            "job_title": profile["title"],
            "company_name": profile["company"],
            "industry": profile["industry"],
            "employee_count": profile["employees"],
            "geography": profile["geography"],
            "product_or_service": profile["service"],
            "message": profile["message"],
            "received_at": received_at.isoformat(),
        }
        if profile.get("budget"):
            payload["budget"] = profile["budget"]
        _, _, row = process_lead(
            session,
            payload,
            icp,
            routing,
            workspace_id=workspace_id,
        )
        row.outcome = LeadOutcomeORM(
            id=uuid4(),
            workspace_id=workspace_id,
            lead_id=row.id,
            pipeline_stage=str(profile["stage"]),
            opportunity_value=float(profile["value"]),
            closed_revenue=float(profile["revenue"]),
            currency="USD",
            data_source="demo",
            opened_at=received_at + timedelta(hours=2),
            closed_at=(
                received_at + timedelta(days=3)
                if str(profile["stage"]).startswith("closed_")
                else None
            ),
            created_at=now,
            updated_at=now,
        )
        current_route = max(
            (route for route in row.routing_decisions if route.is_current),
            key=lambda route: route.created_at,
            default=None,
        )
        if current_route is not None:
            current_route.created_at = received_at + timedelta(minutes=9 + index * 3)
        if profile.get("manual_review") and row.analysis is not None:
            row.analysis.requires_manual_review = True
            row.analysis.status = "manual_review"
            row.analysis.error_code = "demo_low_confidence"
            row.analysis.error_message = "Important budget and authority signals need human review."
            if current_route is not None:
                current_route.manual_review = True
                current_route.assigned_queue = "manual_review"
                current_route.assigned_rep_id = None
                current_route.assigned_rep_name = None
        session.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--demo", action="store_true", help="Seed the isolated Phase 6 demo workspace"
    )
    parser.add_argument(
        "--workspace-id",
        type=UUID,
        help="Seed the illustrative records into this development workspace",
    )
    arguments = parser.parse_args()
    factory = build_session_factory()
    with factory() as session:
        if arguments.demo:
            seed_demo_database(session, arguments.workspace_id or DEMO_WORKSPACE_ID)
        else:
            seed_database(session)
    count = len(DEMO_PROFILES) if arguments.demo else len(SEED_LEADS)
    mode = "demo-workspace" if arguments.demo else "default-workspace"
    print(f"Seeded {count} illustrative {mode} leads.")
