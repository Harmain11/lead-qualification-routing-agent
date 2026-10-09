from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_authenticated_identity, require_permission
from lead_agent.api.schemas import (
    IntegrationCardResponse,
    ProviderConnectionResponse,
    provider_connection_response,
)
from lead_agent.application.auth import membership_permissions, require_membership
from lead_agent.application.integrations import (
    activate_reasoning_provider,
    begin_oauth_connection,
    complete_oauth_connection,
    create_manual_connection,
    disconnect_connection,
    reconnect_connection,
    reject_oauth_connection,
    update_openai_model,
    validate_connection,
)
from lead_agent.domain.auth import AuthenticatedIdentity, AuthorizationError
from lead_agent.domain.integration_directionality import directionality_for
from lead_agent.domain.integrations import (
    PROVIDER_SETUP,
    ManualConnectionInput,
    OAuthBroker,
    ProviderConnectionValidator,
    ProviderRevoker,
    SecretStore,
)
from lead_agent.domain.oauth_errors import OAuthProviderError
from lead_agent.domain.routing import NotificationMessage
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import (
    AnalysisRunORM,
    LeadORM,
    ProviderConnectionORM,
    ProviderLifecycleAuditORM,
    WorkspaceNotificationConfigORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.oauth import get_oauth_broker
from lead_agent.infrastructure.provider_lifecycle import (
    get_provider_connection_validator,
    get_provider_revoker,
    record_provider_lifecycle_audit,
)
from lead_agent.infrastructure.rate_limit import rate_limit_dependency
from lead_agent.infrastructure.reasoning.configuration import (
    default_reasoning_model,
    supported_reasoning_models,
)
from lead_agent.infrastructure.repositories import (
    ensure_workspace,
    get_provider_connection,
    list_provider_connections,
)
from lead_agent.infrastructure.secrets import get_secret_store
from lead_agent.infrastructure.slack import (
    WorkspaceSlackNotificationResolver,
    get_workspace_slack_notification_resolver,
)
from lead_agent.settings.config import Settings, get_settings

router = APIRouter(prefix="/v1/integrations", tags=["integrations"])
_LOGGER = logging.getLogger(__name__)

_OPENAI_USAGE_WINDOWS = {
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
    "quarterly": timedelta(days=90),
    "annual": timedelta(days=365),
}
_REASONING_USAGE_PROVIDERS = ("openai", "gemini", "claude")
_REASONING_USAGE_ACTIONS = {
    "openai": "openai_api_usage",
    "gemini": "reasoning_api_usage",
    "claude": "reasoning_api_usage",
}
_USAGE_TOTAL_FIELDS = (
    "requests",
    "successful_requests",
    "failed_requests",
    "input_tokens",
    "output_tokens",
    "total_tokens",
)


class OAuthBeginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_name: str = Field(default="Pending account", min_length=1, max_length=255)
    scopes: list[str] = Field(default_factory=list)


class OAuthBeginResponse(BaseModel):
    connection: ProviderConnectionResponse
    authorization_url: str
    state: str


class OAuthCallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    state: str = Field(min_length=16)
    authorization_code: str = Field(min_length=1)


class ReconnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credentials: dict[str, str]


class OpenAIModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=128)


@router.get("/openai/usage")
def get_openai_usage(
    period: Literal["weekly", "monthly", "quarterly", "annual"] = Query(default="monthly"),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.view")),
) -> dict[str, Any]:
    """Return workspace-scoped OpenAI calls and token usage observed by Corral."""

    window_end = datetime.now(UTC)
    window_start = window_end - _OPENAI_USAGE_WINDOWS[period]
    events = session.scalars(
        select(ProviderLifecycleAuditORM)
        .where(
            ProviderLifecycleAuditORM.workspace_id == workspace.workspace_id,
            ProviderLifecycleAuditORM.provider == "openai",
            ProviderLifecycleAuditORM.action == "openai_api_usage",
            ProviderLifecycleAuditORM.created_at >= window_start,
            ProviderLifecycleAuditORM.created_at <= window_end,
        )
        .order_by(ProviderLifecycleAuditORM.created_at.desc())
    ).all()

    totals = {
        "requests": len(events),
        "successful_requests": 0,
        "failed_requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    models: dict[str, dict[str, int]] = {}
    for event in events:
        is_success = event.outcome == "success"
        totals["successful_requests" if is_success else "failed_requests"] += 1
        details = event.details if isinstance(event.details, dict) else {}
        model = details.get("model")
        model_name = model[:128] if isinstance(model, str) and model else "Unknown model"
        model_totals = models.setdefault(model_name, {
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        })
        model_totals["requests"] += 1
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            value = details.get(field)
            count = value if isinstance(value, int) and value >= 0 else 0
            totals[field] += count
            model_totals[field] += count

    return {
        "period": period,
        "starts_at": window_start,
        "ends_at": window_end,
        "timezone": "UTC",
        **totals,
        "models": [
            {"model": model, **usage}
            for model, usage in sorted(
                models.items(),
                key=lambda item: item[1]["total_tokens"],
                reverse=True,
            )
        ],
    }

@router.get("/reasoning/usage")
def get_reasoning_usage(
    period: Literal["weekly", "monthly", "quarterly", "annual"] = Query(default="monthly"),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.view")),
) -> dict[str, Any]:
    """Return safe, workspace-scoped usage totals across supported reasoning providers."""

    window_end = datetime.now(UTC)
    window_start = window_end - _OPENAI_USAGE_WINDOWS[period]
    provider_filters = [
        and_(
            ProviderLifecycleAuditORM.provider == provider,
            ProviderLifecycleAuditORM.action == _REASONING_USAGE_ACTIONS[provider],
        )
        for provider in _REASONING_USAGE_PROVIDERS
    ]
    events = session.scalars(
        select(ProviderLifecycleAuditORM)
        .where(
            ProviderLifecycleAuditORM.workspace_id == workspace.workspace_id,
            ProviderLifecycleAuditORM.created_at >= window_start,
            ProviderLifecycleAuditORM.created_at <= window_end,
            or_(*provider_filters),
        )
        .order_by(ProviderLifecycleAuditORM.created_at.desc())
    ).all()

    providers: dict[str, dict[str, Any]] = {
        provider: {
            "provider": provider,
            **{field: 0 for field in _USAGE_TOTAL_FIELDS},
            "models": {},
        }
        for provider in _REASONING_USAGE_PROVIDERS
    }
    for event in events:
        provider_usage = providers.get(event.provider)
        if provider_usage is None:
            continue
        successful = event.outcome == "success"
        outcome_field = "successful_requests" if successful else "failed_requests"
        provider_usage["requests"] += 1
        provider_usage[outcome_field] += 1

        details = event.details if isinstance(event.details, dict) else {}
        model = details.get("model")
        model_name = model[:128].strip() if isinstance(model, str) else ""
        model_name = model_name or "Unknown model"
        models = provider_usage["models"]
        model_usage = models.setdefault(model_name, {
            **{field: 0 for field in _USAGE_TOTAL_FIELDS},
        })
        model_usage["requests"] += 1
        model_usage[outcome_field] += 1
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            value = details.get(field)
            count = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
            provider_usage[field] += count
            model_usage[field] += count

    return {
        "period": period,
        "starts_at": window_start,
        "ends_at": window_end,
        "timezone": "UTC",
        "providers": [
            {
                **{
                    field: value
                    for field, value in provider_usage.items()
                    if field != "models"
                },
                "models": [
                    {"model": model, **usage}
                    for model, usage in sorted(
                        provider_usage["models"].items(),
                        key=lambda item: (-item[1]["total_tokens"], item[0]),
                    )
                ],
            }
            for provider_usage in providers.values()
        ],
    }
class SlackChannelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel_id: str = Field(min_length=3, max_length=255)


class SlackChannelResponse(BaseModel):
    id: str
    name: str
    is_member: bool = True
    is_private: bool = False


class SlackMemberResponse(BaseModel):
    id: str
    name: str


class SlackNotificationRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    connection_id: UUID
    destination_type: Literal["channel", "direct_message"] = "channel"
    channel_id: str | None = Field(default=None, min_length=3, max_length=255)
    queues: list[str] = Field(default_factory=list, max_length=10)
    qualification_bands: list[str] = Field(default_factory=list, max_length=10)
    sources: list[str] = Field(default_factory=list, max_length=10)
    weight: float = Field(default=1, gt=0, le=100)


class SlackNotificationConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_connection_id: UUID | None = None
    rules: list[SlackNotificationRuleRequest] = Field(default_factory=list, max_length=50)


class SlackNotificationRuleResponse(SlackNotificationRuleRequest):
    id: UUID


class SlackNotificationConfigResponse(BaseModel):
    workspace_id: UUID
    default_connection_id: UUID | None
    rules: list[SlackNotificationRuleResponse]
    updated_at: Any | None


class SlackTestNotificationResponse(BaseModel):
    sent: bool
    message: str


class ValidationResponse(BaseModel):
    connection: ProviderConnectionResponse
    valid: bool
    verified: bool
    status: str
    message: str
    retryable: bool
    error_code: str | None


def _connection_or_404(session: Session, workspace_id: UUID, connection_id: UUID) -> Any:
    row = get_provider_connection(session, workspace_id, connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Provider connection not found")
    return row


@router.get("", response_model=list[IntegrationCardResponse])
def list_integrations(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.view")),
    settings: Settings = Depends(get_settings),
) -> list[IntegrationCardResponse]:
    ensure_workspace(session, workspace.workspace_id)
    session.commit()
    connections = list_provider_connections(session, workspace.workspace_id)
    latest: dict[str, Any] = {}
    for row in connections:
        latest.setdefault(row.provider_type, row)
    return [
        IntegrationCardResponse(
            **setup.model_dump(),
            directionality=directionality_for(provider).model_dump(),
            supported_models=(
                list(supported_reasoning_models(provider, settings))
                if provider in {"openai", "gemini", "claude"} else []
            ),
            default_model=(
                default_reasoning_model(provider, settings)
                if provider in {"openai", "gemini", "claude"} else None
            ),
            connection=(
                provider_connection_response(latest[provider]) if provider in latest else None
            ),
            connections=[
                provider_connection_response(row)
                for row in connections
                if row.provider_type == provider
            ],
        )
        for provider, setup in PROVIDER_SETUP.items()
    ]


def _slack_notification_config_response(
    session: Session, workspace_id: UUID
) -> SlackNotificationConfigResponse:
    config = session.get(WorkspaceNotificationConfigORM, workspace_id)
    rules: list[SlackNotificationRuleResponse] = []
    for raw in (config.slack_rules if config is not None else []):
        if not isinstance(raw, dict):
            continue
        try:
            rules.append(SlackNotificationRuleResponse.model_validate(raw))
        except ValueError:
            continue
    return SlackNotificationConfigResponse(
        workspace_id=workspace_id,
        default_connection_id=(
            config.default_slack_connection_id if config is not None else None
        ),
        rules=rules,
        updated_at=config.updated_at if config is not None else None,
    )


@router.get("/slack/notification-config", response_model=SlackNotificationConfigResponse)
def get_slack_notification_config(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.view")),
) -> SlackNotificationConfigResponse:
    return _slack_notification_config_response(session, workspace.workspace_id)


@router.put("/slack/notification-config", response_model=SlackNotificationConfigResponse)
def update_slack_notification_config(
    request: SlackNotificationConfigRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
) -> SlackNotificationConfigResponse:
    slack_connections = list(
        session.scalars(
            select(ProviderConnectionORM).where(
                ProviderConnectionORM.workspace_id == workspace.workspace_id,
                ProviderConnectionORM.provider_type == "slack",
                ProviderConnectionORM.is_active.is_(True),
                ProviderConnectionORM.status == "verified",
            )
        )
    )
    available = {row.id: row for row in slack_connections}
    if request.default_connection_id is not None and request.default_connection_id not in available:
        raise HTTPException(
            status_code=422,
            detail="The default Slack workspace must be an active verified connection.",
        )
    seen_rule_ids: set[UUID] = set()
    normalized_rules: list[dict[str, Any]] = []
    allowed_bands = {"hot", "warm", "needs_qualifying", "nurture", "disqualify", "manual_review"}
    for rule in request.rules:
        if rule.connection_id not in available:
            raise HTTPException(
                status_code=422,
                detail=f"Slack rule '{rule.name}' targets an unavailable connection.",
            )
        rule_id = rule.id or uuid4()
        if rule_id in seen_rule_ids:
            raise HTTPException(
                status_code=422, detail="Slack notification rule IDs must be unique."
            )
        seen_rule_ids.add(rule_id)
        bands = [value.strip().lower() for value in rule.qualification_bands if value.strip()]
        unknown_bands = set(bands) - allowed_bands
        if unknown_bands:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported qualification band(s): {', '.join(sorted(unknown_bands))}",
            )
        if rule.destination_type == "direct_message" and not rule.channel_id:
            raise HTTPException(
                status_code=422,
                detail=f"Slack rule '{rule.name}' needs a Slack member ID for direct messages.",
            )
        normalized_rules.append(
            {
                "id": str(rule_id),
                "name": rule.name.strip(),
                "connection_id": str(rule.connection_id),
                "destination_type": rule.destination_type,
                "channel_id": rule.channel_id.strip() if rule.channel_id else None,
                "queues": sorted({value.strip() for value in rule.queues if value.strip()}),
                "qualification_bands": sorted(set(bands)),
                "sources": sorted({value.strip() for value in rule.sources if value.strip()}),
                "weight": rule.weight,
            }
        )
    now = datetime.now(UTC)
    config = session.get(WorkspaceNotificationConfigORM, workspace.workspace_id)
    if config is None:
        config = WorkspaceNotificationConfigORM(
            workspace_id=workspace.workspace_id,
            default_slack_connection_id=request.default_connection_id,
            slack_rules=normalized_rules,
            updated_by=workspace.user_id,
            updated_at=now,
        )
        session.add(config)
    else:
        config.default_slack_connection_id = request.default_connection_id
        config.slack_rules = normalized_rules
        config.updated_by = workspace.user_id
        config.updated_at = now
    session.commit()
    return _slack_notification_config_response(session, workspace.workspace_id)


@router.get("/connections/{connection_id}", response_model=ProviderConnectionResponse)
def retrieve_connection(
    connection_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.view")),
) -> ProviderConnectionResponse:
    return provider_connection_response(
        _connection_or_404(session, workspace.workspace_id, connection_id)
    )


@router.post(
    "/{provider}/credentials",
    response_model=ProviderConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_dependency("credentials"))],
)
def add_manual_credential(
    provider: str,
    request: ManualConnectionInput,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
    secret_store: SecretStore = Depends(get_secret_store),
) -> ProviderConnectionResponse:
    try:
        row = create_manual_connection(
            session,
            workspace.workspace_id,
            provider,
            request,
            secret_store,
            workspace.actor_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return provider_connection_response(row)


@router.post(
    "/{provider}/connect",
    response_model=OAuthBeginResponse,
    dependencies=[Depends(rate_limit_dependency("oauth"))],
)
def begin_connection(
    provider: str,
    request: OAuthBeginRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.connect")),
    broker: OAuthBroker = Depends(get_oauth_broker),
    secret_store: SecretStore = Depends(get_secret_store),
    settings: Settings = Depends(get_settings),
) -> OAuthBeginResponse:
    if provider == "slack" and "integrations.manage" not in workspace.permissions:
        raise HTTPException(
            status_code=403, detail="permission required: integrations.manage"
        )
    try:
        row, started = begin_oauth_connection(
            session,
            workspace.workspace_id,
            provider,
            request.account_name,
            request.scopes,
            broker,
            secret_store,
            settings.integration_public_base_url,
            workspace.actor_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return OAuthBeginResponse(
        connection=provider_connection_response(row),
        authorization_url=started.authorization_url,
        state=started.state,
    )


@router.get("/oauth/development-authorize", include_in_schema=False)
def development_authorize(
    provider: str,
    state: str,
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if settings.oauth_broker != "development" or settings.app_env == "production":
        raise HTTPException(status_code=404, detail="not found")
    callback = (
        f"{settings.integration_public_base_url.rstrip('/')}"
        f"/v1/integrations/{provider}/oauth/callback"
    )
    return RedirectResponse(f"{callback}?state={state}&code=development-approved")


@router.get(
    "/{provider}/oauth/callback",
    include_in_schema=False,
    dependencies=[Depends(rate_limit_dependency("oauth"))],
)
def browser_connection_callback(
    provider: str,
    state_value: str = Query(alias="state", min_length=16),
    authorization_code: str | None = Query(default=None, alias="code", min_length=1),
    provider_error: str | None = Query(default=None, alias="error"),
    session: Session = Depends(get_db),
    identity: AuthenticatedIdentity = Depends(get_authenticated_identity),
    secret_store: SecretStore = Depends(get_secret_store),
    broker: OAuthBroker = Depends(get_oauth_broker),
    validator: ProviderConnectionValidator = Depends(get_provider_connection_validator),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    try:
        connection_id = UUID(state_value.split(".", 1)[0])
        connection = session.get(ProviderConnectionORM, connection_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="Provider connection not found")
        membership = require_membership(session, identity.user_id, connection.workspace_id)
        if "integrations.connect" not in membership_permissions(membership):
            raise AuthorizationError("permission required: integrations.connect")
        if provider == "slack" and "integrations.manage" not in membership_permissions(membership):
            raise AuthorizationError("permission required: integrations.manage")
        if connection.provider_type != provider:
            raise ValueError("provider does not match OAuth transaction")
        if provider_error or not authorization_code:
            safe_error = OAuthProviderError(
                provider, provider_error or "missing_authorization_code"
            )
            reject_oauth_connection(
                session, connection.workspace_id, connection_id, state_value,
                secret_store, str(identity.user_id), safe_error,
            )
            raise safe_error
        complete_oauth_connection(
            session,
            connection.workspace_id,
            connection_id,
            state_value,
            authorization_code,
            secret_store,
            broker,
            str(identity.user_id),
        )
        if provider in {
            "slack",
            "gmail",
            "outlook",
            "google_calendar",
            "calendly",
            "salesforce",
            "hubspot",
        }:
            _, validation = validate_connection(
                session,
                connection.workspace_id,
                connection_id,
                secret_store,
                broker,
                validator,
                str(identity.user_id),
            )
            if not validation.verified:
                raise ValueError(validation.message)
    except (ValueError, HTTPException) as error:
        # Do not log callback query strings, state, authorization codes or raw
        # provider responses. Only known local messages and allowlisted codes.
        detail = str(getattr(error, "detail", error))
        known_messages = {
            "pending connection was not found",
            "OAuth state validation failed",
            "OAuth callback association is invalid",
            "OAuth transaction belongs to a different actor",
            "OAuth authorization request expired; reconnect and try again",
            "OAuth state has already been redeemed",
            "OAuth PKCE transaction is unavailable",
            "OAuth credential storage failed",
            "provider does not match OAuth transaction",
            "Provider connection not found",
        }
        safe_message = (
            str(error) if isinstance(error, OAuthProviderError)
            else detail if detail in known_messages
            else "The provider connection could not be verified. Reconnect and try again."
        )
        reason = (
            error.error_code if isinstance(error, OAuthProviderError)
            else detail if detail in known_messages else "callback_validation_failed"
        )
        _LOGGER.warning(
            "OAuth callback failed for provider=%s reason=%s",
            provider if provider in PROVIDER_SETUP else "unknown", reason,
        )
        query = urlencode(
            {
                "integration": provider,
                "status": "failed",
                "message": safe_message,
            }
        )
        target = f"{settings.frontend_public_base_url.rstrip('/')}?{query}"
        return RedirectResponse(target)
    except Exception:
        session.rollback()
        _LOGGER.exception("OAuth callback failed for provider=%s", provider)
        query = urlencode(
            {
                "integration": provider,
                "status": "failed",
                "message": "The provider connection failed. Reconnect and try again.",
            }
        )
        target = f"{settings.frontend_public_base_url.rstrip('/')}?{query}"
        return RedirectResponse(target)
    query = urlencode({"integration": provider, "status": "connected"})
    target = f"{settings.frontend_public_base_url.rstrip('/')}?{query}"
    return RedirectResponse(target)


@router.post(
    "/{provider}/callback",
    response_model=ProviderConnectionResponse,
    dependencies=[Depends(rate_limit_dependency("oauth"))],
)
def connection_callback(
    provider: str,
    request: OAuthCallbackRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.connect")),
    secret_store: SecretStore = Depends(get_secret_store),
    broker: OAuthBroker = Depends(get_oauth_broker),
) -> ProviderConnectionResponse:
    connection = _connection_or_404(session, workspace.workspace_id, request.connection_id)
    if connection.provider_type != provider:
        raise HTTPException(status_code=404, detail="Provider connection not found")
    try:
        row = complete_oauth_connection(
            session,
            workspace.workspace_id,
            request.connection_id,
            request.state,
            request.authorization_code,
            secret_store,
            broker,
            workspace.actor_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return provider_connection_response(row)


@router.post(
    "/connections/{connection_id}/test",
    response_model=ValidationResponse,
    dependencies=[Depends(rate_limit_dependency("provider_actions"))],
)
def test_connection(
    connection_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
    secret_store: SecretStore = Depends(get_secret_store),
    broker: OAuthBroker = Depends(get_oauth_broker),
    validator: ProviderConnectionValidator = Depends(get_provider_connection_validator),
) -> ValidationResponse:
    try:
        row, validation = validate_connection(
            session,
            workspace.workspace_id,
            connection_id,
            secret_store,
            broker,
            validator,
            workspace.actor_id,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return ValidationResponse(
        connection=provider_connection_response(row),
        valid=validation.valid,
        verified=validation.verified,
        status=validation.status,
        message=validation.message,
        retryable=validation.retryable,
        error_code=validation.error_code,
    )


@router.post("/connections/{connection_id}/backfill-openai")
@router.post("/connections/{connection_id}/backfill-reasoning")
def backfill_openai_connection(
    connection_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    connection = _connection_or_404(session, workspace.workspace_id, connection_id)
    if (
        connection.provider_type not in {"openai", "gemini", "claude"}
        or connection.status != "verified"
        or not connection.is_active
    ):
        raise HTTPException(
            status_code=409,
            detail="Verify and activate the AI provider before backfill",
        )
    count = session.scalar(
        select(func.count(LeadORM.id))
        .where(
            LeadORM.workspace_id == workspace.workspace_id,
            ~select(AnalysisRunORM.id)
            .where(
                AnalysisRunORM.lead_id == LeadORM.id,
                AnalysisRunORM.provider == connection.provider_type,
                AnalysisRunORM.status == "completed",
            )
            .exists(),
        )
    ) or 0
    # Backfill is optional. In local development, do not hold the successful
    # credential verification hostage to a missing Redis/Celery worker.
    redis_client = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    try:
        redis_client.ping()
    except (RedisError, OSError, ValueError) as error:
        if settings.app_env.lower() in {"development", "test", "local"}:
            return {
                "status": "skipped",
                "queued_count": 0,
                "message": (
                    "The AI provider is verified. Backfill will run when the local worker "
                    "and Redis are available."
                ),
            }
        raise HTTPException(
            status_code=503, detail="The lead analysis worker is unavailable"
        ) from error
    finally:
        redis_client.close()
    from lead_agent.worker.celery_app import reanalyze_workspace_leads

    try:
        reanalyze_workspace_leads.delay(str(workspace.workspace_id))
    except Exception as error:
        raise HTTPException(
            status_code=503, detail="The lead analysis worker is unavailable"
        ) from error
    return {
        "status": "queued",
        "queued_count": int(count),
        "message": (
            "Existing leads will be reanalyzed with "
            f"{connection.provider_type.title()} in the background."
        ),
    }


@router.get(
    "/connections/{connection_id}/slack-channels", response_model=list[SlackChannelResponse]
)
def list_slack_channels(
    connection_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
    notification_resolver: WorkspaceSlackNotificationResolver = Depends(
        get_workspace_slack_notification_resolver
    ),
) -> list[SlackChannelResponse]:
    try:
        channels = notification_resolver.list_channels(
            session, workspace.workspace_id, connection_id, workspace.actor_id
        )
    except (LookupError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return [SlackChannelResponse(**channel) for channel in channels]


@router.get(
    "/connections/{connection_id}/slack-channels/{channel_id}/members",
    response_model=list[SlackMemberResponse],
)
def list_slack_channel_members(
    connection_id: UUID,
    channel_id: str,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
    notification_resolver: WorkspaceSlackNotificationResolver = Depends(
        get_workspace_slack_notification_resolver
    ),
) -> list[SlackMemberResponse]:
    try:
        members = notification_resolver.list_channel_members(
            session, workspace.workspace_id, connection_id, channel_id, workspace.actor_id
        )
    except (LookupError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return [SlackMemberResponse(**member) for member in members]


@router.put("/connections/{connection_id}/slack-channel", response_model=SlackChannelResponse)
def select_slack_channel(
    connection_id: UUID,
    request: SlackChannelRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
    notification_resolver: WorkspaceSlackNotificationResolver = Depends(
        get_workspace_slack_notification_resolver
    ),
) -> SlackChannelResponse:
    try:
        channel = notification_resolver.select_channel(
            session, workspace.workspace_id, connection_id, request.channel_id, workspace.actor_id
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return SlackChannelResponse(**channel)


@router.post(
    "/connections/{connection_id}/slack-test-notification",
    response_model=SlackTestNotificationResponse,
)
def send_slack_test_notification(
    connection_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
    notification_resolver: WorkspaceSlackNotificationResolver = Depends(
        get_workspace_slack_notification_resolver
    ),
) -> SlackTestNotificationResponse:
    try:
        row, _ = notification_resolver.resolve_connection(
            session, workspace.workspace_id, connection_id, workspace.actor_id
        )
        channel_id = row.connection_metadata.get("slack_channel_id")
        if not isinstance(channel_id, str) or not channel_id:
            raise ValueError("Choose a Slack notification channel first.")
        provider = notification_resolver.provider_for_connection(
            session, workspace.workspace_id, connection_id, workspace.actor_id
        )
        if provider is None:
            raise ValueError("Verified Slack connection and notification channel are required.")
        delivery = provider.send(
            NotificationMessage(
                deduplication_key=f"slack-test:{connection_id}:{UUID(int=0)}",
                priority="normal",
                text=(
                    "[TEST] Lead Qualification & Routing Agent notification test. "
                    "This confirms the selected workspace channel is connected."
                ),
                metadata={"connection_id": str(connection_id)},
            )
        )
        record_provider_lifecycle_audit(
            session,
            row,
            action="test_notification",
            outcome="sent" if delivery.state == "sent" else "failed",
            actor_id=workspace.actor_id,
            error_code=None if delivery.state == "sent" else "slack_test_notification_failed",
            details={"channel_id": channel_id, "delivery_state": delivery.state},
        )
        session.commit()
        if delivery.state != "sent":
            raise ValueError(delivery.error_message or "Slack test notification could not be sent.")
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return SlackTestNotificationResponse(
        sent=True, message="Test notification sent to the selected channel."
    )


@router.post("/connections/{connection_id}/disconnect", response_model=ProviderConnectionResponse)
def disconnect(
    connection_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.disconnect")),
    secret_store: SecretStore = Depends(get_secret_store),
    revoker: ProviderRevoker = Depends(get_provider_revoker),
) -> ProviderConnectionResponse:
    try:
        row = disconnect_connection(
            session,
            workspace.workspace_id,
            connection_id,
            secret_store,
            revoker,
            workspace.actor_id,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return provider_connection_response(row)


@router.post(
    "/connections/{connection_id}/reconnect",
    response_model=ProviderConnectionResponse,
    dependencies=[Depends(rate_limit_dependency("credentials"))],
)
def reconnect(
    connection_id: UUID,
    request: ReconnectRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
    secret_store: SecretStore = Depends(get_secret_store),
) -> ProviderConnectionResponse:
    try:
        row = reconnect_connection(
            session,
            workspace.workspace_id,
            connection_id,
            request.credentials,
            secret_store,
            workspace.actor_id,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return provider_connection_response(row)


@router.put(
    "/connections/{connection_id}/openai-model",
    response_model=ProviderConnectionResponse,
)
@router.put(
    "/connections/{connection_id}/reasoning-model",
    response_model=ProviderConnectionResponse,
)
def configure_openai_model(
    connection_id: UUID,
    request: OpenAIModelRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
) -> ProviderConnectionResponse:
    try:
        row = update_openai_model(
            session,
            workspace.workspace_id,
            connection_id,
            request.model,
            actor_id=workspace.actor_id,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return provider_connection_response(row)


@router.post(
    "/connections/{connection_id}/activate-reasoning",
    response_model=ProviderConnectionResponse,
)
def activate_reasoning_connection(
    connection_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.manage")),
) -> ProviderConnectionResponse:
    try:
        row = activate_reasoning_provider(
            session, workspace.workspace_id, connection_id, actor_id=workspace.actor_id
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return provider_connection_response(row)
