from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import (
    get_csrf_authenticated_identity,
    require_permission,
)
from lead_agent.application.team import (
    accept_invitation,
    change_member_permissions,
    change_member_role,
    invite_member,
    list_admin_audit_events,
    list_workspace_invitations,
    list_workspace_members,
    resend_invitation,
    revoke_invitation,
    set_member_status,
)
from lead_agent.domain.auth import AuthenticatedIdentity
from lead_agent.domain.rbac import (
    ROLE_LABELS,
    effective_override_metadata,
    effective_permissions,
    normalize_role,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import (
    UserORM,
    WorkspaceAdminAuditEventORM,
    WorkspaceInvitationORM,
)
from lead_agent.infrastructure.db.session import get_db

router = APIRouter(tags=["workspace-team"])


class PermissionOverridesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grants: list[str] = Field(default_factory=list)
    denies: list[str] = Field(default_factory=list)


class InviteMemberRequest(PermissionOverridesRequest):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(min_length=1, max_length=32)


class RoleChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=32)


class AcceptInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=32, max_length=256)
    workspace_id: UUID | None = None


class MemberResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    user_id: UUID
    email: str
    display_name: str
    role: str
    role_label: str
    status: str
    permission_overrides: dict[str, list[str]]
    effective_permissions: list[str]
    invited_by: UUID | None
    invited_at: datetime | None
    accepted_at: datetime | None
    updated_at: datetime


class InvitationResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    email: str
    role: str
    role_label: str
    permission_overrides: dict[str, list[str]]
    status: str
    invited_by: UUID
    accepted_user_id: UUID | None
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AuditEventResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    actor_id: UUID | None
    target_membership_id: UUID | None
    operation: str
    before: dict[str, Any]
    after: dict[str, Any]
    created_at: datetime


def _member_response(row: Any, user: Any | None = None) -> MemberResponse:
    user_id = row.user_id
    email = user.email if user is not None else ""
    display_name = user.display_name if user is not None else ""
    role = normalize_role(row.role)
    overrides = effective_override_metadata(row.permission_overrides)
    return MemberResponse(
        id=row.id,
        workspace_id=row.workspace_id,
        user_id=user_id,
        email=email,
        display_name=display_name,
        role=role,
        role_label=ROLE_LABELS[role],
        status=row.status,
        permission_overrides=overrides,
        effective_permissions=sorted(effective_permissions(role, overrides)),
        invited_by=row.invited_by,
        invited_at=row.invited_at,
        accepted_at=row.accepted_at,
        updated_at=row.updated_at,
    )


def _invitation_response(row: WorkspaceInvitationORM) -> InvitationResponse:
    return InvitationResponse(
        id=row.id,
        workspace_id=row.workspace_id,
        email=row.email,
        role=row.role,
        role_label=ROLE_LABELS.get(row.role, row.role),
        permission_overrides=effective_override_metadata(row.permission_overrides),
        status=row.status,
        invited_by=row.invited_by,
        accepted_user_id=row.accepted_user_id,
        expires_at=row.expires_at,
        accepted_at=row.accepted_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _audit_response(row: WorkspaceAdminAuditEventORM) -> AuditEventResponse:
    return AuditEventResponse(
        id=row.id,
        workspace_id=row.workspace_id,
        actor_id=row.actor_id,
        target_membership_id=row.target_membership_id,
        operation=row.operation,
        before=row.before,
        after=row.after,
        created_at=row.created_at,
    )


def _actor(workspace: WorkspaceContext) -> UUID:
    if workspace.user_id is None:
        raise HTTPException(status_code=403, detail="authenticated workspace actor required")
    return workspace.user_id


@router.get(
    "/v1/members",
    response_model=list[MemberResponse],
)
def list_members(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("members.view")),
) -> list[MemberResponse]:
    return [
        _member_response(row, user)
        for row, user in list_workspace_members(session, workspace.workspace_id)
    ]


@router.get(
    "/v1/members/audit-events",
    response_model=list[AuditEventResponse],
)
def audit_events(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("members.view")),
) -> list[AuditEventResponse]:
    return [
        _audit_response(row)
        for row in list_admin_audit_events(session, workspace.workspace_id)
    ]


@router.post(
    "/v1/members/invite",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
def invite(
    request: InviteMemberRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("members.invite")),
) -> InvitationResponse:
    try:
        invitation, _token = invite_member(
            session,
            workspace.workspace_id,
            _actor(workspace),
            request.email,
            request.role,
            request.model_dump(include={"grants", "denies"}),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _invitation_response(invitation)


@router.get(
    "/v1/invitations",
    response_model=list[InvitationResponse],
)
def invitations(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("members.view")),
) -> list[InvitationResponse]:
    return [
        _invitation_response(row)
        for row in list_workspace_invitations(session, workspace.workspace_id)
    ]


@router.post(
    "/v1/invitations/{invitation_id}/resend",
    response_model=InvitationResponse,
)
def resend(
    invitation_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("members.manage")),
) -> InvitationResponse:
    try:
        invitation, _token = resend_invitation(
            session, workspace.workspace_id, invitation_id, _actor(workspace)
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _invitation_response(invitation)


@router.post(
    "/v1/invitations/{invitation_id}/revoke",
    response_model=InvitationResponse,
)
def revoke(
    invitation_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("members.manage")),
) -> InvitationResponse:
    try:
        invitation = revoke_invitation(
            session, workspace.workspace_id, invitation_id, _actor(workspace)
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _invitation_response(invitation)


@router.post("/v1/invitations/accept", response_model=MemberResponse)
def accept(
    request: AcceptInvitationRequest,
    identity: AuthenticatedIdentity = Depends(get_csrf_authenticated_identity),
    session: Session = Depends(get_db),
) -> MemberResponse:
    try:
        membership = accept_invitation(
            session,
            request.token,
            identity.user_id,
            identity.email,
            request.workspace_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    user = session.get(UserORM, membership.user_id)
    return _member_response(membership, user)


@router.patch(
    "/v1/members/{membership_id}/role",
    response_model=MemberResponse,
)
def update_role(
    membership_id: UUID,
    request: RoleChangeRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("members.manage")),
) -> MemberResponse:
    try:
        membership = change_member_role(
            session,
            workspace.workspace_id,
            membership_id,
            _actor(workspace),
            request.role,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    user = session.get(UserORM, membership.user_id)
    return _member_response(membership, user)


@router.patch(
    "/v1/members/{membership_id}/permissions",
    response_model=MemberResponse,
)
def update_permissions(
    membership_id: UUID,
    request: PermissionOverridesRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("members.manage")),
) -> MemberResponse:
    try:
        membership = change_member_permissions(
            session,
            workspace.workspace_id,
            membership_id,
            _actor(workspace),
            request.model_dump(),
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _member_response(membership, session.get(UserORM, membership.user_id))


def _status_change(
    membership_id: UUID,
    status_value: str,
    session: Session,
    workspace: WorkspaceContext,
) -> MemberResponse:
    try:
        membership = set_member_status(
            session,
            workspace.workspace_id,
            membership_id,
            _actor(workspace),
            status_value,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    return _member_response(membership, session.get(UserORM, membership.user_id))


@router.post("/v1/members/{membership_id}/suspend", response_model=MemberResponse)
def suspend(
    membership_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("members.manage")),
) -> MemberResponse:
    return _status_change(membership_id, "suspended", session, workspace)


@router.post("/v1/members/{membership_id}/reactivate", response_model=MemberResponse)
def reactivate(
    membership_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("members.manage")),
) -> MemberResponse:
    return _status_change(membership_id, "active", session, workspace)


@router.delete("/v1/members/{membership_id}", response_model=MemberResponse)
def remove(
    membership_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("members.manage")),
) -> MemberResponse:
    return _status_change(membership_id, "revoked", session, workspace)
