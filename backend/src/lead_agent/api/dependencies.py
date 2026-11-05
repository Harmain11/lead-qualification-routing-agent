from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from lead_agent.application.auth import (
    authenticate_session,
    membership_permissions,
    require_membership,
    verify_csrf,
)
from lead_agent.domain.auth import (
    AuthenticatedIdentity,
    AuthenticationError,
    AuthorizationError,
)
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.crm import CrmProvider
from lead_agent.domain.rbac import role_permissions
from lead_agent.domain.workspaces import DEFAULT_WORKSPACE_ID, WorkspaceContext
from lead_agent.infrastructure.crm.factory import (
    WorkspaceCrmProviderResolver,
    get_crm_provider,
    get_workspace_crm_resolver,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.db.team_scope import bind_team_scope
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


def _check_residency(request: Request, session: Session, workspace_id: UUID) -> None:
    from lead_agent.application.governance import OutreachBlocked, assert_residency

    # Keep policy repair available when an operator moves the deployment to a different region.
    if request.url.path == "/v1/ops/policy":
        return
    try:
        assert_residency(session, workspace_id)
    except OutreachBlocked as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


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
            membership = require_membership(session, identity.user_id, workspace_id)
            if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.headers.get(
                "Authorization"
            ):
                verify_csrf(
                    session,
                    identity.session_id,
                    request.headers.get("X-CSRF-Token"),
                )
        except AuthenticationError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)
            ) from error
        except AuthorizationError as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
        _check_residency(request, session, workspace_id)
        request.state.workspace_id = str(workspace_id)
        request.state.actor_id = str(identity.user_id)
        context = WorkspaceContext(
            workspace_id=workspace_id,
            actor_id=str(identity.user_id),
            user_id=identity.user_id,
            membership_id=membership.id,
            role=membership.role,
            permissions=membership_permissions(membership),
        )
        bind_team_scope(session, context)
        return context

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
    _check_residency(request, session, workspace_id)
    request.state.workspace_id = str(workspace_id)
    request.state.actor_id = (x_actor_id or "development-user")[:255]
    context = WorkspaceContext(
        workspace_id=workspace_id,
        actor_id=(x_actor_id or "development-user")[:255],
        role="owner",
        permissions=role_permissions("owner"),
    )
    bind_team_scope(session, context)
    return context


def get_active_crm_provider(
    provider_type: str | None = Query(default=None, alias="provider"),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(get_workspace_context),
    fallback: CrmProvider = Depends(get_crm_provider),
    resolver: WorkspaceCrmProviderResolver = Depends(get_workspace_crm_resolver),
) -> CrmProvider:
    if "crm.sync" not in workspace.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="permission required: crm.sync",
        )
    return resolver.resolve(
        session,
        workspace.workspace_id,
        fallback,
        actor_id=workspace.actor_id,
        provider_type=provider_type,
    )


def require_permissions(*permissions: str) -> Callable[..., WorkspaceContext]:
    if not permissions or any(not permission for permission in permissions):
        raise ValueError("permissions must not be empty")

    def dependency(
        workspace: WorkspaceContext = Depends(get_workspace_context),
    ) -> WorkspaceContext:
        missing = [
            permission for permission in permissions if permission not in workspace.permissions
        ]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"permission required: {missing[0]}",
            )
        return workspace

    return dependency


def require_permission(permission: str) -> Callable[..., WorkspaceContext]:
    return require_permissions(permission)


def require_any_permission(*permissions: str) -> Callable[..., WorkspaceContext]:
    if not permissions or any(not permission for permission in permissions):
        raise ValueError("permissions must not be empty")

    def dependency(
        workspace: WorkspaceContext = Depends(get_workspace_context),
    ) -> WorkspaceContext:
        if not any(permission in workspace.permissions for permission in permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"one of these permissions is required: {', '.join(permissions)}",
            )
        return workspace

    return dependency
