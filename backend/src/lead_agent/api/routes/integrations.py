from __future__ import annotations

from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_authenticated_identity, require_permission
from lead_agent.api.schemas import (
    IntegrationCardResponse,
    ProviderConnectionResponse,
    provider_connection_response,
)
from lead_agent.application.auth import membership_permissions, require_membership
from lead_agent.application.integrations import (
    begin_oauth_connection,
    complete_oauth_connection,
    create_manual_connection,
    disconnect_connection,
    reconnect_connection,
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
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import ProviderConnectionORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.oauth import get_oauth_broker
from lead_agent.infrastructure.provider_lifecycle import (
    get_provider_connection_validator,
    get_provider_revoker,
)
from lead_agent.infrastructure.rate_limit import rate_limit_dependency
from lead_agent.infrastructure.repositories import (
    ensure_workspace,
    get_provider_connection,
    list_provider_connections,
)
from lead_agent.infrastructure.secrets import get_secret_store
from lead_agent.settings.config import Settings, get_settings

router = APIRouter(prefix="/v1/integrations", tags=["integrations"])


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
            connection=(
                provider_connection_response(latest[provider]) if provider in latest else None
            ),
        )
        for provider, setup in PROVIDER_SETUP.items()
    ]


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
            session, workspace.workspace_id, provider, request, secret_store
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
        if connection.provider_type != provider:
            raise ValueError("provider does not match OAuth transaction")
        if provider_error or not authorization_code:
            raise ValueError("OAuth authorization was not completed")
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
    except (ValueError, HTTPException) as error:
        query = urlencode(
            {
                "integration": provider,
                "status": "failed",
                "message": str(getattr(error, "detail", error)),
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
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return provider_connection_response(row)
