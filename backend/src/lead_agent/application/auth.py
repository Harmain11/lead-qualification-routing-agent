from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.domain.auth import AuthenticatedIdentity, AuthenticationError, AuthorizationError
from lead_agent.infrastructure.db.models import (
    AuthSessionORM,
    UserORM,
    WorkspaceMembershipORM,
    WorkspaceORM,
)

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


@dataclass(frozen=True)
class IssuedSession:
    identity: AuthenticatedIdentity
    token: str
    csrf_token: str
    expires_at: datetime


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${derived.hex()}"


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
    duration_hours: int = 336,
) -> IssuedSession:
    normalized_email = email.strip().lower()
    if not _EMAIL.match(normalized_email):
        raise ValueError("a valid email address is required")
    if session.scalar(select(UserORM).where(UserORM.email == normalized_email)) is not None:
        raise ValueError("an account already exists for this email")
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
        status="active",
        created_at=now,
        updated_at=now,
    )
    workspace = WorkspaceORM(
        id=uuid4(),
        name=clean_workspace,
        slug=f"workspace-{uuid4().hex[:16]}",
        status="active",
        is_demo=False,
        created_at=now,
        updated_at=now,
    )
    session.add_all([user, workspace])
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
    return issue_session(session, user, workspace.id, duration_hours=duration_hours)


def sign_in(
    session: Session, email: str, password: str, duration_hours: int = 336
) -> IssuedSession:
    user = session.scalar(select(UserORM).where(UserORM.email == email.strip().lower()))
    if user is None or user.status != "active" or not verify_password(password, user.password_hash):
        raise AuthenticationError("email or password is incorrect")
    membership = session.scalar(
        select(WorkspaceMembershipORM)
        .where(
            WorkspaceMembershipORM.user_id == user.id,
            WorkspaceMembershipORM.status == "active",
        )
        .order_by(WorkspaceMembershipORM.created_at)
    )
    if membership is None:
        raise AuthorizationError("account has no active workspace membership")
    return issue_session(
        session, user, membership.workspace_id, duration_hours=duration_hours
    )


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
    if row is None or not csrf_token or not hmac.compare_digest(
        row.csrf_token_hash, _digest(csrf_token)
    ):
        raise AuthorizationError("CSRF validation failed")


def require_membership(
    session: Session, user_id: UUID, workspace_id: UUID
) -> WorkspaceMembershipORM:
    membership = session.scalar(
        select(WorkspaceMembershipORM).where(
            WorkspaceMembershipORM.user_id == user_id,
            WorkspaceMembershipORM.workspace_id == workspace_id,
            WorkspaceMembershipORM.status == "active",
        )
    )
    if membership is None:
        raise AuthorizationError("workspace membership is required")
    return membership


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
