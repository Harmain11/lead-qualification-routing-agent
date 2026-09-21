from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_policies
from lead_agent.api.schemas import (
    LeadCreateRequest,
    LeadListResponse,
    LeadResponse,
    QualificationResponse,
    lead_response,
    qualification_response,
)
from lead_agent.application.services import process_lead
from lead_agent.domain.analysis import ReasoningProvider
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.reasoning.factory import get_reasoning_provider
from lead_agent.infrastructure.repositories import get_lead, get_qualification, list_leads

router = APIRouter(prefix="/v1/leads", tags=["leads"])


def policy_dependency() -> tuple[IcpConfig, RoutingConfig]:
    return get_policies()


def _process_create_request(
    request: LeadCreateRequest,
    session: Session,
    policies: tuple[IcpConfig, RoutingConfig],
    provider: ReasoningProvider,
) -> LeadResponse:
    try:
        _, _, row = process_lead(
            session, request.model_dump(mode="json"), *policies, provider=provider
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return lead_response(row)


@router.post("", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
def create_lead(
    request: LeadCreateRequest,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(policy_dependency),
    provider: ReasoningProvider = Depends(get_reasoning_provider),
) -> LeadResponse:
    return _process_create_request(request, session, policies, provider)


@router.get("", response_model=LeadListResponse)
def list_all_leads(session: Session = Depends(get_db)) -> LeadListResponse:
    rows = list_leads(session)
    return LeadListResponse(items=[lead_response(row) for row in rows], total=len(rows))


@router.get("/{lead_id}", response_model=LeadResponse)
def retrieve_lead(lead_id: UUID, session: Session = Depends(get_db)) -> LeadResponse:
    row = get_lead(session, lead_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead_response(row)


@router.get("/{lead_id}/qualification", response_model=QualificationResponse)
def retrieve_qualification(
    lead_id: UUID, session: Session = Depends(get_db)
) -> QualificationResponse:
    if get_lead(session, lead_id) is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    result = get_qualification(session, lead_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Qualification not found")
    return qualification_response(result)  # type: ignore[return-value]
