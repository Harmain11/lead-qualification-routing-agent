"""Explicit workspace teams and their access-granting member/lead links."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    JSON,
    String,
    Text,
    event,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from lead_agent.infrastructure.db.base import Base


def now() -> datetime:
    return datetime.now(UTC)


class WorkspaceTeamORM(Base):
    __tablename__ = "workspace_teams"
    __table_args__ = (
        Index("uq_team_workspace_name", "workspace_id", "name_key", unique=True),
        Index("uq_team_workspace_id", "workspace_id", "id", unique=True),
        Index(
            "uq_team_workspace_default", "workspace_id", unique=True,
            sqlite_where=text("is_default = 1"), postgresql_where=text("is_default IS TRUE"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    name_key: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WorkspaceTeamMemberORM(Base):
    __tablename__ = "workspace_team_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "team_id"], ["workspace_teams.workspace_id", "workspace_teams.id"],
            ondelete="CASCADE",
        ),
        Index("ix_team_members_membership", "membership_id", "team_id"),
    )
    team_id: Mapped[UUID] = mapped_column(primary_key=True)
    membership_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_memberships.id", ondelete="CASCADE"), primary_key=True,
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    is_manager: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WorkspaceTeamLeadORM(Base):
    __tablename__ = "workspace_team_leads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "team_id"], ["workspace_teams.workspace_id", "workspace_teams.id"],
            ondelete="CASCADE",
        ),
        Index("ix_team_leads_lead", "lead_id", "team_id"),
    )
    team_id: Mapped[UUID] = mapped_column(primary_key=True)
    lead_id: Mapped[UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True,
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LeadAccessGrantORM(Base):
    """An explicit, revocable collaboration grant independent of assignment."""

    __tablename__ = "lead_access_grants"
    __table_args__ = (
        Index("ix_lead_access_grants_member", "workspace_id", "membership_id"),
        Index("ix_lead_access_grants_lead", "workspace_id", "lead_id"),
    )
    lead_id: Mapped[UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True,
    )
    membership_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_memberships.id", ondelete="CASCADE"), primary_key=True,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    can_edit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    granted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    reason: Mapped[str] = mapped_column(String(255), nullable=False, default="collaboration")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LeadReadReceiptORM(Base):
    """Per-member acknowledgement used to distinguish new lead activity from read work."""

    __tablename__ = "lead_read_receipts"
    __table_args__ = (
        Index("ix_lead_read_receipts_workspace_user", "workspace_id", "user_id"),
    )
    lead_id: Mapped[UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now)


class LeadAssignmentHistoryORM(Base):
    """Immutable assignment snapshots used for audit and historical attribution."""

    __tablename__ = "lead_assignment_history"
    __table_args__ = (
        Index("ix_lead_assignment_history_lead", "workspace_id", "lead_id", "created_at"),
        Index("ix_lead_assignment_history_owner", "workspace_id", "new_owner_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    lead_id: Mapped[UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False,
    )
    previous_owner_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    new_owner_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    previous_team_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspace_teams.id", ondelete="SET NULL"), nullable=True,
    )
    new_team_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspace_teams.id", ondelete="SET NULL"), nullable=True,
    )
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    reason: Mapped[str] = mapped_column(String(255), nullable=False, default="assignment")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ConnectionAccessGrantORM(Base):
    """Delegates a bounded set of connection actions without transferring ownership."""

    __tablename__ = "connection_access_grants"
    __table_args__ = (
        Index("ix_connection_access_grants_member", "workspace_id", "membership_id"),
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), primary_key=True,
    )
    membership_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_memberships.id", ondelete="CASCADE"), primary_key=True,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    actions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    granted_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


@event.listens_for(Session, "before_flush")
def _remember_default_enrollment(session: Session, _context: Any, _instances: Any) -> None:
    from lead_agent.infrastructure.db.models import LeadORM, WorkspaceMembershipORM, WorkspaceORM

    pending = session.info.setdefault("corral_default_enrollment", [])
    for row in session.new | session.dirty:
        if isinstance(row, (LeadORM, WorkspaceORM)) and row in session.new:
            pending.append(row)
        elif isinstance(row, WorkspaceMembershipORM) and row.status == "active" and (
            row in session.new or inspect(row).attrs.status.history.has_changes()
        ):
            pending.append(row)


@event.listens_for(Session, "after_flush_postexec")
def _enroll_defaults(session: Session, _context: Any) -> None:
    from lead_agent.infrastructure.db.models import LeadORM, WorkspaceMembershipORM, WorkspaceORM

    pending = session.info.pop("corral_default_enrollment", [])
    if not pending:
        return
    defaults: dict[UUID, UUID] = {}
    scope = session.info.get("corral_team_scope")
    for row in pending:
        workspace_id = row.id if isinstance(row, WorkspaceORM) else row.workspace_id
        if workspace_id not in defaults:
            team_id = session.scalar(select(WorkspaceTeamORM.id).where(
                WorkspaceTeamORM.workspace_id == workspace_id,
                WorkspaceTeamORM.is_default.is_(True),
            ))
            if team_id is None:
                team_id = workspace_id
                session.add(WorkspaceTeamORM(
                    id=team_id, workspace_id=workspace_id,
                    name="Default team", name_key="default team",
                    description="Default scope for new workspace members and inbound leads",
                    is_default=True,
                ))
            defaults[workspace_id] = team_id
        team_id = defaults[workspace_id]
        if isinstance(row, WorkspaceMembershipORM):
            has_team = session.scalar(select(WorkspaceTeamMemberORM.team_id).where(
                WorkspaceTeamMemberORM.membership_id == row.id,
                WorkspaceTeamMemberORM.workspace_id == workspace_id,
            ).limit(1))
            if has_team is None:
                session.add(WorkspaceTeamMemberORM(
                    workspace_id=workspace_id, team_id=team_id, membership_id=row.id,
                ))
        elif isinstance(row, LeadORM):
            team_ids = [team_id]
            if scope is not None and not scope.unrestricted:
                team_ids = list(scope.team_ids)
            for assigned_team in team_ids:
                session.add(WorkspaceTeamLeadORM(
                    workspace_id=workspace_id, team_id=assigned_team, lead_id=row.id,
                ))


@event.listens_for(Session, "after_rollback")
def _clear_enrollment(session: Session) -> None:
    session.info.pop("corral_default_enrollment", None)
