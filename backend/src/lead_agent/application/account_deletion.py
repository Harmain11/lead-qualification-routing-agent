from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from lead_agent.infrastructure.db.operations import ContactORM, LeadContactORM
from lead_agent.infrastructure.db.models import (
    EncryptedProviderSecretORM,
    LeadORM,
    ProviderConnectionORM,
    SocialConversationORM,
    SocialMessageORM,
    SourceEventORM,
    UserORM,
    WorkspaceAgentConversationORM,
    WorkspaceEmailDraftORM,
    WorkspaceInvitationORM,
    WorkspaceMembershipORM,
    WorkspaceORM,
)


@dataclass(frozen=True)
class OwnedWorkspaceDeletionPreview:
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


@dataclass(frozen=True)
class AccountDeletionPreview:
    owned_workspaces: list[OwnedWorkspaceDeletionPreview]
    shared_workspaces: int
    invitations_to_cancel: int
    blockers: list[str]

    @property
    def can_delete(self) -> bool:
        return not self.blockers


class AccountDeletionBlocked(ValueError):
    pass


def _count_for_workspace(session: Session, model: type, workspace_id: UUID) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(model).where(model.workspace_id == workspace_id)
        )
        or 0
    )


def preview_account_deletion(session: Session, user_id: UUID) -> AccountDeletionPreview:
    memberships = list(
        session.scalars(
            select(WorkspaceMembershipORM).where(WorkspaceMembershipORM.user_id == user_id)
        )
    )
    owned = [row for row in memberships if row.role == "owner"]
    owned_workspaces: list[OwnedWorkspaceDeletionPreview] = []
    blockers: list[str] = []
    owned_ids = {row.workspace_id for row in owned}

    for membership in owned:
        workspace = session.get(WorkspaceORM, membership.workspace_id)
        if workspace is None:
            continue
        other_active_members = int(
            session.scalar(
                select(func.count())
                .select_from(WorkspaceMembershipORM)
                .where(
                    WorkspaceMembershipORM.workspace_id == workspace.id,
                    WorkspaceMembershipORM.user_id != user_id,
                    WorkspaceMembershipORM.status != "revoked",
                )
            )
            or 0
        )
        if other_active_members:
            blockers.append(
                f"Transfer or remove the other members from {workspace.name} before deleting this account."
            )
        owned_workspaces.append(
            OwnedWorkspaceDeletionPreview(
                id=workspace.id,
                name=workspace.name,
                other_active_members=other_active_members,
                leads=_count_for_workspace(session, LeadORM, workspace.id),
                integrations=_count_for_workspace(session, ProviderConnectionORM, workspace.id),
                stored_secrets=_count_for_workspace(
                    session, EncryptedProviderSecretORM, workspace.id
                ),
                source_events=_count_for_workspace(session, SourceEventORM, workspace.id),
                drafts=_count_for_workspace(session, WorkspaceEmailDraftORM, workspace.id),
                conversations=_count_for_workspace(
                    session, SocialConversationORM, workspace.id
                ),
                messages=_count_for_workspace(session, SocialMessageORM, workspace.id),
            )
        )

    shared_workspaces = sum(1 for row in memberships if row.workspace_id not in owned_ids)
    invitations_to_cancel = int(
        session.scalar(
            select(func.count())
            .select_from(WorkspaceInvitationORM)
            .where(WorkspaceInvitationORM.invited_by == user_id)
        )
        or 0
    )

    return AccountDeletionPreview(
        owned_workspaces=owned_workspaces,
        shared_workspaces=shared_workspaces,
        invitations_to_cancel=invitations_to_cancel,
        blockers=blockers,
    )


def delete_account(session: Session, user_id: UUID, confirmation_email: str) -> None:
    user = session.scalar(select(UserORM).where(UserORM.id == user_id).with_for_update())
    if user is None:
        raise LookupError("account not found")
    if confirmation_email.strip().casefold() != user.email.casefold():
        raise ValueError("confirmation email does not match")

    owned_ids = list(
        session.scalars(
            select(WorkspaceMembershipORM.workspace_id).where(
                WorkspaceMembershipORM.user_id == user_id,
                WorkspaceMembershipORM.role == "owner",
            )
        )
    )
    # Serialize deletion with membership inserts and changes before deciding
    # whether the owner is the only remaining member.
    if owned_ids:
        list(
            session.scalars(
                select(WorkspaceORM)
                .where(WorkspaceORM.id.in_(owned_ids))
                .order_by(WorkspaceORM.id)
                .with_for_update()
            )
        )
        list(
            session.scalars(
                select(WorkspaceMembershipORM)
                .where(WorkspaceMembershipORM.workspace_id.in_(owned_ids))
                .order_by(WorkspaceMembershipORM.id)
                .with_for_update()
            )
        )

    preview = preview_account_deletion(session, user_id)
    if preview.blockers:
        raise AccountDeletionBlocked(" ".join(preview.blockers))

    # Contact rows are workspace-cascaded, but lead_contacts.contact_id is
    # RESTRICT. Remove workspace-owned links first so contact deletion can
    # proceed; lead rows are still removed by the workspace cascade below.
    if owned_ids:
        session.execute(
            delete(LeadContactORM).where(
                LeadContactORM.contact_id.in_(
                    select(ContactORM.id).where(ContactORM.workspace_id.in_(owned_ids))
                )
            )
        )
        session.flush()

    # Workspaces with this user as owner are deleted as a unit. Workspaces where
    # the user is only a member remain intact; their membership is removed by
    # the user foreign-key cascade.
    for item in preview.owned_workspaces:
        workspace = session.scalar(
            select(WorkspaceORM).where(WorkspaceORM.id == item.id).with_for_update()
        )
        if workspace is not None:
            session.delete(workspace)
    session.flush()

    # Invitations in shared workspaces reference the inviter with RESTRICT.
    # Account deletion explicitly cancels those invitations before removing the
    # user; the Settings preview reports their count before confirmation.
    session.execute(
        delete(WorkspaceInvitationORM).where(WorkspaceInvitationORM.invited_by == user_id)
    )
    session.execute(
        delete(WorkspaceAgentConversationORM).where(
            WorkspaceAgentConversationORM.actor_id == str(user_id)
        )
    )
    session.flush()

    session.delete(user)
    session.commit()

