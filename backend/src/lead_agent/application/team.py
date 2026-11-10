from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from lead_agent.domain.auth import AuthorizationError
from lead_agent.domain.rbac import (
    AccessScope,
    effective_access_scope,
    effective_override_metadata,
    effective_permissions,
    normalize_access_policy,
    normalize_role,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import (
    AuthSessionORM,
    LeadORM,
    UserORM,
    WorkspaceAdminAuditEventORM,
    WorkspaceInvitationORM,
    WorkspaceMembershipORM,
)
from lead_agent.infrastructure.db.teams import (
    ConnectionAccessGrantORM,
    LeadAccessGrantORM,
    WorkspaceTeamMemberORM,
    WorkspaceTeamORM,
)

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
INVITATION_TTL = timedelta(days=7)


def personal_data_user_ids(
    session: Session, workspace: WorkspaceContext,
) -> frozenset[str] | None:
    """Privacy audience: admins all, reps self, managers active teammates, viewers none."""
    role = normalize_role(workspace.role)
    if role in {"owner", "admin"}:
        return None
    if workspace.user_id is None or role == "viewer":
        return frozenset()
    if role != "sales_manager":
        return frozenset({str(workspace.user_id)})
    links = WorkspaceTeamMemberORM.__table__
    own_team_rows = session.execute(
        select(
            WorkspaceTeamMemberORM.team_id,
            WorkspaceTeamORM.is_default,
            WorkspaceTeamMemberORM.is_manager,
        )
        .join(WorkspaceTeamORM, WorkspaceTeamORM.id == WorkspaceTeamMemberORM.team_id)
        .where(
            WorkspaceTeamMemberORM.workspace_id == workspace.workspace_id,
            WorkspaceTeamMemberORM.membership_id == workspace.membership_id,
        )
    ).all()
    explicit_team_ids = [
        team_id
        for team_id, is_default, is_manager in own_team_rows
        if not is_default and is_manager
    ]
    if not explicit_team_ids:
        return frozenset({str(workspace.user_id)})
    own_teams = select(links.c.team_id).where(
        links.c.workspace_id == workspace.workspace_id,
        links.c.membership_id == workspace.membership_id,
        links.c.is_manager.is_(True),
        links.c.team_id.in_(explicit_team_ids),
    )
    user_ids = session.scalars(
        select(WorkspaceMembershipORM.user_id).join(
            WorkspaceTeamMemberORM,
            WorkspaceTeamMemberORM.membership_id == WorkspaceMembershipORM.id,
        ).where(
            WorkspaceMembershipORM.workspace_id == workspace.workspace_id,
            WorkspaceMembershipORM.status == "active",
            WorkspaceTeamMemberORM.workspace_id == workspace.workspace_id,
            WorkspaceTeamMemberORM.team_id.in_(own_teams),
        )
    )
    return frozenset({str(workspace.user_id), *(str(user_id) for user_id in user_ids)})


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
        "access_policy": normalize_access_policy(row.access_policy),
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


def _actor_permissions(
    session: Session, workspace_id: UUID, actor_id: UUID, permission: str
) -> frozenset[str]:
    actor = get_membership_for_user(session, workspace_id, actor_id)
    user = session.get(UserORM, actor_id)
    if actor is None or actor.status != "active" or user is None or user.status != "active":
        raise AuthorizationError("an active workspace membership is required")
    permissions = effective_permissions(actor.role, actor.permission_overrides)
    if permission not in permissions:
        raise AuthorizationError(f"permission required: {permission}")
    return permissions


def _assert_delegable(
    permissions: frozenset[str], role: str, overrides: dict[str, Any] | None
) -> None:
    if normalize_role(role) == "owner":
        raise ValueError("workspace ownership is protected; ownership transfer is not implemented")
    if not effective_permissions(role, overrides) <= permissions:
        raise AuthorizationError("members cannot delegate or manage permissions they do not hold")


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
    access_policy: dict[str, Any] | None = None,
) -> tuple[WorkspaceInvitationORM, str]:
    normalized_email = _normalize_email(email)
    canonical_role = normalize_role(role)
    if canonical_role == "owner":
        raise ValueError("ownership transfer is not implemented")
    overrides = effective_override_metadata(permission_overrides)
    normalized_policy = normalize_access_policy(access_policy)
    permissions = _actor_permissions(session, workspace_id, actor_id, "members.invite")
    _assert_delegable(permissions, canonical_role, overrides)
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
                access_policy=normalized_policy,
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
        membership.access_policy = normalized_policy
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
            access_policy=normalized_policy,
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
        invitation.access_policy = normalized_policy
        invitation.token_hash = _token_hash(token)
        invitation.invited_by = actor_id
        invitation.expires_at = now + INVITATION_TTL
        invitation.revoked_at = None
        invitation.updated_at = now
        operation = "invitation_resent"
    # Audit rows reference the membership by ID, without an ORM relationship
    # that orders their inserts. Persist pending rows inside this transaction
    # before adding the audit entry so its foreign key can be satisfied.
    session.flush()
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
            "access_policy": normalized_policy,
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
    permissions = _actor_permissions(session, workspace_id, actor_id, "members.invite")
    _assert_delegable(permissions, invitation.role, invitation.permission_overrides)
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
    permissions = _actor_permissions(session, workspace_id, actor_id, "members.invite")
    _assert_delegable(permissions, invitation.role, invitation.permission_overrides)
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
            access_policy=invitation.access_policy,
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
        membership.access_policy = invitation.access_policy
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
    permissions = _actor_permissions(session, workspace_id, actor_id, "members.manage")
    _assert_delegable(permissions, target.role, target.permission_overrides)
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
    permissions = _actor_permissions(session, workspace_id, actor_id, "members.manage")
    _assert_delegable(permissions, canonical_role, target.permission_overrides)
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
    permissions = _actor_permissions(session, workspace_id, actor_id, "members.manage")
    _assert_delegable(permissions, target.role, overrides)
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


def change_member_access_policy(
    session: Session,
    workspace_id: UUID,
    membership_id: UUID,
    actor_id: UUID,
    access_policy: dict[str, Any],
) -> WorkspaceMembershipORM:
    target = _target_for_change(session, workspace_id, membership_id, actor_id)
    policy = normalize_access_policy(access_policy)
    actor = get_membership_for_user(session, workspace_id, actor_id)
    if actor is None:
        raise AuthorizationError("an active workspace membership is required")
    actor_permissions = effective_permissions(actor.role, actor.permission_overrides)
    scope_rank = {
        AccessScope.NONE: 0,
        AccessScope.OWN: 1,
        AccessScope.TEAM: 2,
        AccessScope.ALL: 3,
    }
    for permission, raw_scope in policy["scopes"].items():
        requested_scope = AccessScope(raw_scope)
        actor_scope = effective_access_scope(actor.role, permission, actor.access_policy)
        if requested_scope == AccessScope.NONE:
            continue
        if permission not in actor_permissions:
            raise AuthorizationError(
                f"cannot delegate {permission} without holding that permission"
            )
        if actor_scope == AccessScope.ALL:
            continue
        if requested_scope == AccessScope.CUSTOM:
            if actor_scope != AccessScope.CUSTOM:
                raise AuthorizationError(
                    f"cannot delegate a broader {permission} scope than the actor holds"
                )
            continue
        if (
            actor_scope == AccessScope.CUSTOM
            or scope_rank[requested_scope] > scope_rank[actor_scope]
        ):
            raise AuthorizationError(
                f"cannot delegate a broader {permission} scope than the actor holds"
            )
    before = _membership_snapshot(target)
    target.access_policy = policy
    target.updated_at = datetime.now(UTC)
    _audit(
        session,
        workspace_id,
        actor_id,
        "access_policy_changed",
        target_membership_id=target.id,
        before=before,
        after=_membership_snapshot(target),
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
    if status in {"suspended", "revoked"}:
        # Remove a departed member from the live ownership path immediately.
        # Prefer the least-loaded active teammate; otherwise retain the team and
        # leave the record deliberately unassigned for an authorized manager.
        from lead_agent.application.lead_access import assign_lead

        owned_leads = list(
            session.scalars(
                select(LeadORM)
                .where(
                    LeadORM.workspace_id == workspace_id,
                    LeadORM.current_owner_id == target.user_id,
                )
                .order_by(LeadORM.received_at.asc())
            )
        )
        for lead in owned_leads:
            replacement = None
            if lead.primary_team_id is not None:
                candidates = list(
                    session.scalars(
                        select(WorkspaceMembershipORM)
                        .join(
                            WorkspaceTeamMemberORM,
                            WorkspaceTeamMemberORM.membership_id
                            == WorkspaceMembershipORM.id,
                        )
                        .where(
                            WorkspaceMembershipORM.workspace_id == workspace_id,
                            WorkspaceMembershipORM.status == "active",
                            WorkspaceMembershipORM.id != target.id,
                            WorkspaceTeamMemberORM.workspace_id == workspace_id,
                            WorkspaceTeamMemberORM.team_id == lead.primary_team_id,
                        )
                    )
                )
                if candidates:
                    replacement = min(
                        candidates,
                        key=lambda member: session.scalar(
                            select(func.count())
                            .select_from(LeadORM)
                            .where(
                                LeadORM.workspace_id == workspace_id,
                                LeadORM.current_owner_id == member.user_id,
                            )
                        )
                        or 0,
                    )
            assign_lead(
                session,
                lead,
                replacement.user_id if replacement is not None else None,
                reason=(
                    f"automatic reassignment after member {status}"
                    if replacement is not None
                    else f"owner cleared after member {status}"
                ),
            )
    target.status = status
    target.updated_at = datetime.now(UTC)
    if status in {"suspended", "revoked"}:
        session.execute(
            update(AuthSessionORM)
            .where(
                AuthSessionORM.user_id == target.user_id,
                AuthSessionORM.current_workspace_id == workspace_id,
                AuthSessionORM.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        session.execute(
            delete(LeadAccessGrantORM).where(
                LeadAccessGrantORM.workspace_id == workspace_id,
                LeadAccessGrantORM.membership_id == target.id,
            )
        )
        session.execute(
            delete(ConnectionAccessGrantORM).where(
                ConnectionAccessGrantORM.workspace_id == workspace_id,
                ConnectionAccessGrantORM.membership_id == target.id,
            )
        )
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
