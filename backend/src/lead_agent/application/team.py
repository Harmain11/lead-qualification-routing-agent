from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.domain.rbac import effective_override_metadata, normalize_role
from lead_agent.infrastructure.db.models import (
    UserORM,
    WorkspaceAdminAuditEventORM,
    WorkspaceInvitationORM,
    WorkspaceMembershipORM,
)

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
INVITATION_TTL = timedelta(days=7)


def _normalize_email(email: str) -> str:
    value = email.strip().lower()
    if not _EMAIL.match(value):
        raise ValueError("a valid invitation email is required")
    return value


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _membership_snapshot(row: WorkspaceMembershipORM | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "membership_id": str(row.id),
        "user_id": str(row.user_id),
        "role": normalize_role(row.role),
        "status": row.status,
        "permission_overrides": effective_override_metadata(row.permission_overrides),
    }


def _audit(
    session: Session,
    workspace_id: UUID,
    actor_id: UUID | None,
    operation: str,
    *,
    target_membership_id: UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> WorkspaceAdminAuditEventORM:
    row = WorkspaceAdminAuditEventORM(
        id=uuid4(),
        workspace_id=workspace_id,
        actor_id=actor_id,
        target_membership_id=target_membership_id,
        operation=operation,
        before=before or {},
        after=after or {},
        created_at=datetime.now(UTC),
    )
    session.add(row)
    return row


def list_workspace_members(
    session: Session, workspace_id: UUID
) -> list[tuple[WorkspaceMembershipORM, UserORM]]:
    rows = session.execute(
        select(WorkspaceMembershipORM, UserORM)
        .join(UserORM, UserORM.id == WorkspaceMembershipORM.user_id)
        .where(WorkspaceMembershipORM.workspace_id == workspace_id)
        .order_by(UserORM.email)
    )
    return [(row[0], row[1]) for row in rows]


def get_membership(
    session: Session, workspace_id: UUID, membership_id: UUID
) -> WorkspaceMembershipORM | None:
    return session.scalar(
        select(WorkspaceMembershipORM).where(
            WorkspaceMembershipORM.id == membership_id,
            WorkspaceMembershipORM.workspace_id == workspace_id,
        )
    )


def get_membership_for_user(
    session: Session, workspace_id: UUID, user_id: UUID
) -> WorkspaceMembershipORM | None:
    return session.scalar(
        select(WorkspaceMembershipORM).where(
            WorkspaceMembershipORM.workspace_id == workspace_id,
            WorkspaceMembershipORM.user_id == user_id,
        )
    )


def list_workspace_invitations(
    session: Session, workspace_id: UUID
) -> list[WorkspaceInvitationORM]:
    return list(
        session.scalars(
            select(WorkspaceInvitationORM)
            .where(WorkspaceInvitationORM.workspace_id == workspace_id)
            .order_by(WorkspaceInvitationORM.created_at.desc())
        )
    )


def get_invitation_for_token(
    session: Session, token: str, workspace_id: UUID
) -> WorkspaceInvitationORM | None:
    return session.scalar(
        select(WorkspaceInvitationORM).where(
            WorkspaceInvitationORM.token_hash == _token_hash(token),
            WorkspaceInvitationORM.workspace_id == workspace_id,
        )
    )


def list_admin_audit_events(
    session: Session, workspace_id: UUID
) -> list[WorkspaceAdminAuditEventORM]:
    return list(
        session.scalars(
            select(WorkspaceAdminAuditEventORM)
            .where(WorkspaceAdminAuditEventORM.workspace_id == workspace_id)
            .order_by(WorkspaceAdminAuditEventORM.created_at.desc())
        )
    )


def invite_member(
    session: Session,
    workspace_id: UUID,
    actor_id: UUID,
    email: str,
    role: str,
    permission_overrides: dict[str, Any] | None = None,
) -> tuple[WorkspaceInvitationORM, str]:
    normalized_email = _normalize_email(email)
    canonical_role = normalize_role(role)
    if canonical_role == "owner":
        raise ValueError("ownership transfer is not implemented")
    overrides = effective_override_metadata(permission_overrides)
    membership = session.scalar(
        select(WorkspaceMembershipORM)
        .join(UserORM, UserORM.id == WorkspaceMembershipORM.user_id)
        .where(
            WorkspaceMembershipORM.workspace_id == workspace_id,
            UserORM.email == normalized_email,
        )
    )
    if membership is not None and membership.status in {"active", "suspended"}:
        raise ValueError("user is already a workspace member")
    invitation = session.scalar(
        select(WorkspaceInvitationORM).where(
            WorkspaceInvitationORM.workspace_id == workspace_id,
            WorkspaceInvitationORM.email == normalized_email,
            WorkspaceInvitationORM.status == "pending",
        )
    )
    token = secrets.token_urlsafe(48)
    now = datetime.now(UTC)
    if membership is None:
        existing_user = session.scalar(select(UserORM).where(UserORM.email == normalized_email))
        if existing_user is not None:
            membership = WorkspaceMembershipORM(
                id=uuid4(),
                workspace_id=workspace_id,
                user_id=existing_user.id,
                role=canonical_role,
                status="invited",
                invited_by=actor_id,
                invited_at=now,
                accepted_at=None,
                permission_overrides=overrides,
                created_at=now,
                updated_at=now,
            )
            session.add(membership)
    if membership is not None:
        # Keep an existing user's membership visible as pending while the
        # invitation is outstanding.  A user without an account is represented
        # by the invitation row until they accept.
        membership.status = "invited"
        membership.role = canonical_role
        membership.permission_overrides = overrides
        membership.invited_by = actor_id
        membership.invited_at = now
        membership.accepted_at = None
        membership.updated_at = now
    if invitation is None:
        invitation = WorkspaceInvitationORM(
            id=uuid4(),
            workspace_id=workspace_id,
            email=normalized_email,
            role=canonical_role,
            permission_overrides=overrides,
            token_hash=_token_hash(token),
            status="pending",
            invited_by=actor_id,
            accepted_user_id=None,
            expires_at=now + INVITATION_TTL,
            accepted_at=None,
            revoked_at=None,
            created_at=now,
            updated_at=now,
        )
        session.add(invitation)
        operation = "member_invited"
    else:
        invitation.role = canonical_role
        invitation.permission_overrides = overrides
        invitation.token_hash = _token_hash(token)
        invitation.invited_by = actor_id
        invitation.expires_at = now + INVITATION_TTL
        invitation.revoked_at = None
        invitation.updated_at = now
        operation = "invitation_resent"
    _audit(
        session,
        workspace_id,
        actor_id,
        operation,
        target_membership_id=membership.id if membership else None,
        after={
            "email": normalized_email,
            "role": canonical_role,
            "permission_overrides": overrides,
        },
    )
    session.commit()
    return invitation, token


def resend_invitation(
    session: Session, workspace_id: UUID, invitation_id: UUID, actor_id: UUID
) -> tuple[WorkspaceInvitationORM, str]:
    invitation = session.scalar(
        select(WorkspaceInvitationORM).where(
            WorkspaceInvitationORM.id == invitation_id,
            WorkspaceInvitationORM.workspace_id == workspace_id,
        )
    )
    if invitation is None:
        raise LookupError("invitation not found")
    if invitation.status != "pending":
        raise ValueError(f"invitation is {invitation.status} and cannot be resent")
    token = secrets.token_urlsafe(48)
    invitation.token_hash = _token_hash(token)
    invitation.expires_at = datetime.now(UTC) + INVITATION_TTL
    invitation.invited_by = actor_id
    invitation.updated_at = datetime.now(UTC)
    _audit(
        session,
        workspace_id,
        actor_id,
        "invitation_resent",
        after={"email": invitation.email, "role": invitation.role},
    )
    session.commit()
    return invitation, token


def revoke_invitation(
    session: Session, workspace_id: UUID, invitation_id: UUID, actor_id: UUID
) -> WorkspaceInvitationORM:
    invitation = session.scalar(
        select(WorkspaceInvitationORM).where(
            WorkspaceInvitationORM.id == invitation_id,
            WorkspaceInvitationORM.workspace_id == workspace_id,
        ).with_for_update()
    )
    if invitation is None:
        raise LookupError("invitation not found")
    if invitation.status != "pending":
        raise ValueError("only pending invitations can be revoked")
    invitation.status = "revoked"
    invitation.revoked_at = datetime.now(UTC)
    invitation.updated_at = invitation.revoked_at
    membership = session.scalar(
        select(WorkspaceMembershipORM)
        .join(UserORM, UserORM.id == WorkspaceMembershipORM.user_id)
        .where(
            WorkspaceMembershipORM.workspace_id == workspace_id,
            UserORM.email == invitation.email,
        )
        .with_for_update()
    )
    if membership is not None and membership.status == "invited":
        membership.status = "revoked"
        membership.updated_at = invitation.revoked_at
    _audit(
        session,
        workspace_id,
        actor_id,
        "invitation_revoked",
        target_membership_id=membership.id if membership is not None else None,
        after={"email": invitation.email, "role": invitation.role},
    )
    session.commit()
    session.refresh(invitation)
    return invitation


def accept_invitation(
    session: Session,
    token: str,
    user_id: UUID,
    email: str,
    workspace_id: UUID | None = None,
) -> WorkspaceMembershipORM:
    normalized_email = _normalize_email(email)
    statement = select(WorkspaceInvitationORM).where(
        WorkspaceInvitationORM.token_hash == _token_hash(token),
    )
    if workspace_id is not None:
        statement = statement.where(WorkspaceInvitationORM.workspace_id == workspace_id)
    invitation = session.scalar(statement.with_for_update())
    if invitation is None:
        raise ValueError("invitation is invalid or belongs to another workspace")
    now = datetime.now(UTC)
    if invitation.status != "pending":
        raise ValueError("invitation has already been used or revoked")
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        invitation.status = "expired"
        invitation.updated_at = now
        expired_membership = get_membership_for_user(session, invitation.workspace_id, user_id)
        if expired_membership is not None and expired_membership.status == "invited":
            expired_membership.status = "revoked"
            expired_membership.updated_at = now
        session.commit()
        raise ValueError("invitation has expired")
    if invitation.email != normalized_email:
        raise ValueError("invitation email does not match the authenticated user")
    membership = get_membership_for_user(session, invitation.workspace_id, user_id)
    if membership is not None and membership.status in {"active", "suspended"}:
        raise ValueError("user is already a workspace member")
    if membership is None:
        membership = WorkspaceMembershipORM(
            id=uuid4(),
            workspace_id=invitation.workspace_id,
            user_id=user_id,
            role=invitation.role,
            status="active",
            invited_by=invitation.invited_by,
            invited_at=invitation.created_at,
            accepted_at=now,
            permission_overrides=invitation.permission_overrides,
            created_at=now,
            updated_at=now,
        )
        session.add(membership)
    else:
        membership.role = invitation.role
        membership.status = "active"
        membership.invited_by = invitation.invited_by
        membership.invited_at = invitation.created_at
        membership.accepted_at = now
        membership.permission_overrides = invitation.permission_overrides
        membership.updated_at = now
    invitation.status = "accepted"
    invitation.accepted_user_id = user_id
    invitation.accepted_at = now
    invitation.updated_at = now
    session.flush()
    _audit(
        session,
        invitation.workspace_id,
        user_id,
        "member_joined",
        target_membership_id=membership.id,
        after=_membership_snapshot(membership),
    )
    session.commit()
    session.refresh(membership)
    return membership


def _target_for_change(
    session: Session, workspace_id: UUID, membership_id: UUID, actor_id: UUID
) -> WorkspaceMembershipORM:
    target = get_membership(session, workspace_id, membership_id)
    if target is None:
        raise LookupError("workspace member not found")
    if target.user_id == actor_id:
        raise ValueError("members cannot change their own membership")
    if normalize_role(target.role) == "owner":
        raise ValueError("workspace ownership is protected; ownership transfer is not implemented")
    return target


def change_member_role(
    session: Session,
    workspace_id: UUID,
    membership_id: UUID,
    actor_id: UUID,
    role: str,
) -> WorkspaceMembershipORM:
    target = _target_for_change(session, workspace_id, membership_id, actor_id)
    canonical_role = normalize_role(role)
    if canonical_role == "owner":
        raise ValueError("ownership transfer is not implemented")
    before = _membership_snapshot(target)
    target.role = canonical_role
    target.updated_at = datetime.now(UTC)
    after = _membership_snapshot(target)
    _audit(
        session,
        workspace_id,
        actor_id,
        "role_changed",
        target_membership_id=target.id,
        before=before,
        after=after,
    )
    session.commit()
    session.refresh(target)
    return target


def change_member_permissions(
    session: Session,
    workspace_id: UUID,
    membership_id: UUID,
    actor_id: UUID,
    permission_overrides: dict[str, Any],
) -> WorkspaceMembershipORM:
    target = _target_for_change(session, workspace_id, membership_id, actor_id)
    overrides = effective_override_metadata(permission_overrides)
    before = _membership_snapshot(target)
    target.permission_overrides = overrides
    target.updated_at = datetime.now(UTC)
    after = _membership_snapshot(target)
    _audit(
        session,
        workspace_id,
        actor_id,
        "permissions_changed",
        target_membership_id=target.id,
        before=before,
        after=after,
    )
    session.commit()
    session.refresh(target)
    return target


def set_member_status(
    session: Session,
    workspace_id: UUID,
    membership_id: UUID,
    actor_id: UUID,
    status: str,
) -> WorkspaceMembershipORM:
    if status not in {"active", "suspended", "revoked"}:
        raise ValueError("unsupported membership status")
    target = _target_for_change(session, workspace_id, membership_id, actor_id)
    if status == "revoked" and target.status == "revoked":
        raise ValueError("member is already revoked")
    before = _membership_snapshot(target)
    target.status = status
    target.updated_at = datetime.now(UTC)
    operation = {
        "suspended": "member_suspended",
        "active": "member_reactivated",
        "revoked": "member_removed",
    }[status]
    after = _membership_snapshot(target)
    _audit(
        session,
        workspace_id,
        actor_id,
        operation,
        target_membership_id=target.id,
        before=before,
        after=after,
    )
    session.commit()
    session.refresh(target)
    return target
