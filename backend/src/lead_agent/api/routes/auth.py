from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import (
    get_authenticated_identity,
    get_csrf_authenticated_identity,
    get_runtime_settings,
)
from lead_agent.application.auth import (
    IssuedSession,
    authenticate_session,
    hash_password,
    issue_session,
    list_memberships,
    register_user,
    require_membership,
    revoke_session,
    revoke_session_token,
    select_workspace,
    sign_in,
)
from lead_agent.domain.auth import AuthenticatedIdentity, AuthenticationError, AuthorizationError
from lead_agent.domain.workspaces import DEFAULT_WORKSPACE_ID, DEMO_WORKSPACE_ID
from lead_agent.infrastructure.db.models import AuthSessionORM, UserORM, WorkspaceMembershipORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.rate_limit import rate_limit_dependency
from lead_agent.infrastructure.repositories import ensure_workspace
from lead_agent.settings.config import Settings

router = APIRouter(prefix="/v1/auth", tags=["authentication"])


class SignUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=10, max_length=256)
    display_name: str = Field(min_length=1, max_length=255)
    workspace_name: str = Field(min_length=1, max_length=255)


class SignInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class WorkspaceSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID


class UserResponse(BaseModel):
    id: UUID
    email: str
    display_name: str


class WorkspaceMembershipResponse(BaseModel):
    id: UUID
    name: str
    role: str
    is_demo: bool


class SessionResponse(BaseModel):
    user: UserResponse
    current_workspace_id: UUID
    workspaces: list[WorkspaceMembershipResponse]
    expires_at: datetime | None


def _set_session_cookies(response: Response, issued: IssuedSession, settings: Settings) -> None:
    max_age = settings.auth_session_hours * 3600
    expires_at = issued.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    else:
        expires_at = expires_at.astimezone(UTC)
    response.set_cookie(
        settings.auth_session_cookie_name,
        issued.token,
        max_age=max_age,
        expires=expires_at,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )
    response.set_cookie(
        settings.auth_csrf_cookie_name,
        issued.csrf_token,
        max_age=max_age,
        expires=expires_at,
        secure=settings.auth_cookie_secure,
        httponly=False,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )


def _clear_session_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.auth_session_cookie_name,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )
    response.delete_cookie(
        settings.auth_csrf_cookie_name,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=False,
        samesite=settings.auth_cookie_samesite,
    )


def _session_response(
    session: Session,
    identity: AuthenticatedIdentity,
    *,
    expires_at: datetime | None = None,
) -> SessionResponse:
    memberships = list_memberships(session, identity.user_id)
    session_row = session.get(AuthSessionORM, identity.session_id)
    return SessionResponse(
        user=UserResponse(
            id=identity.user_id,
            email=identity.email,
            display_name=identity.display_name,
        ),
        current_workspace_id=identity.current_workspace_id,
        workspaces=[
            WorkspaceMembershipResponse(
                id=workspace.id,
                name=workspace.name,
                role=membership.role,
                is_demo=workspace.is_demo,
            )
            for membership, workspace in memberships
        ],
        expires_at=expires_at or (session_row.expires_at if session_row is not None else None),
    )


@router.post(
    "/signup",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_dependency("authentication"))],
)
def signup(
    request: SignUpRequest,
    http_request: Request,
    response: Response,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> SessionResponse:
    try:
        issued = register_user(
            session,
            email=request.email,
            password=request.password,
            display_name=request.display_name,
            workspace_name=request.workspace_name,
            duration_hours=settings.auth_session_hours,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    revoke_session_token(session, http_request.cookies.get(settings.auth_session_cookie_name))
    _set_session_cookies(response, issued, settings)
    return _session_response(session, issued.identity, expires_at=issued.expires_at)


@router.post(
    "/signin",
    response_model=SessionResponse,
    dependencies=[Depends(rate_limit_dependency("authentication"))],
)
def signin(
    request: SignInRequest,
    http_request: Request,
    response: Response,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> SessionResponse:
    try:
        issued = sign_in(session, request.email, request.password, settings.auth_session_hours)
    except (AuthenticationError, AuthorizationError) as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    revoke_session_token(session, http_request.cookies.get(settings.auth_session_cookie_name))
    _set_session_cookies(response, issued, settings)
    return _session_response(session, issued.identity, expires_at=issued.expires_at)


@router.get("/session", response_model=SessionResponse)
def current_session(
    identity: AuthenticatedIdentity = Depends(get_authenticated_identity),
    session: Session = Depends(get_db),
) -> SessionResponse:
    try:
        require_membership(session, identity.user_id, identity.current_workspace_id)
    except AuthorizationError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    return _session_response(session, identity)


@router.get("/status", response_model=SessionResponse | None)
def session_status(
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> SessionResponse | None:
    token = request.cookies.get(settings.auth_session_cookie_name)
    if not token:
        return None
    try:
        identity = authenticate_session(session, token)
    except AuthenticationError:
        _clear_session_cookies(response, settings)
        return None
    try:
        require_membership(session, identity.user_id, identity.current_workspace_id)
    except AuthorizationError:
        _clear_session_cookies(response, settings)
        return None
    return _session_response(session, identity)


@router.post("/workspace", response_model=SessionResponse)
def change_workspace(
    request: WorkspaceSelectionRequest,
    identity: AuthenticatedIdentity = Depends(get_csrf_authenticated_identity),
    session: Session = Depends(get_db),
) -> SessionResponse:
    try:
        select_workspace(session, identity, request.workspace_id)
    except AuthorizationError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    updated = AuthenticatedIdentity(
        user_id=identity.user_id,
        email=identity.email,
        display_name=identity.display_name,
        session_id=identity.session_id,
        current_workspace_id=request.workspace_id,
    )
    return _session_response(session, updated)


@router.post("/signout", status_code=status.HTTP_204_NO_CONTENT)
def signout(
    response: Response,
    identity: AuthenticatedIdentity = Depends(get_csrf_authenticated_identity),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> Response:
    revoke_session(session, identity)
    _clear_session_cookies(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/development-session",
    response_model=SessionResponse,
    dependencies=[Depends(rate_limit_dependency("authentication"))],
)
def development_session(
    response: Response,
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> SessionResponse:
    if not (
        settings.auth_development_fallback
        and settings.app_env.lower() in {"development", "test", "local"}
    ):
        raise HTTPException(status_code=404, detail="not found")
    requested = request.headers.get("X-Workspace-ID")
    try:
        workspace_id = UUID(requested) if requested else DEMO_WORKSPACE_ID
    except ValueError as error:
        raise HTTPException(status_code=422, detail="X-Workspace-ID must be a UUID") from error
    if workspace_id not in {DEFAULT_WORKSPACE_ID, DEMO_WORKSPACE_ID}:
        raise HTTPException(status_code=403, detail="development sessions use reserved workspaces")
    workspace = ensure_workspace(
        session,
        workspace_id,
        name="Demo Revenue Workspace" if workspace_id == DEMO_WORKSPACE_ID else "Default Workspace",
        is_demo=workspace_id == DEMO_WORKSPACE_ID,
    )
    user = session.scalar(select(UserORM).where(UserORM.email == "developer@localhost.invalid"))
    now = datetime.now(UTC)
    if user is None:
        user = UserORM(
            id=uuid4(),
            email="developer@localhost.invalid",
            display_name="Local Developer",
            password_hash=hash_password(uuid4().hex),
            status="active",
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        session.flush()
    membership = session.scalar(
        select(WorkspaceMembershipORM).where(
            WorkspaceMembershipORM.user_id == user.id,
            WorkspaceMembershipORM.workspace_id == workspace.id,
        )
    )
    if membership is None:
        session.add(
            WorkspaceMembershipORM(
                id=uuid4(),
                workspace_id=workspace.id,
                user_id=user.id,
                role="owner",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    issued = issue_session(
        session,
        user,
        workspace.id,
        duration_hours=settings.auth_session_hours,
    )
    _set_session_cookies(response, issued, settings)
    return _session_response(session, issued.identity, expires_at=issued.expires_at)
