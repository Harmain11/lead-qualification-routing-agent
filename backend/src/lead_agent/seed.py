from __future__ import annotations

from sqlalchemy.orm import Session

from lead_agent.application.services import process_lead
from lead_agent.infrastructure.db.session import build_session_factory
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


def seed_database(session: Session) -> None:
    icp, routing = get_settings().load_policies()
    for payload in SEED_LEADS:
        process_lead(session, payload, icp, routing)


if __name__ == "__main__":
    factory = build_session_factory()
    with factory() as session:
        seed_database(session)
    print(f"Seeded {len(SEED_LEADS)} illustrative leads.")
