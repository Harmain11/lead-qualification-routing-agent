from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import Field
from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission, require_permissions
from lead_agent.api.operations_schemas import CollaborationData, NoteRecord
from lead_agent.application.auth import membership_permissions
from lead_agent.application.governance import audit_operation
from lead_agent.application.lead_access import assert_lead_action
from lead_agent.domain.operations import StrictModel
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import LeadORM, UserORM, WorkspaceMembershipORM
from lead_agent.infrastructure.db.operations import LeadNoteORM, LeadPresenceORM
from lead_agent.infrastructure.db.session import get_db

router = APIRouter(prefix="/v1/collaboration", tags=["live notes and presence"])


def owned_lead(session: Session, workspace_id: UUID, lead_id: UUID) -> LeadORM:
    lead = session.get(LeadORM, lead_id)
    if lead is None or lead.workspace_id != workspace_id:
        raise HTTPException(404, "Lead not found")
    return lead


def _presence_member_can_view(session: Session, lead: LeadORM, user_id: UUID) -> bool:
    membership = session.scalar(
        select(WorkspaceMembershipORM)
        .join(UserORM, UserORM.id == WorkspaceMembershipORM.user_id)
        .where(
            WorkspaceMembershipORM.workspace_id == lead.workspace_id,
            WorkspaceMembershipORM.user_id == user_id,
            WorkspaceMembershipORM.status == "active",
            UserORM.status == "active",
        )
    )
    if membership is None:
        return False
    context = WorkspaceContext(
        workspace_id=lead.workspace_id,
        actor_id=str(user_id),
        user_id=user_id,
        membership_id=membership.id,
        role=membership.role,
        permissions=membership_permissions(membership),
        access_policy=membership.access_policy,
    )
    try:
        assert_lead_action(session, context, lead, "view")
    except HTTPException:
        return False
    return True


@router.get("/leads/{lead_id}", response_model=CollaborationData)
def collaboration(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("comments.view")),
) -> Any:
    lead = owned_lead(session, workspace.workspace_id, lead_id)
    assert_lead_action(session, workspace, lead, "view")
    note_visibility = [LeadNoteORM.is_private.is_(False)]
    if "comments.view_private" in workspace.permissions:
        note_visibility.append(LeadNoteORM.is_private.is_(True))
    else:
        note_visibility.append(LeadNoteORM.author_id == workspace.actor_id)
    notes = list(
        session.scalars(
            select(LeadNoteORM)
            .where(
                LeadNoteORM.workspace_id == workspace.workspace_id,
                LeadNoteORM.lead_id == lead_id,
                or_(*note_visibility),
            )
            .order_by(LeadNoteORM.created_at)
            .limit(200)
        )
    )
    presence_rows = session.execute(
        select(UserORM.id, UserORM.display_name)
        .join(LeadPresenceORM, LeadPresenceORM.user_id == UserORM.id)
        .where(
            LeadPresenceORM.workspace_id == workspace.workspace_id,
            LeadPresenceORM.lead_id == lead_id,
            LeadPresenceORM.expires_at > datetime.now(UTC),
        )
    ).all()
    presence = [
        (user_id, name)
        for user_id, name in presence_rows
        if _presence_member_can_view(session, lead, user_id)
    ]
    return {
        "notes": notes,
        "presence": [{"user_id": user_id, "name": name} for user_id, name in presence],
    }


@router.post("/leads/{lead_id}/presence")
def presence(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> Any:
    lead = owned_lead(session, workspace.workspace_id, lead_id)
    assert_lead_action(session, workspace, lead, "view")
    if workspace.user_id is None:
        raise HTTPException(401, "Sign in to share viewing presence")
    key = {"lead_id": lead_id, "user_id": workspace.user_id}
    row = session.get(LeadPresenceORM, key)
    if row is None:
        try:
            with session.begin_nested():
                row = LeadPresenceORM(
                    **key,
                    workspace_id=workspace.workspace_id,
                    expires_at=datetime.now(UTC) + timedelta(seconds=45),
                )
                session.add(row)
                session.flush()
        except IntegrityError:
            row = session.get(LeadPresenceORM, key)
    if row is None:
        raise HTTPException(409, "Presence changed; retry")
    row.expires_at = datetime.now(UTC) + timedelta(seconds=45)
    session.commit()
    return {"expires_at": row.expires_at}


class NoteInput(StrictModel):
    body: str = Field(min_length=1, max_length=5000)
    version: int = Field(ge=0)
    is_private: bool = False


@router.put("/leads/{lead_id}/notes/{note_id}", response_model=NoteRecord)
def save_note(
    lead_id: UUID,
    note_id: UUID,
    request: NoteInput,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("comments.view")),
) -> Any:
    lead = owned_lead(session, workspace.workspace_id, lead_id)
    assert_lead_action(session, workspace, lead, "view")
    row = session.get(LeadNoteORM, note_id)
    if row and (row.workspace_id != workspace.workspace_id or row.lead_id != lead_id):
        raise HTTPException(404, "Note not found")
    if (row.version if row else 0) != request.version:
        raise HTTPException(
            409,
            (
                "A teammate changed this note. Keep your text, reload the "
                "saved version and merge before saving."
            ),
        )
    required = "comments.add" if row is None else (
        "comments.edit_own" if row.author_id == workspace.actor_id else "comments.edit_shared"
    )
    if required not in workspace.permissions:
        raise HTTPException(403, f"permission required: {required}")
    if row is not None and row.is_private and row.author_id != workspace.actor_id and (
        "comments.view_private" not in workspace.permissions
    ):
        raise HTTPException(404, "Note not found")
    if row:
        changed = cast(
            CursorResult[Any],
            session.execute(
                update(LeadNoteORM)
                .where(LeadNoteORM.id == note_id, LeadNoteORM.version == request.version)
                .values(
                    body=request.body,
                    is_private=request.is_private,
                    version=request.version + 1,
                    updated_by=workspace.actor_id,
                    updated_at=datetime.now(UTC),
                )
            ),
        )
        if not changed.rowcount:
            raise HTTPException(409, "A teammate changed this note; reload and merge your text.")
    else:
        row = LeadNoteORM(
            id=note_id,
            workspace_id=workspace.workspace_id,
            lead_id=lead_id,
            body=request.body,
            is_private=request.is_private,
            author_id=workspace.actor_id,
            updated_by=workspace.actor_id,
        )
        session.add(row)
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "lead.note.saved",
        {"lead_id": str(lead_id), "note_id": str(note_id), "version": request.version + 1},
    )
    session.commit()
    session.refresh(row)
    return row


@router.delete("/leads/{lead_id}/notes/{note_id}", status_code=204)
def delete_note(
    lead_id: UUID,
    note_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(
        require_permissions("comments.view", "comments.delete")
    ),
) -> Response:
    lead = owned_lead(session, workspace.workspace_id, lead_id)
    assert_lead_action(session, workspace, lead, "view")
    row = session.get(LeadNoteORM, note_id)
    if row is None or row.workspace_id != workspace.workspace_id or row.lead_id != lead_id:
        raise HTTPException(404, "Note not found")
    if row.is_private and row.author_id != workspace.actor_id and (
        "comments.view_private" not in workspace.permissions
    ):
        raise HTTPException(404, "Note not found")
    session.delete(row)
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "lead.note.deleted",
        {"lead_id": str(lead_id), "note_id": str(note_id)},
    )
    session.commit()
    return Response(status_code=204)
