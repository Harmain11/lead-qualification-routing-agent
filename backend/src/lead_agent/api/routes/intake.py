from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import (
    get_policies,
    require_permission,
)
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
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import WorkspacePublicIntakeORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.intake.adapters import (
    ClutchAdapter,
    EmailAdapter,
    LinkedInAdapter,
    WebsiteAdapter,
)
from lead_agent.infrastructure.notifications.factory import get_notification_provider
from lead_agent.infrastructure.rate_limit import rate_limit_dependency
from lead_agent.infrastructure.repositories import get_current_crm_sync, get_current_routing
from lead_agent.settings.config import Settings, get_settings

router = APIRouter(prefix="/v1/intake", tags=["intake"])

_PUBLIC_FORM_FIELDS = {
    "source_detail", "event_id", "external_id", "full_name", "email", "phone",
    "job_title", "company_name", "industry", "employee_count", "annual_revenue",
    "geography", "budget", "product_or_service", "message", "received_at",
}


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
    provider: ReasoningProvider | None,
    notification_provider: NotificationProvider,
    workspace: WorkspaceContext,
) -> LeadResponse:
    try:
        event = adapter.adapt(payload)
        result = process_source_event(
            session,
            event,
            *policies,
            provider=provider,
            notification_provider=notification_provider,
            workspace_id=workspace.workspace_id,
        )
    except (ValueError, ValidationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return lead_response(
        result.lead,
        get_current_routing(session, result.lead.id, workspace.workspace_id),
        get_current_crm_sync(session, result.lead.id, workspace_id=workspace.workspace_id),
    )


def _public_form_url(settings: Settings, token: str) -> str:
    return f"{settings.integration_public_base_url.rstrip('/')}/v1/intake/public/{token}"


@router.get("/public-config")
def public_config(
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
) -> dict[str, object]:
    row = session.get(WorkspacePublicIntakeORM, workspace.workspace_id)
    now = datetime.now(UTC)
    if row is None:
        row = WorkspacePublicIntakeORM(
            workspace_id=workspace.workspace_id,
            public_token=secrets.token_urlsafe(32),
            created_by=workspace.user_id,
            created_at=now,
        )
        session.add(row)
        session.commit()
    return {
        "workspace_id": str(workspace.workspace_id),
        "url": _public_form_url(settings, row.public_token),
        "created_at": row.created_at,
        "rotated_at": row.rotated_at,
    }


@router.post("/public-config/rotate")
def rotate_public_config(
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
) -> dict[str, object]:
    row = session.get(WorkspacePublicIntakeORM, workspace.workspace_id)
    now = datetime.now(UTC)
    if row is None:
        row = WorkspacePublicIntakeORM(
            workspace_id=workspace.workspace_id,
            public_token=secrets.token_urlsafe(32),
            created_by=workspace.user_id,
            created_at=now,
        )
        session.add(row)
    else:
        row.public_token = secrets.token_urlsafe(32)
        row.rotated_at = now
    session.commit()
    return {
        "workspace_id": str(workspace.workspace_id),
        "url": _public_form_url(settings, row.public_token),
        "created_at": row.created_at,
        "rotated_at": row.rotated_at,
    }


def _public_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in raw.items() if key in _PUBLIC_FORM_FIELDS}
    payload["source"] = "website"
    payload["source_detail"] = payload.get("source_detail") or "public-website-form"
    return payload


@router.post(
    "/public/{token}",
    response_model=LeadResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_dependency("agent"))],
)
async def public_website_intake(
    token: str,
    request: Request,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
) -> LeadResponse:
    row = session.scalar(
        select(WorkspacePublicIntakeORM).where(WorkspacePublicIntakeORM.public_token == token)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="website intake endpoint not found")
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        payload = _json_payload(await request.body())
    elif (
        "application/x-www-form-urlencoded" in content_type
        or "multipart/form-data" in content_type
    ):
        form = await request.form()
        payload = {key: value for key, value in form.multi_items() if isinstance(value, str)}
    else:
        raise HTTPException(
            status_code=415,
            detail="website intake requires JSON or an HTML form submission",
        )
    try:
        validated = LeadCreateRequest.model_validate(_public_payload(payload))
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()) from error
    workspace = WorkspaceContext(workspace_id=row.workspace_id, actor_id="public-website-form")
    return _intake(
        WebsiteAdapter(),
        validated.model_dump(mode="json"),
        session,
        policies,
        None,
        notification_provider,
        workspace,
    )


@router.post("/web", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
async def website_intake(
    request: Request,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    settings: Settings = Depends(get_settings),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
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
        None,
        notification_provider,
        workspace,
    )


@router.post("/email", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
def email_intake(
    request: EmailIntakeRequest,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> LeadResponse:
    return _intake(
        EmailAdapter(),
        request.model_dump(mode="json"),
        session,
        policies,
        None,
        notification_provider,
        workspace,
    )


@router.post("/linkedin", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
def linkedin_intake(
    request: LinkedInIntakeRequest,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> LeadResponse:
    return _intake(
        LinkedInAdapter(),
        request.model_dump(mode="json"),
        session,
        policies,
        None,
        notification_provider,
        workspace,
    )


@router.post("/fourth", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
def fourth_channel_intake(
    request: ClutchIntakeRequest,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    notification_provider: NotificationProvider = Depends(get_notification_provider),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> LeadResponse:
    return _intake(
        ClutchAdapter(),
        request.model_dump(mode="json"),
        session,
        policies,
        None,
        notification_provider,
        workspace,
    )
