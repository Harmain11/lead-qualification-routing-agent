from __future__ import annotations

import base64
import binascii
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from lead_agent.api.dependencies import (
    get_authenticated_identity,
    get_csrf_authenticated_identity,
    get_runtime_settings,
)
from lead_agent.application.auth import (
    IssuedSession,
    authenticate_session,
    begin_google_auth,
    complete_google_login,
    consume_google_auth_state,
    csrf_token_matches,
    hash_password,
    issue_session,
    list_memberships,
    membership_permissions,
    register_user,
    request_password_reset,
    require_membership,
    resend_email_verification,
    reset_password,
    revoke_session,
    revoke_session_token,
    rotate_csrf_token,
    select_workspace,
    send_password_reset_email,
    sign_in,
    verify_email_token,
)
from lead_agent.application.account_deletion import (
    AccountDeletionBlocked,
    delete_account,
    preview_account_deletion,
)
from lead_agent.domain.auth import AuthenticatedIdentity, AuthenticationError, AuthorizationError
from lead_agent.domain.workspaces import DEFAULT_WORKSPACE_ID, DEMO_WORKSPACE_ID
from lead_agent.infrastructure.db.models import AuthSessionORM, UserORM, WorkspaceMembershipORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.email import EmailDeliveryError, EmailProvider, get_email_provider
from lead_agent.infrastructure.google_auth import GoogleAuthClient, get_google_auth_client
from lead_agent.infrastructure.oauth import pkce_challenge
from lead_agent.infrastructure.observability import current_request_id
from lead_agent.infrastructure.rate_limit import rate_limit_dependency
from lead_agent.infrastructure.repositories import ensure_workspace
from lead_agent.settings.config import Settings

router = APIRouter(prefix="/v1/auth", tags=["authentication"])
_LOGGER = logging.getLogger("lead_agent.api.auth")


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


class WorkspaceAccessCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    code: str = Field(min_length=32, max_length=256)
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=255)
    password: str | None = Field(default=None, min_length=10, max_length=256)


class EmailAddressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)


class EmailVerificationNotice(BaseModel):
    message: str


class PasswordSignupStatus(BaseModel):
    enabled: bool


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
    effective_permissions: list[str]


class SessionResponse(BaseModel):
    user: UserResponse
    current_workspace_id: UUID
    workspaces: list[WorkspaceMembershipResponse]
    expires_at: datetime | None


class AccountProfileResponse(BaseModel):
    id: UUID
    email: str
    display_name: str
    job_title: str | None
    phone: str | None
    bio: str | None
    avatar_data_url: str | None


class AccountProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    job_title: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    bio: str | None = Field(default=None, max_length=1000)
    avatar_data_url: str | None = Field(default=None, max_length=1_500_000)


class DeleteAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_email: str = Field(min_length=3, max_length=320)


class OwnedWorkspaceDeletionPreviewResponse(BaseModel):
    id: UUID
    name: str
    other_active_members: int
    leads: int
    integrations: int
    stored_secrets: int
    source_events: int
    drafts: int
    conversations: int
    messages: int


class AccountDeletionPreviewResponse(BaseModel):
    email: str
    owned_workspaces: list[OwnedWorkspaceDeletionPreviewResponse]
    shared_workspaces: int
    invitations_to_cancel: int
    blockers: list[str]
    can_delete: bool
    recent_sign_in_required: bool


def _account_profile_response(user: UserORM) -> AccountProfileResponse:
    return AccountProfileResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        job_title=user.job_title,
        phone=user.phone,
        bio=user.bio,
        avatar_data_url=user.avatar_data_url,
    )


def _session_is_recent(session_row: AuthSessionORM | None) -> bool:
    if session_row is None:
        return False
    created_at = session_row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    else:
        created_at = created_at.astimezone(UTC)
    return datetime.now(UTC) - created_at <= timedelta(minutes=15)


def _validated_avatar_data_url(value: str | None) -> str | None:
    if value is None:
        return None
    header, separator, encoded = value.partition(",")
    formats = {
        "data:image/jpeg;base64": ("jpeg", b"\xff\xd8\xff"),
        "data:image/png;base64": ("png", b"\x89PNG\r\n\x1a\n"),
        "data:image/webp;base64": ("webp", b"RIFF"),
    }
    image_format = formats.get(header)
    if not separator or image_format is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Profile photo must be a JPEG, PNG, or WebP image.",
        )
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Profile photo data is invalid.",
        ) from error
    if not image_bytes or len(image_bytes) > 1_000_000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Profile photo must be smaller than 1 MB.",
        )
    format_name, signature = image_format
    has_signature = image_bytes.startswith(signature)
    if format_name == "webp":
        has_signature = has_signature and image_bytes[8:12] == b"WEBP"
    if not has_signature:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Profile photo data does not match its image format.",
        )
    return value


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
                effective_permissions=sorted(membership_permissions(membership)),
            )
            for membership, workspace in memberships
        ],
        expires_at=expires_at or (session_row.expires_at if session_row is not None else None),
    )


@router.post(
    "/signup",
    response_model=EmailVerificationNotice,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_dependency("authentication"))],
)
def signup(
    request: SignUpRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
    email_provider: EmailProvider = Depends(get_email_provider),
) -> EmailVerificationNotice:
    if not email_provider.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Password signup is unavailable until email verification delivery is configured."
            ),
        )
    try:
        register_user(
            session,
            email=request.email,
            password=request.password,
            display_name=request.display_name,
            workspace_name=request.workspace_name,
            email_provider=email_provider,
            verification_base_url=settings.frontend_public_base_url,
            verification_expiry_minutes=settings.email_verification_expiry_minutes,
        )
    except EmailDeliveryError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password signup is temporarily unavailable; please try again later.",
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return EmailVerificationNotice(
        message="If the address can be registered, a verification link has been sent."
    )


@router.get("/password-signup/status", response_model=PasswordSignupStatus)
def password_signup_status(
    email_provider: EmailProvider = Depends(get_email_provider),
) -> PasswordSignupStatus:
    return PasswordSignupStatus(enabled=email_provider.configured)


@router.post(
    "/resend-verification",
    response_model=EmailVerificationNotice,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_dependency("credentials"))],
)
def resend_verification(
    request: EmailAddressRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
    email_provider: EmailProvider = Depends(get_email_provider),
) -> EmailVerificationNotice:
    if email_provider.configured:
        try:
            resend_email_verification(
                session,
                email=request.email,
                email_provider=email_provider,
                verification_base_url=settings.frontend_public_base_url,
                verification_expiry_minutes=settings.email_verification_expiry_minutes,
                resend_after_seconds=settings.email_verification_resend_seconds,
            )
        except EmailDeliveryError:
            session.rollback()
    return EmailVerificationNotice(
        message="If the address has a pending account, a verification link will be sent."
    )


class PasswordResetConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=256)
    password: str = Field(min_length=10, max_length=256)


@router.post(
    "/password-reset/request",
    response_model=EmailVerificationNotice,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_dependency("credentials"))],
)
def password_reset_request(
    request: EmailAddressRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
    email_provider: EmailProvider = Depends(get_email_provider),
) -> EmailVerificationNotice:
    if email_provider.configured:
        message = request_password_reset(
            session,
            email=request.email,
            reset_base_url=settings.frontend_public_base_url,
            expiry_minutes=settings.password_reset_expiry_minutes,
            resend_after_seconds=settings.email_verification_resend_seconds,
        )
        if message is not None:
            background.add_task(send_password_reset_email, email_provider, message)
    # Identical response, sent before any mail is handed off, whether or not
    # the account exists or mail is configured.
    return EmailVerificationNotice(
        message="If an account uses that address, a reset link is on its way."
    )


@router.post(
    "/password-reset/confirm",
    response_model=EmailVerificationNotice,
    dependencies=[Depends(rate_limit_dependency("credentials"))],
)
def password_reset_confirm(
    request: PasswordResetConfirmRequest,
    session: Session = Depends(get_db),
) -> EmailVerificationNotice:
    try:
        reset_password(session, request.token, request.password)
    except AuthenticationError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has expired. Request a new one.",
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return EmailVerificationNotice(message="Password updated. Sign in with your new password.")


@router.get("/verify-email", include_in_schema=False)
def verify_email(
    token: str,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> Response:
    try:
        issued = verify_email_token(
            session,
            token,
            duration_hours=settings.auth_session_hours,
        )
    except AuthenticationError:
        return RedirectResponse(
            url=f"{settings.frontend_public_base_url.rstrip('/')}/?email_verification=expired",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    redirect = RedirectResponse(
        url=f"{settings.frontend_public_base_url.rstrip('/')}/?email_verification=verified",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    _set_session_cookies(redirect, issued, settings)
    return redirect


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
        issued = sign_in(
            session,
            request.email,
            request.password,
            settings.auth_session_hours,
            require_email_verified=settings.auth_require_email_verification,
        )
    except (AuthenticationError, AuthorizationError) as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    revoke_session_token(session, http_request.cookies.get(settings.auth_session_cookie_name))
    _set_session_cookies(response, issued, settings)
    return _session_response(session, issued.identity, expires_at=issued.expires_at)


@router.post(
    "/workspace-code",
    response_model=SessionResponse,
    dependencies=[Depends(rate_limit_dependency("authentication"))],
)
def workspace_code_signin(
    request: WorkspaceAccessCodeRequest,
    http_request: Request,
    response: Response,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> SessionResponse:
    """Create a staff session from an admin-issued, one-time workspace code."""
    from lead_agent.application.team import accept_invitation, get_invitation_for_token

    email = request.email.strip().lower()
    invitation = get_invitation_for_token(session, request.code, request.workspace_id)
    now = datetime.now(UTC)
    if invitation is None or invitation.status != "pending" or invitation.email != email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid workspace access code",
        )
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Workspace access code has expired",
        )

    user = session.scalar(select(UserORM).where(UserORM.email == email))
    if user is None:
        if request.password is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Choose a password of at least 10 characters to finish account setup.",
            )
        user = UserORM(
            id=uuid4(),
            email=email,
            display_name=request.display_name.strip(),
            password_hash=hash_password(request.password),
            email_verified=True,
            email_verified_at=now,
            status="active",
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        session.flush()
    elif user.status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This user account is suspended",
        )
    else:
        user.email_verified = True
        user.email_verified_at = user.email_verified_at or now
        if user.status == "pending_email":
            user.status = "active"
        user.updated_at = now

    try:
        accept_invitation(session, request.code, user.id, email, request.workspace_id)
        issued = issue_session(
            session, user, request.workspace_id, duration_hours=settings.auth_session_hours
        )
    except (AuthenticationError, AuthorizationError, ValueError) as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    revoke_session_token(session, http_request.cookies.get(settings.auth_session_cookie_name))
    _set_session_cookies(response, issued, settings)
    return _session_response(session, issued.identity, expires_at=issued.expires_at)


def _google_redirect(settings: Settings, *, error: str | None = None) -> RedirectResponse:
    query = urlencode({"auth_error": error}) if error else "auth=google"
    return RedirectResponse(
        url=f"{settings.frontend_public_base_url.rstrip('/')}/?{query}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _log_google_callback_failure(stage: str, error: Exception) -> None:
    _LOGGER.warning(
        "google.auth.callback_failed",
        extra={
            "fields": {
                "request_id": current_request_id(),
                "stage": stage,
                "error_type": type(error).__name__,
            }
        },
    )


@router.get(
    "/google/start",
    include_in_schema=False,
    dependencies=[Depends(rate_limit_dependency("oauth"))],
)
def google_start(
    invitation_token: str | None = None,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
    google_client: GoogleAuthClient = Depends(get_google_auth_client),
) -> RedirectResponse:
    redirect_uri = (
        f"{settings.integration_public_base_url.rstrip('/')}/v1/auth/google/callback"
    )
    try:
        state = begin_google_auth(
            session,
            redirect_uri=redirect_uri,
            invitation_token=invitation_token,
        )
        authorization_url = google_client.authorization_url(
            state=state.state,
            code_challenge=pkce_challenge(state.code_verifier),
            redirect_uri=state.redirect_uri,
        )
    except ValueError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    # Keep the development authorize endpoint on the same origin so local QA can follow it.
    if settings.oauth_broker == "development" and not settings.app_env == "production":
        authorization_url = (
            f"{settings.integration_public_base_url.rstrip('/')}/v1/auth/google/development-authorize?"
            f"{urlencode({'state': state.state, 'redirect_uri': state.redirect_uri})}"
        )
    return RedirectResponse(authorization_url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/google/development-authorize", include_in_schema=False)
def google_development_authorize(
    state: str,
    settings: Settings = Depends(get_runtime_settings),
) -> RedirectResponse:
    if settings.oauth_broker != "development" or settings.app_env == "production":
        raise HTTPException(status_code=404, detail="not found")
    callback = f"{settings.integration_public_base_url.rstrip('/')}/v1/auth/google/callback"
    return RedirectResponse(
        f"{callback}?{urlencode({'state': state, 'code': 'development-approved'})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get(
    "/google/callback",
    include_in_schema=False,
    dependencies=[Depends(rate_limit_dependency("oauth"))],
)
def google_callback(
    request: Request,
    state: str,
    code: str | None = None,
    error: str | None = None,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
    google_client: GoogleAuthClient = Depends(get_google_auth_client),
) -> Response:
    try:
        auth_state = consume_google_auth_state(session, state)
    except AuthenticationError as exception:
        session.rollback()
        raise HTTPException(
            status_code=400, detail="Google authentication state is invalid"
        ) from exception
    if error or not code:
        return _google_redirect(settings, error="google_authorization_denied")
    stage = "google_identity_exchange"
    try:
        google_identity = google_client.exchange(
            code=code,
            code_verifier=auth_state.code_verifier,
            redirect_uri=auth_state.redirect_uri,
        )
        stage = "local_account_session_creation"
        issued = complete_google_login(
            session,
            google_identity,
            invitation_token=auth_state.invitation_token,
            duration_hours=settings.auth_session_hours,
        )
    except (AuthenticationError, AuthorizationError, ValueError) as exception:
        session.rollback()
        _log_google_callback_failure(stage, exception)
        return _google_redirect(settings, error="google_authentication_failed")
    except Exception as exception:
        session.rollback()
        _log_google_callback_failure(stage, exception)
        return _google_redirect(settings, error="google_authentication_failed")
    revoke_session_token(session, request.cookies.get(settings.auth_session_cookie_name))
    redirect = _google_redirect(settings)
    _set_session_cookies(redirect, issued, settings)
    return redirect


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


@router.get("/profile", response_model=AccountProfileResponse)
def account_profile(
    identity: AuthenticatedIdentity = Depends(get_authenticated_identity),
    session: Session = Depends(get_db),
) -> AccountProfileResponse:
    user = session.get(UserORM, identity.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found.")
    return _account_profile_response(user)


@router.patch("/profile", response_model=AccountProfileResponse)
def update_account_profile(
    request: AccountProfileUpdateRequest,
    identity: AuthenticatedIdentity = Depends(get_csrf_authenticated_identity),
    session: Session = Depends(get_db),
) -> AccountProfileResponse:
    user = session.get(UserORM, identity.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found.")

    changes = request.model_dump(exclude_unset=True)
    normalized: dict[str, str | None] = {}
    if "display_name" in changes:
        value = changes["display_name"]
        clean_name = value.strip() if isinstance(value, str) else ""
        if not clean_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Display name cannot be blank.",
            )
        normalized["display_name"] = clean_name
    for field in ("job_title", "phone", "bio"):
        if field in changes:
            value = changes[field]
            normalized[field] = value.strip() or None if isinstance(value, str) else None
    if "avatar_data_url" in changes:
        normalized["avatar_data_url"] = _validated_avatar_data_url(changes["avatar_data_url"])

    for field, value in normalized.items():
        setattr(user, field, value)
    if normalized:
        user.updated_at = datetime.now(UTC)
        session.commit()
        session.refresh(user)
    return _account_profile_response(user)


@router.get("/account-deletion-preview", response_model=AccountDeletionPreviewResponse)
def account_deletion_preview(
    identity: AuthenticatedIdentity = Depends(get_authenticated_identity),
    session: Session = Depends(get_db),
) -> AccountDeletionPreviewResponse:
    user = session.get(UserORM, identity.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found.")
    preview = preview_account_deletion(session, identity.user_id)
    auth_session = session.get(AuthSessionORM, identity.session_id)
    recent_sign_in_required = not _session_is_recent(auth_session)
    return AccountDeletionPreviewResponse(
        email=user.email,
        owned_workspaces=[
            OwnedWorkspaceDeletionPreviewResponse(**workspace.__dict__)
            for workspace in preview.owned_workspaces
        ],
        shared_workspaces=preview.shared_workspaces,
        invitations_to_cancel=preview.invitations_to_cancel,
        blockers=preview.blockers,
        can_delete=preview.can_delete and not recent_sign_in_required,
        recent_sign_in_required=recent_sign_in_required,
    )


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_account(
    request: DeleteAccountRequest,
    response: Response,
    identity: AuthenticatedIdentity = Depends(get_csrf_authenticated_identity),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> Response:
    auth_session = session.get(AuthSessionORM, identity.session_id)
    if not _session_is_recent(auth_session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="For your security, sign out and sign in again before deleting your account.",
        )
    try:
        delete_account(session, identity.user_id, request.confirmation_email)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    except AccountDeletionBlocked as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    _clear_session_cookies(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


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
    memberships = list_memberships(session, identity.user_id)
    if not memberships:
        _clear_session_cookies(response, settings)
        return None
    if not any(workspace.id == identity.current_workspace_id for _, workspace in memberships):
        fallback_workspace_id = memberships[0][1].id
        try:
            select_workspace(session, identity, fallback_workspace_id)
        except AuthorizationError:
            _clear_session_cookies(response, settings)
            return None
        identity = AuthenticatedIdentity(
            user_id=identity.user_id,
            email=identity.email,
            display_name=identity.display_name,
            session_id=identity.session_id,
            current_workspace_id=fallback_workspace_id,
        )
    session_row = session.get(AuthSessionORM, identity.session_id)
    if session_row is not None and not csrf_token_matches(
        session, identity.session_id, request.cookies.get(settings.auth_csrf_cookie_name)
    ):
        csrf_token = rotate_csrf_token(session, identity.session_id)
        expires_at = (
            session_row.expires_at.astimezone(UTC)
            if session_row.expires_at.tzinfo
            else session_row.expires_at.replace(tzinfo=UTC)
        )
        response.set_cookie(
            settings.auth_csrf_cookie_name,
            csrf_token,
            max_age=settings.auth_session_hours * 3600,
            expires=expires_at,
            secure=settings.auth_cookie_secure,
            httponly=False,
            samesite=settings.auth_cookie_samesite,
            path="/",
        )
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
            email_verified=True,
            email_verified_at=now,
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
