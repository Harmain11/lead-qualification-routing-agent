from __future__ import annotations

from functools import lru_cache
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from lead_agent.application.auth import authenticate_session, require_membership, verify_csrf
from lead_agent.domain.auth import AuthenticatedIdentity, AuthenticationError
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.crm import CrmProvider
from lead_agent.domain.workspaces import DEFAULT_WORKSPACE_ID, WorkspaceContext
from lead_agent.infrastructure.crm.factory import (
    WorkspaceCrmProviderResolver,
    get_crm_provider,
    get_workspace_crm_resolver,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.settings.config import Settings, get_settings


@lru_cache
def get_policies() -> tuple[IcpConfig, RoutingConfig]:
    return get_settings().load_policies()


def get_runtime_settings() -> Settings:
    return get_settings()


def _session_token(request: Request, settings: Settings) -> str | None:
    authorization = request.headers.get("Authorization")
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not value:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid authorization header",
            )
        return value
    return request.cookies.get(settings.auth_session_cookie_name)


def get_authenticated_identity(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> AuthenticatedIdentity:
    token = _session_token(request, settings)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    try:
        return authenticate_session(session, token)
    except AuthenticationError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error


def get_csrf_authenticated_identity(
    request: Request,
    identity: AuthenticatedIdentity = Depends(get_authenticated_identity),
    session: Session = Depends(get_db),
) -> AuthenticatedIdentity:
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.headers.get(
        "Authorization"
    ):
        try:
            verify_csrf(session, identity.session_id, request.headers.get("X-CSRF-Token"))
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(error),
            ) from error
    return identity


def get_workspace_context(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
    x_actor_id: str | None = Header(default=None, alias="X-Actor-ID"),
) -> WorkspaceContext:
    token = _session_token(request, settings)
    if token:
        try:
            identity = authenticate_session(session, token)
            workspace_id = UUID(x_workspace_id) if x_workspace_id else identity.current_workspace_id
            require_membership(session, identity.user_id, workspace_id)
            if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.headers.get(
                "Authorization"
            ):
                verify_csrf(
                    session,
                    identity.session_id,
                    request.headers.get("X-CSRF-Token"),
                )
        except ValueError as error:
            if isinstance(error, AuthenticationError):
                code = status.HTTP_401_UNAUTHORIZED
            else:
                code = status.HTTP_403_FORBIDDEN
            raise HTTPException(status_code=code, detail=str(error)) from error
        request.state.workspace_id = str(workspace_id)
        request.state.actor_id = str(identity.user_id)
        return WorkspaceContext(workspace_id=workspace_id, actor_id=str(identity.user_id))

    development_allowed = settings.auth_development_fallback and settings.app_env.lower() in {
        "development",
        "test",
        "local",
    }
    if not development_allowed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    try:
        workspace_id = UUID(x_workspace_id) if x_workspace_id else DEFAULT_WORKSPACE_ID
    except ValueError as error:
        raise HTTPException(status_code=400, detail="X-Workspace-ID must be a UUID") from error
    request.state.workspace_id = str(workspace_id)
    request.state.actor_id = (x_actor_id or "development-user")[:255]
    return WorkspaceContext(
        workspace_id=workspace_id,
        actor_id=(x_actor_id or "development-user")[:255],
    )


def get_active_crm_provider(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(get_workspace_context),
    fallback: CrmProvider = Depends(get_crm_provider),
    resolver: WorkspaceCrmProviderResolver = Depends(get_workspace_crm_resolver),
) -> CrmProvider:
    return resolver.resolve(session, workspace.workspace_id, fallback)
