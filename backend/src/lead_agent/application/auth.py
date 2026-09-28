from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.domain.auth import AuthenticatedIdentity, AuthenticationError, AuthorizationError
from lead_agent.domain.rbac import effective_permissions, normalize_overrides
from lead_agent.infrastructure.db.models import (
    AuthOAuthStateORM,
    AuthSessionORM,
    EmailVerificationTokenORM,
    ExternalIdentityORM,
    UserORM,
    WorkspaceMembershipORM,
    WorkspaceORM,
)
from lead_agent.infrastructure.email import EmailDeliveryError, EmailProvider, VerificationEmail
from lead_agent.infrastructure.google_auth import GoogleIdentity, new_pkce_verifier

logger = logging.getLogger(__name__)

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 5


@dataclass(frozen=True)
class IssuedSession:
    identity: AuthenticatedIdentity
    token: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True)
class VerificationRequired:
    email: str
    expires_at: datetime


@dataclass(frozen=True)
class GoogleAuthState:
    state: str
    code_verifier: str
    redirect_uri: str
    invitation_token: str | None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def _identity(session_row: AuthSessionORM, user: UserORM) -> AuthenticatedIdentity:
    return AuthenticatedIdentity(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        session_id=session_row.id,
        current_workspace_id=session_row.current_workspace_id,
    )


def issue_session(
    session: Session,
    user: UserORM,
    workspace_id: UUID,
    *,
    duration_hours: int = 336,
) -> IssuedSession:
    now = datetime.now(UTC)
    token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    row = AuthSessionORM(
        id=uuid4(),
        user_id=user.id,
        current_workspace_id=workspace_id,
        token_hash=_digest(token),
        csrf_token_hash=_digest(csrf_token),
        expires_at=now + timedelta(hours=duration_hours),
        last_seen_at=now,
        revoked_at=None,
        created_at=now,
    )
    session.add(row)
    session.commit()
    return IssuedSession(_identity(row, user), token, csrf_token, row.expires_at)


def register_user(
    session: Session,
    *,
    email: str,
    password: str,
    display_name: str,
    workspace_name: str,
    email_provider: EmailProvider,
    verification_base_url: str,
    verification_expiry_minutes: int = 60,
) -> VerificationRequired:
    normalized_email = email.strip().lower()
    if not _EMAIL.match(normalized_email):
        raise ValueError("a valid email address is required")
    if session.scalar(select(UserORM).where(UserORM.email == normalized_email)) is not None:
        # Do not reveal whether an address is registered.
        return VerificationRequired(normalized_email, datetime.now(UTC))
    clean_name = display_name.strip()
    clean_workspace = workspace_name.strip()
    if not clean_name or not clean_workspace:
        raise ValueError("name and workspace name are required")
    now = datetime.now(UTC)
    user = UserORM(
        id=uuid4(),
        email=normalized_email,
        display_name=clean_name,
        password_hash=hash_password(password),
        email_verified=False,
        email_verified_at=None,
        status="pending_email",
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.flush()
    token = secrets.token_urlsafe(48)
    expires_at = now + timedelta(minutes=verification_expiry_minutes)
    session.add(
        EmailVerificationTokenORM(
            id=uuid4(),
            user_id=user.id,
            token_hash=_digest(token),
            workspace_name=clean_workspace,
            expires_at=expires_at,
            consumed_at=None,
            created_at=now,
        )
    )
    session.flush()
    try:
        email_provider.send_verification_email(
            VerificationEmail(
                recipient=normalized_email,
                verification_url=(
                    f"{verification_base_url.rstrip('/')}/?verify_email={token}"
                ),
                expires_at=expires_at.isoformat(),
            )
        )
    except EmailDeliveryError:
        session.rollback()
        raise
    session.commit()
    return VerificationRequired(normalized_email, expires_at)


def sign_in(
    session: Session,
    email: str,
    password: str,
    duration_hours: int = 336,
    *,
    require_email_verified: bool = False,
) -> IssuedSession:
    user = session.scalar(select(UserORM).where(UserORM.email == email.strip().lower()))
    if user is None or user.status != "active" or not verify_password(password, user.password_hash):
        raise AuthenticationError("email or password is incorrect")
    if not user.email_verified:
        raise AuthenticationError("email or password is incorrect")
    membership = session.scalar(
        select(WorkspaceMembershipORM)
        .join(WorkspaceORM, WorkspaceORM.id == WorkspaceMembershipORM.workspace_id)
        .where(
            WorkspaceMembershipORM.user_id == user.id,
            WorkspaceMembershipORM.status == "active",
            WorkspaceORM.status == "active",
        )
        .order_by(WorkspaceMembershipORM.created_at)
    )
    if membership is None:
        raise AuthorizationError("account has no active workspace membership")
    return issue_session(session, user, membership.workspace_id, duration_hours=duration_hours)


def resend_email_verification(
    session: Session,
    *,
    email: str,
    email_provider: EmailProvider,
    verification_base_url: str,
    verification_expiry_minutes: int = 60,
    resend_after_seconds: int = 60,
) -> bool:
    """Queue a fresh token without disclosing whether the address exists."""
    normalized_email = email.strip().lower()
    user = session.scalar(select(UserORM).where(UserORM.email == normalized_email))
    if user is None or user.email_verified or user.status != "pending_email":
        return False
    now = datetime.now(UTC)
    latest = session.scalar(
        select(EmailVerificationTokenORM)
        .where(EmailVerificationTokenORM.user_id == user.id)
        .order_by(EmailVerificationTokenORM.created_at.desc())
    )
    if latest is not None:
        created_at = (
            latest.created_at.replace(tzinfo=UTC)
            if latest.created_at.tzinfo is None
            else latest.created_at
        )
        if created_at + timedelta(seconds=resend_after_seconds) > now:
            return False
        workspace_name = latest.workspace_name
    else:
        return False
    token = secrets.token_urlsafe(48)
    expires_at = now + timedelta(minutes=verification_expiry_minutes)
    session.add(
        EmailVerificationTokenORM(
            id=uuid4(),
            user_id=user.id,
            token_hash=_digest(token),
            workspace_name=workspace_name,
            expires_at=expires_at,
            consumed_at=None,
            created_at=now,
        )
    )
    session.flush()
    try:
        email_provider.send_verification_email(
            VerificationEmail(
                recipient=normalized_email,
                verification_url=f"{verification_base_url.rstrip('/')}/?verify_email={token}",
                expires_at=expires_at.isoformat(),
            )
        )
    except EmailDeliveryError:
        session.rollback()
        raise
    session.commit()
    return True


def verify_email_token(
    session: Session,
    token: str,
    *,
    duration_hours: int = 336,
) -> IssuedSession:
    if not token.strip():
        raise AuthenticationError("email verification link is invalid or expired")
    row = session.scalar(
        select(EmailVerificationTokenORM)
        .where(EmailVerificationTokenORM.token_hash == _digest(token))
        .with_for_update()
    )
    now = datetime.now(UTC)
    expires_at = (
        row.expires_at.replace(tzinfo=UTC)
        if row and row.expires_at.tzinfo is None
        else (row.expires_at if row else None)
    )
    if row is None or row.consumed_at is not None or expires_at is None or expires_at <= now:
        raise AuthenticationError("email verification link is invalid or expired")
    user = session.get(UserORM, row.user_id)
    if user is None or user.status != "pending_email":
        raise AuthenticationError("email verification link is invalid or expired")
    row.consumed_at = now
    user.email_verified = True
    user.email_verified_at = now
    user.status = "active"
    workspace = WorkspaceORM(
        id=uuid4(),
        name=row.workspace_name,
        slug=f"workspace-{uuid4().hex[:16]}",
        status="active",
        is_demo=False,
        created_at=now,
        updated_at=now,
    )
    session.add(workspace)
    session.flush()
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
    logger.info("auth.email_verified", extra={"fields": {"user_id": str(user.id)}})
    return issue_session(session, user, workspace.id, duration_hours=duration_hours)


def authenticate_session(session: Session, token: str) -> AuthenticatedIdentity:
    now = datetime.now(UTC)
    row = session.scalar(
        select(AuthSessionORM).where(
            AuthSessionORM.token_hash == _digest(token),
            AuthSessionORM.revoked_at.is_(None),
            AuthSessionORM.expires_at > now,
        )
    )
    if row is None:
        raise AuthenticationError("session is missing, expired, or invalid")
    user = session.get(UserORM, row.user_id)
    if user is None or user.status != "active":
        raise AuthenticationError("account is not active")
    row.last_seen_at = now
    session.flush()
    return _identity(row, user)


def verify_csrf(session: Session, session_id: UUID, csrf_token: str | None) -> None:
    row = session.get(AuthSessionORM, session_id)
    if (
        row is None
        or not csrf_token
        or not hmac.compare_digest(row.csrf_token_hash, _digest(csrf_token))
    ):
        raise AuthorizationError("CSRF validation failed")


def require_membership(
    session: Session, user_id: UUID, workspace_id: UUID
) -> WorkspaceMembershipORM:
    membership = session.scalar(
        select(WorkspaceMembershipORM)
        .join(WorkspaceORM, WorkspaceORM.id == WorkspaceMembershipORM.workspace_id)
        .where(
            WorkspaceMembershipORM.user_id == user_id,
            WorkspaceMembershipORM.workspace_id == workspace_id,
            WorkspaceMembershipORM.status == "active",
            WorkspaceORM.status == "active",
        )
    )
    if membership is None:
        raise AuthorizationError("workspace membership is required")
    return membership


def membership_permissions(membership: WorkspaceMembershipORM) -> frozenset[str]:
    """Resolve a membership's effective permissions without caching revocable state."""
    try:
        overrides = normalize_overrides(membership.permission_overrides)
    except (AttributeError, TypeError, ValueError):
        overrides = {"grants": [], "denies": []}
    return effective_permissions(membership.role, overrides)


def list_memberships(
    session: Session, user_id: UUID
) -> list[tuple[WorkspaceMembershipORM, WorkspaceORM]]:
    rows = session.execute(
        select(WorkspaceMembershipORM, WorkspaceORM)
        .join(WorkspaceORM, WorkspaceORM.id == WorkspaceMembershipORM.workspace_id)
        .where(
            WorkspaceMembershipORM.user_id == user_id,
            WorkspaceMembershipORM.status == "active",
            WorkspaceORM.status == "active",
        )
        .order_by(WorkspaceMembershipORM.created_at)
    )
    return [(row[0], row[1]) for row in rows]


def select_workspace(session: Session, identity: AuthenticatedIdentity, workspace_id: UUID) -> None:
    require_membership(session, identity.user_id, workspace_id)
    row = session.get(AuthSessionORM, identity.session_id)
    if row is None:
        raise AuthenticationError("session is no longer available")
    row.current_workspace_id = workspace_id
    row.last_seen_at = datetime.now(UTC)
    session.commit()


def revoke_session(session: Session, identity: AuthenticatedIdentity) -> None:
    row = session.get(AuthSessionORM, identity.session_id)
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        session.commit()


def revoke_session_token(session: Session, token: str | None) -> None:
    """Revoke a previously presented opaque session during authentication rotation."""
    if not token:
        return
    row = session.scalar(select(AuthSessionORM).where(AuthSessionORM.token_hash == _digest(token)))
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        session.commit()


def begin_google_auth(
    session: Session,
    *,
    redirect_uri: str,
    invitation_token: str | None = None,
    lifetime_seconds: int = 600,
) -> GoogleAuthState:
    """Create auth-only state. Integration OAuth state is never reused here."""
    now = datetime.now(UTC)
    state = secrets.token_urlsafe(48)
    verifier = new_pkce_verifier()
    session.add(
        AuthOAuthStateORM(
            id=uuid4(),
            provider="google",
            state_hash=_digest(state),
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            invitation_token=invitation_token.strip() if invitation_token else None,
            expires_at=now + timedelta(seconds=lifetime_seconds),
            consumed_at=None,
            created_at=now,
        )
    )
    session.commit()
    return GoogleAuthState(state, verifier, redirect_uri, invitation_token)


def consume_google_auth_state(session: Session, state: str) -> GoogleAuthState:
    if not state.strip():
        raise AuthenticationError("Google authentication state is invalid")
    row = session.scalar(
        select(AuthOAuthStateORM).where(
            AuthOAuthStateORM.provider == "google",
            AuthOAuthStateORM.state_hash == _digest(state),
        ).with_for_update()
    )
    now = datetime.now(UTC)
    expires_at = (
        row.expires_at.replace(tzinfo=UTC)
        if row and row.expires_at.tzinfo is None
        else (row.expires_at if row else None)
    )
    if row is None or row.consumed_at is not None or expires_at is None or expires_at <= now:
        raise AuthenticationError("Google authentication state is invalid or expired")
    row.consumed_at = now
    session.commit()
    return GoogleAuthState(
        state=state,
        code_verifier=row.code_verifier,
        redirect_uri=row.redirect_uri,
        invitation_token=row.invitation_token,
    )


def complete_google_login(
    session: Session,
    identity: GoogleIdentity,
    *,
    invitation_token: str | None = None,
    duration_hours: int = 336,
) -> IssuedSession:
    """Map a verified Google subject to one local user, then issue the normal CRM session."""
    if not identity.email_verified:
        raise AuthenticationError("Google email is not verified")
    normalized_email = identity.email.strip().lower()
    if not _EMAIL.match(normalized_email) or not identity.subject.strip():
        raise AuthenticationError("Google identity is incomplete")
    now = datetime.now(UTC)
    external = session.scalar(
        select(ExternalIdentityORM).where(
            ExternalIdentityORM.provider == "google",
            ExternalIdentityORM.subject == identity.subject,
        )
    )
    user = session.get(UserORM, external.user_id) if external is not None else None
    if user is not None and user.email != normalized_email:
        raise AuthenticationError("Google identity email does not match the linked account")
    if user is None:
        # A verified Google email can safely link to the one exact local account. Domain
        # matching is intentionally never used as an authorization mechanism.
        user = session.scalar(select(UserORM).where(UserORM.email == normalized_email))
    if user is None:
        user = UserORM(
            id=uuid4(),
            email=normalized_email,
            display_name=identity.display_name.strip() or normalized_email.split("@", 1)[0],
            password_hash=hash_password(secrets.token_urlsafe(32)),
            email_verified=True,
            email_verified_at=now,
            status="active",
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        session.flush()
    else:
        user.email_verified = True
        user.email_verified_at = user.email_verified_at or now
        user.updated_at = now
    if external is None:
        session.add(
            ExternalIdentityORM(
                id=uuid4(),
                provider="google",
                subject=identity.subject,
                user_id=user.id,
                email=normalized_email,
                email_verified=True,
                created_at=now,
                last_login_at=now,
            )
        )
    else:
        external.email = normalized_email
        external.email_verified = True
        external.last_login_at = now
    session.flush()

    workspace_id: UUID | None = None
    if invitation_token:
        from lead_agent.application.team import accept_invitation

        membership = accept_invitation(
            session, invitation_token, user.id, normalized_email, workspace_id=None
        )
        workspace_id = membership.workspace_id
    else:
        memberships = list_memberships(session, user.id)
        if not memberships:
            from lead_agent.infrastructure.db.models import WorkspaceORM

            workspace = WorkspaceORM(
                id=uuid4(),
                name=f"{user.display_name}'s Workspace"[:255],
                slug=f"workspace-{uuid4().hex[:16]}",
                status="active",
                is_demo=False,
                created_at=now,
                updated_at=now,
            )
            session.add(workspace)
            session.flush()
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
            workspace_id = workspace.id
        else:
            workspace_id = memberships[0][1].id
    if workspace_id is None:
        raise AuthorizationError("account has no active workspace membership")
    session.commit()
    return issue_session(session, user, workspace_id, duration_hours=duration_hours)
