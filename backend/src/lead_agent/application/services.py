from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.models import NormalizedLead, QualificationResult
from lead_agent.domain.normalization import normalize_lead
from lead_agent.domain.scoring import qualify_lead
from lead_agent.infrastructure.db.models import LeadORM
from lead_agent.infrastructure.repositories import create_lead_with_result


def process_lead(
    session: Session,
    payload: dict[str, object],
    icp: IcpConfig,
    routing: RoutingConfig,
) -> tuple[NormalizedLead, QualificationResult, LeadORM]:
    lead = normalize_lead(payload, uuid4())
    result = qualify_lead(lead, icp, routing)
    row = create_lead_with_result(session, lead, result)
    return lead, result, row
