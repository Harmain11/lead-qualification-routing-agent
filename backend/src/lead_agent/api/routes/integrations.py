from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.schemas import (
    IntegrationCardResponse,
    ProviderConnectionResponse,
    provider_connection_response,
)
from lead_agent.application.integrations import (
    begin_oauth_connection,
    complete_oauth_connection,
    create_manual_connection,
    disconnect_connection,
    reconnect_connection,
    validate_connection,
)
from lead_agent.domain.integrations import (
    PROVIDER_SETUP,
    ManualConnectionInput,
    OAuthBroker,
    SecretStore,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.oauth import get_oauth_broker
from lead_agent.infrastructure.repositories import (
    ensure_workspace,
    get_provider_connection,
    list_provider_connections,
)
from lead_agent.infrastructure.secrets import get_secret_store

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
    message: str


def _connection_or_404(session: Session, workspace_id: UUID, connection_id: UUID) -> Any:
    row = get_provider_connection(session, workspace_id, connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Provider connection not found")
    return row


@router.get("", response_model=list[IntegrationCardResponse])
def list_integrations(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(get_workspace_context),
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
    workspace: WorkspaceContext = Depends(get_workspace_context),
) -> ProviderConnectionResponse:
    return provider_connection_response(
        _connection_or_404(session, workspace.workspace_id, connection_id)
    )


@router.post(
    "/{provider}/credentials",
    response_model=ProviderConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_manual_credential(
    provider: str,
    request: ManualConnectionInput,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(get_workspace_context),
    secret_store: SecretStore = Depends(get_secret_store),
) -> ProviderConnectionResponse:
    try:
        row = create_manual_connection(
            session, workspace.workspace_id, provider, request, secret_store
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return provider_connection_response(row)


@router.post("/{provider}/connect", response_model=OAuthBeginResponse)
def begin_connection(
    provider: str,
    request: OAuthBeginRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(get_workspace_context),
    broker: OAuthBroker = Depends(get_oauth_broker),
) -> OAuthBeginResponse:
    try:
        row, started = begin_oauth_connection(
            session,
            workspace.workspace_id,
            provider,
            request.account_name,
            request.scopes,
            broker,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return OAuthBeginResponse(
        connection=provider_connection_response(row),
        authorization_url=started.authorization_url,
        state=started.state,
    )


@router.post("/{provider}/callback", response_model=ProviderConnectionResponse)
def connection_callback(
    provider: str,
    request: OAuthCallbackRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(get_workspace_context),
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
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return provider_connection_response(row)


@router.post("/connections/{connection_id}/test", response_model=ValidationResponse)
def test_connection(
    connection_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(get_workspace_context),
    secret_store: SecretStore = Depends(get_secret_store),
) -> ValidationResponse:
    try:
        row, validation = validate_connection(
            session, workspace.workspace_id, connection_id, secret_store
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return ValidationResponse(
        connection=provider_connection_response(row),
        valid=validation.valid,
        message=validation.message,
    )


@router.post("/connections/{connection_id}/disconnect", response_model=ProviderConnectionResponse)
def disconnect(
    connection_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(get_workspace_context),
    secret_store: SecretStore = Depends(get_secret_store),
) -> ProviderConnectionResponse:
    try:
        row = disconnect_connection(session, workspace.workspace_id, connection_id, secret_store)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return provider_connection_response(row)


@router.post("/connections/{connection_id}/reconnect", response_model=ProviderConnectionResponse)
def reconnect(
    connection_id: UUID,
    request: ReconnectRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(get_workspace_context),
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
