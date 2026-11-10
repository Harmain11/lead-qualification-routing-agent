"""Canonical lead ownership, team assignment, sharing, and transfer history."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from lead_agent.domain.rbac import (
    AccessScope,
    effective_access_scope,
    effective_permissions,
    normalize_role,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import (
    LeadORM,
    UserORM,
    WorkspaceAdminAuditEventORM,
    WorkspaceMembershipORM,
)
from lead_agent.infrastructure.db.teams import (
    LeadAccessGrantORM,
    LeadAssignmentHistoryORM,
    WorkspaceTeamLeadORM,
    WorkspaceTeamMemberORM,
    WorkspaceTeamORM,
)


def _active_member_for_user(
    session: Session, workspace_id: UUID, user_id: UUID,
) -> WorkspaceMembershipORM:
    member = session.scalar(select(WorkspaceMembershipORM).where(
        WorkspaceMembershipORM.workspace_id == workspace_id,
        WorkspaceMembershipORM.user_id == user_id,
        WorkspaceMembershipORM.status == "active",
    ))
    user = session.get(UserORM, user_id)
    if member is None or user is None or user.status != "active":
        raise HTTPException(422, "Choose an active workspace member.")
    if "leads.edit" not in effective_permissions(member.role, member.permission_overrides):
        raise HTTPException(422, "Choose a workspace member with lead-edit access.")
    return member


def _active_grant(
    session: Session, lead: LeadORM, membership_id: UUID | None,
) -> LeadAccessGrantORM | None:
    if membership_id is None:
        return None
    grant = session.get(LeadAccessGrantORM, (lead.id, membership_id))
    if grant is None or grant.workspace_id != lead.workspace_id:
        return None
    expires_at = grant.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            return None
    return grant


def assert_lead_action(
    session: Session,
    workspace: WorkspaceContext,
    lead: LeadORM,
    action: str,
) -> None:
    """Authorize an action against the canonical record scope and explicit share mode."""
    if lead.workspace_id != workspace.workspace_id:
        raise HTTPException(404, "Lead not found")
    permission = f"leads.{action}"
    if permission not in workspace.permissions:
        raise HTTPException(403, f"permission required: {permission}")
    role = normalize_role(workspace.role)
    if role in {"owner", "admin"}:
        return
    scope_permission = "leads.view" if action == "view" else "leads.edit"
    scope = effective_access_scope(role, scope_permission, workspace.access_policy)
    if scope == AccessScope.ALL:
        return
    if scope == AccessScope.OWN and workspace.user_id == lead.current_owner_id:
        return
    if scope == AccessScope.TEAM and workspace.membership_id and lead.primary_team_id:
        manager_link = session.get(
            WorkspaceTeamMemberORM, (lead.primary_team_id, workspace.membership_id)
        )
        if manager_link is not None and manager_link.is_manager:
            return
    grant = _active_grant(session, lead, workspace.membership_id)
    if grant is not None and (action == "view" or grant.can_edit):
        return
    raise HTTPException(403, f"lead {action} is outside your authorized scope")


def _member_team_ids(session: Session, member: WorkspaceMembershipORM) -> list[UUID]:
    rows = session.execute(
        select(WorkspaceTeamMemberORM.team_id, WorkspaceTeamORM.is_default)
        .join(WorkspaceTeamORM, WorkspaceTeamORM.id == WorkspaceTeamMemberORM.team_id)
        .where(
            WorkspaceTeamMemberORM.workspace_id == member.workspace_id,
            WorkspaceTeamMemberORM.membership_id == member.id,
        )
        .order_by(WorkspaceTeamORM.is_default, WorkspaceTeamORM.created_at)
    ).all()
    return [team_id for team_id, _is_default in rows]


def _authorize_transfer_team(
    session: Session, actor: WorkspaceContext | None, team_id: UUID | None,
) -> None:
    if actor is None or team_id is None or normalize_role(actor.role) in {"owner", "admin"}:
        return
    if normalize_role(actor.role) != "sales_manager" or actor.membership_id is None:
        raise HTTPException(403, "lead reassignment is outside your authorized scope")
    allowed = session.get(WorkspaceTeamMemberORM, (team_id, actor.membership_id))
    if allowed is None or not allowed.is_manager:
        raise HTTPException(403, "the destination team is outside your managed teams")


def assign_lead(
    session: Session,
    lead: LeadORM,
    new_owner_id: UUID | None,
    *,
    actor: WorkspaceContext | None = None,
    reason: str = "assignment",
) -> WorkspaceMembershipORM | None:
    """Atomically change canonical ownership and the access-granting primary team."""
    previous_owner = lead.current_owner_id
    previous_team = lead.primary_team_id
    member = None
    new_team = previous_team
    if new_team is None:
        new_team = session.scalar(select(WorkspaceTeamLeadORM.team_id).where(
            WorkspaceTeamLeadORM.workspace_id == lead.workspace_id,
            WorkspaceTeamLeadORM.lead_id == lead.id,
        ).order_by(WorkspaceTeamLeadORM.created_at).limit(1))
    if new_owner_id is not None:
        member = _active_member_for_user(session, lead.workspace_id, new_owner_id)
        team_ids = _member_team_ids(session, member)
        if not team_ids:
            raise HTTPException(422, "the assignee must belong to an authorized team")
        new_team = previous_team if previous_team in team_ids else team_ids[0]
    _authorize_transfer_team(session, actor, new_team)

    if previous_owner == new_owner_id and previous_team == new_team:
        return member

    lead.current_owner_id = new_owner_id
    lead.primary_team_id = new_team
    lead.updated_at = datetime.now(UTC)
    # A lead has one current primary team. Removing old links immediately revokes
    # team-derived visibility while explicit collaboration grants remain intact.
    session.execute(delete(WorkspaceTeamLeadORM).where(
        WorkspaceTeamLeadORM.workspace_id == lead.workspace_id,
        WorkspaceTeamLeadORM.lead_id == lead.id,
    ))
    if new_team is not None:
        session.add(WorkspaceTeamLeadORM(
            workspace_id=lead.workspace_id,
            team_id=new_team,
            lead_id=lead.id,
            created_at=datetime.now(UTC),
        ))
    session.add(LeadAssignmentHistoryORM(
        id=uuid4(),
        workspace_id=lead.workspace_id,
        lead_id=lead.id,
        previous_owner_id=previous_owner,
        new_owner_id=new_owner_id,
        previous_team_id=previous_team,
        new_team_id=new_team,
        actor_id=actor.user_id if actor else None,
        reason=reason[:255],
        details={"source": "manual" if actor else "routing"},
        created_at=datetime.now(UTC),
    ))
    session.add(WorkspaceAdminAuditEventORM(
        id=uuid4(),
        workspace_id=lead.workspace_id,
        actor_id=actor.user_id if actor else None,
        target_membership_id=member.id if member else None,
        operation="lead_reassigned",
        before={
            "lead_id": str(lead.id),
            "owner_id": str(previous_owner) if previous_owner else None,
            "team_id": str(previous_team) if previous_team else None,
        },
        after={"lead_id": str(lead.id), "owner_id": str(new_owner_id) if new_owner_id else None,
               "team_id": str(new_team) if new_team else None},
        created_at=datetime.now(UTC),
    ))
    return member


def share_lead(
    session: Session,
    lead: LeadORM,
    membership: WorkspaceMembershipORM,
    *,
    actor_id: UUID,
    can_edit: bool = False,
    reason: str = "collaboration",
) -> LeadAccessGrantORM:
    if membership.workspace_id != lead.workspace_id or membership.status != "active":
        raise HTTPException(404, "workspace member not found")
    grant = session.get(LeadAccessGrantORM, (lead.id, membership.id))
    if grant is None:
        grant = LeadAccessGrantORM(
            lead_id=lead.id,
            membership_id=membership.id,
            workspace_id=lead.workspace_id,
            created_at=datetime.now(UTC),
        )
        session.add(grant)
    grant.can_edit = can_edit
    grant.granted_by = actor_id
    grant.reason = reason[:255]
    grant.expires_at = None
    session.add(WorkspaceAdminAuditEventORM(
        id=uuid4(),
        workspace_id=lead.workspace_id,
        actor_id=actor_id,
        target_membership_id=membership.id,
        operation="lead_access_granted",
        before={},
        after={"lead_id": str(lead.id), "can_edit": can_edit, "reason": grant.reason},
        created_at=datetime.now(UTC),
    ))
    return grant


def revoke_lead_share(
    session: Session,
    lead: LeadORM,
    membership_id: UUID,
    *,
    actor_id: UUID,
) -> None:
    grant = session.get(LeadAccessGrantORM, (lead.id, membership_id))
    if grant is None or grant.workspace_id != lead.workspace_id:
        raise HTTPException(404, "lead share not found")
    session.delete(grant)
    session.add(WorkspaceAdminAuditEventORM(
        id=uuid4(),
        workspace_id=lead.workspace_id,
        actor_id=actor_id,
        target_membership_id=membership_id,
        operation="lead_access_revoked",
        before={"lead_id": str(lead.id), "can_edit": grant.can_edit},
        after={"lead_id": str(lead.id)},
        created_at=datetime.now(UTC),
    ))
