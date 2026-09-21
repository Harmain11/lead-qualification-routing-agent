from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_policies
from lead_agent.api.schemas import (
    ClutchIntakeRequest,
    EmailIntakeRequest,
    LeadCreateRequest,
    LeadResponse,
    LinkedInIntakeRequest,
    lead_response,
)
from lead_agent.application.services import process_source_event
from lead_agent.domain.analysis import ReasoningProvider
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.routing import NotificationProvider
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.intake.adapters import (
    ClutchAdapter,
    EmailAdapter,
    LinkedInAdapter,
    WebsiteAdapter,
)
from lead_agent.infrastructure.notifications.factory import get_notification_provider
from lead_agent.infrastructure.reasoning.factory import get_reasoning_provider
from lead_agent.infrastructure.repositories import get_current_routing
from lead_agent.settings.config import Settings, get_settings

router = APIRouter(prefix="/v1/intake", tags=["intake"])


def _verify_signature(raw_body: bytes, signature: str | None, secret: str) -> None:
    if not secret:
        return
    if not signature:
        raise HTTPException(status_code=401, detail="missing webhook signature")
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="invalid webhook signature")


def _json_payload(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=422, detail="request body must be valid JSON") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be a JSON object")
    return payload


def _intake(
    adapter: Any,
    payload: Mapping[str, Any],
    session: Session,
    policies: tuple[IcpConfig, RoutingConfig],
    provider: ReasoningProvider,
    notification_provider: NotificationProvider,
) -> LeadResponse:
    try:
        event = adapter.adapt(payload)
        result = process_source_event(
            session,
            event,
            *policies,
            provider=provider,
            notification_provider=notification_provider,
        )
    except (ValueError, ValidationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return lead_response(result.lead, get_current_routing(session, result.lead.id))


@router.post("/web", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
async def website_intake(
    request: Request,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    settings: Settings = Depends(get_settings),
    provider: ReasoningProvider = Depends(get_reasoning_provider),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
) -> LeadResponse:
    raw_body = await request.body()
    _verify_signature(
        raw_body, request.headers.get("X-Webhook-Signature"), settings.webhook_signing_secret
    )
    payload = _json_payload(raw_body)
    try:
        validated = LeadCreateRequest.model_validate(payload)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()) from error
    return _intake(
        WebsiteAdapter(),
        validated.model_dump(mode="json"),
        session,
        policies,
        provider,
        notification_provider,
    )


@router.post("/email", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
def email_intake(
    request: EmailIntakeRequest,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    provider: ReasoningProvider = Depends(get_reasoning_provider),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
) -> LeadResponse:
    return _intake(
        EmailAdapter(),
        request.model_dump(mode="json"),
        session,
        policies,
        provider,
        notification_provider,
    )


@router.post("/linkedin", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
def linkedin_intake(
    request: LinkedInIntakeRequest,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    provider: ReasoningProvider = Depends(get_reasoning_provider),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
) -> LeadResponse:
    return _intake(
        LinkedInAdapter(),
        request.model_dump(mode="json"),
        session,
        policies,
        provider,
        notification_provider,
    )


@router.post("/fourth", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
def fourth_channel_intake(
    request: ClutchIntakeRequest,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    provider: ReasoningProvider = Depends(get_reasoning_provider),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
) -> LeadResponse:
    return _intake(
        ClutchAdapter(),
        request.model_dump(mode="json"),
        session,
        policies,
        provider,
        notification_provider,
    )
