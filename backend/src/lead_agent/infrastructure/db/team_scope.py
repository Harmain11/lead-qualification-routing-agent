"""Request-bound ORM isolation. Workers without a bound context remain unscoped.

SQLAlchemy loader criteria also cover column/aggregate selects and aliases. Core/text
queries are deliberately rejected in a scoped session rather than silently bypassed.
This is an application boundary, not database RLS or a sandbox for arbitrary Python.
"""

from dataclasses import dataclass
from functools import wraps
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import String, and_, cast, event, false, func, inspect, or_, select, true
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import ColumnClause, TextClause
from sqlalchemy.sql.schema import Column, Table

from lead_agent.domain.rbac import AccessScope, effective_access_scope, normalize_role
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.base import Base

_KEY = "corral_team_scope"


@dataclass(frozen=True)
class TeamScope:
    workspace_id: UUID
    membership_id: UUID | None
    unrestricted: bool
    team_ids: tuple[UUID, ...] = ()
    user_id: UUID | None = None
    role: str = "viewer"
    lead_scope: AccessScope = AccessScope.NONE


def _criteria(scope: TeamScope) -> dict[type[Any], Any]:
    from lead_agent.infrastructure.db import models as m
    from lead_agent.infrastructure.db.operations import (
        AgentInsightORM,
        CompanyORM,
        ConsentEventORM,
        ContactORM,
        LeadContactORM,
    )
    from lead_agent.infrastructure.db.teams import LeadAccessGrantORM, WorkspaceTeamLeadORM

    leads = m.LeadORM.__table__
    links = WorkspaceTeamLeadORM.__table__
    teams = scope.team_ids
    allowed = select(leads.c.id).where(leads.c.workspace_id == scope.workspace_id)
    if not scope.unrestricted:
        grants = LeadAccessGrantORM.__table__
        visibility = []
        if scope.membership_id is not None:
            visibility.append(leads.c.id.in_(select(grants.c.lead_id).where(
                grants.c.workspace_id == scope.workspace_id,
                grants.c.membership_id == scope.membership_id,
                or_(grants.c.expires_at.is_(None), grants.c.expires_at > func.now()),
            )))
        if scope.lead_scope == AccessScope.TEAM and teams:
            visibility.append(leads.c.id.in_(select(links.c.lead_id).where(
                links.c.workspace_id == scope.workspace_id, links.c.team_id.in_(teams),
            )))
        if scope.lead_scope == AccessScope.OWN and scope.user_id is not None:
            decisions = m.RoutingDecisionORM.__table__
            visibility.extend((
                leads.c.current_owner_id == scope.user_id,
                and_(
                    leads.c.current_owner_id.is_(None),
                    leads.c.id.in_(select(decisions.c.lead_id).where(
                        decisions.c.workspace_id == scope.workspace_id,
                        decisions.c.is_current.is_(True),
                        decisions.c.assigned_rep_id == str(scope.user_id),
                    )),
                ),
            ))
        allowed = allowed.where(or_(*visibility) if visibility else false())

    result: dict[type[Any], Any] = {}
    # New mapped lead children automatically inherit the same boundary, including
    # nullable lead IDs (NULL is intentionally not visible to restricted actors).
    for mapper in Base.registry.mappers:
        model = mapper.class_
        if model is m.LeadORM:
            result[model] = model.id.in_(allowed)
        elif hasattr(model, "lead_id") and model not in {WorkspaceTeamLeadORM, AgentInsightORM}:
            result[model] = model.lead_id.in_(allowed)

    decisions = m.RoutingDecisionORM.__table__
    outcomes = m.LeadOutcomeORM.__table__
    contacts = ContactORM.__table__
    contact_links = LeadContactORM.__table__
    contact_ids = select(contact_links.c.contact_id).where(contact_links.c.lead_id.in_(allowed))
    special = {
        m.SourceEventORM: m.SourceEventORM.id.in_(
            select(leads.c.source_event_id).where(leads.c.id.in_(allowed)),
        ),
        m.NotificationORM: m.NotificationORM.routing_decision_id.in_(
            select(decisions.c.id).where(decisions.c.lead_id.in_(allowed)),
        ),
        m.LeadWinNotificationORM: m.LeadWinNotificationORM.outcome_id.in_(
            select(outcomes.c.id).where(outcomes.c.lead_id.in_(allowed)),
        ),
        ContactORM: ContactORM.id.in_(contact_ids),
        CompanyORM: CompanyORM.id.in_(
            select(contacts.c.company_id).where(contacts.c.id.in_(contact_ids)),
        ),
        ConsentEventORM: ConsentEventORM.email.in_(
            select(contacts.c.email).where(contacts.c.id.in_(contact_ids)),
        ),
        m.AgentToolAuditORM: func.replace(
            m.AgentToolAuditORM.input_summary["lead_id"].as_string(), "-", "",
        ).in_(select(func.replace(cast(leads.c.id, String), "-", "")).where(
            leads.c.id.in_(allowed),
        )),
        # Outbox payloads have polymorphic aggregates and no trustworthy lead FK.
        m.OutboxMessageORM: false(),
    }
    for model, criterion in special.items():
        result[model] = criterion
    for model in result:
        workspace_filter = (
            model.workspace_id == scope.workspace_id if hasattr(model, "workspace_id") else true()
        )
        if scope.unrestricted and model is not m.LeadORM and hasattr(model, "workspace_id"):
            # Administrators retain workspace-level contacts and nullable children.
            result[model] = and_(workspace_filter)
        else:
            result[model] = and_(workspace_filter, result[model])
    return result


def _reject_unsafe_statement(statement: Any, protected: set[str]) -> None:
    def raw_table(node: Any) -> bool:
        return (
            isinstance(node, Table) and node.name in protected
            and "parententity" not in node._annotations
        )

    for node in visitors.iterate(statement):
        if isinstance(node, TextClause):
            raise HTTPException(403, "text SQL is unavailable in a scoped request")
        if isinstance(node, ColumnClause) and node.is_literal and node.name != "*":
            raise HTTPException(403, "literal SQL columns are unavailable in a scoped request")
        if (
            isinstance(node, Column) and node.table.name in protected
            and "parententity" not in node._annotations
        ):
            raise HTTPException(403, "use mapped ORM columns in a scoped request")
        # An implicit table behind an annotated ORM column is safe. Explicit
        # Core FROMs, raw table selects and joins are not loader-criteria aware.
        explicit = (*getattr(node, "_raw_columns", ()), *getattr(node, "_from_obj", ()))
        if any(raw_table(part) for part in explicit):
            raise HTTPException(403, "use mapped ORM entities in a scoped request")
        if any(raw_table(getattr(node, side, None)) for side in ("left", "right")):
            raise HTTPException(403, "use mapped ORM joins in a scoped request")


@event.listens_for(Session, "do_orm_execute")
def _scope_sql(state: ORMExecuteState) -> None:
    scope = state.session.info.get(_KEY)
    if scope is None:
        return
    criteria = _criteria(scope)
    if state.is_column_load:
        refresh_state = getattr(state.load_options, "_refresh_state", None)
        if refresh_state is not None:
            _assert_visible(state.session, refresh_state.obj(), check_values=False)
    if not scope.unrestricted and not (state.is_relationship_load or state.is_column_load):
        _reject_unsafe_statement(state.statement, {m.__table__.name for m in criteria})
    if not scope.unrestricted and not state.is_orm_statement:
        raise HTTPException(403, "Core SQL is unavailable in a scoped request")
    if not scope.unrestricted and state.is_insert:
        raise HTTPException(403, "bulk inserts are unavailable in a scoped request")
    if not scope.unrestricted and (state.is_update or state.is_delete):
        if state.is_executemany:
            raise HTTPException(403, "bulk primary-key writes are unavailable in a scoped request")
        values = getattr(state.statement, "_values", {}) or {}
        protected_keys = {
            "id", "workspace_id", "lead_id", "source_event_id", "contact_id", "company_id",
            "routing_decision_id", "outcome_id",
        }
        if any(getattr(column, "key", column) in protected_keys for column in values):
            raise HTTPException(403, "bulk writes cannot change scope relationships")
        # SQLAlchemy's automatic synchronization emits an internal Core PK
        # SELECT. Session.get always reloads protected objects instead.
        state.update_execution_options(synchronize_session=False)
    # Never skip relationship or column loads; both can carry private data.
    if not state.is_orm_statement:
        return
    state.statement = state.statement.options(*(
        with_loader_criteria(model, criterion, include_aliases=True, propagate_to_loaders=False)
        for model, criterion in criteria.items()
    ))


def bind_team_scope(session: Session, workspace: WorkspaceContext) -> None:
    """Bind once before application queries, including sessions supplied by tests.

    Instance get/refresh guards are intentional: SQLAlchemy's identity-map hit
    emits no event, so a do_orm_execute listener alone cannot protect Session.get.
    """
    role = normalize_role(workspace.role)
    lead_scope = effective_access_scope(role, "leads.view", workspace.access_policy)
    unrestricted = role in {"owner", "admin"} or lead_scope == AccessScope.ALL
    team_ids: tuple[UUID, ...] = ()
    if not unrestricted and workspace.membership_id is not None:
        from lead_agent.infrastructure.db.teams import WorkspaceTeamMemberORM, WorkspaceTeamORM

        rows = session.execute(
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
        if role == "sales_manager":
            managed = tuple(team_id for team_id, _is_default, is_manager in rows if is_manager)
            team_ids = managed
        else:
            explicit = tuple(team_id for team_id, is_default, _is_manager in rows if not is_default)
            team_ids = explicit or tuple(team_id for team_id, _is_default, _is_manager in rows)
    scope = TeamScope(
        workspace.workspace_id, workspace.membership_id, unrestricted, team_ids,
        workspace.user_id, role, lead_scope,
    )
    previous = session.info.get(_KEY)
    if previous == scope:
        return
    if previous is not None:
        raise HTTPException(403, "a database session cannot switch workspace scope")
    if session.new or session.dirty or session.deleted:
        # Authentication may update its session timestamps. Flush these before
        # binding, but never discard pending changes by expunging the identity map.
        session.flush()
    session.expunge_all()
    session.info[_KEY] = scope
    original_get = session.get
    original_refresh = session.refresh

    @wraps(original_get)
    def guarded_get(entity: Any, ident: Any, **kwargs: Any) -> Any:
        mapper = inspect(entity)
        if mapper.class_ not in _criteria(scope):
            return original_get(entity, ident, **kwargs)
        if mapper.class_.__name__ == "LeadORM" and not isinstance(ident, dict) and ident in session.info.get(
            "corral_scope_created_leads", set()
        ):
            # Permit the request that created a lead to finish its pipeline graph
            # even when automatic routing immediately transfers ownership.
            return original_get(entity, ident, **kwargs)
        if isinstance(ident, dict):
            values = tuple(ident[column.key] for column in mapper.primary_key)
        else:
            values = tuple(ident) if isinstance(ident, tuple | list) else (ident,)
        if len(values) != len(mapper.primary_key):
            raise ValueError("incorrect number of primary key values")
        statement = select(entity).where(*(
            getattr(entity, column.key) == value
            for column, value in zip(mapper.primary_key, values, strict=True)
        )).options(*(kwargs.get("options") or ()))
        statement = statement.execution_options(
            **{**kwargs.get("execution_options", {}), "populate_existing": True},
        )
        if kwargs.get("with_for_update"):
            statement = statement.with_for_update()
        return session.scalar(statement)

    @wraps(original_refresh)
    def guarded_refresh(instance: Any, *args: Any, **kwargs: Any) -> None:
        _assert_visible(session, instance)
        original_refresh(instance, *args, **kwargs)

    session.get = guarded_get  # type: ignore[method-assign]
    session.refresh = guarded_refresh  # type: ignore[method-assign]
    if not scope.unrestricted:
        def unavailable(*args: Any, **kwargs: Any) -> Any:
            raise HTTPException(403, "unscoped database APIs are unavailable in a scoped request")

        # Legacy bulk methods bypass both unit-of-work and do_orm_execute events.
        session.bulk_save_objects = unavailable  # type: ignore[method-assign]
        session.bulk_insert_mappings = unavailable  # type: ignore[method-assign]
        session.bulk_update_mappings = unavailable  # type: ignore[method-assign]
        session.connection = unavailable  # type: ignore[method-assign]


def _assert_visible(session: Session, instance: Any, *, check_values: bool = True) -> None:
    scope = session.info.get(_KEY)
    if scope is None:
        return
    state = inspect(instance)
    criterion = _criteria(scope).get(state.mapper.class_)
    if criterion is None:
        return
    if check_values and scope.unrestricted and (
        not hasattr(instance, "workspace_id") or instance.workspace_id == scope.workspace_id
    ):
        return
    from lead_agent.infrastructure.db.models import AgentToolAuditORM
    if check_values and isinstance(instance, AgentToolAuditORM):
        created_audits = session.info.setdefault("corral_scope_created_audits", set())
        if instance.workspace_id == scope.workspace_id and (
            state.pending or id(instance) in created_audits
        ):
            # Preserve durable logs even for denied tools. These are records the
            # server creates in this request; reads still require a visible lead.
            created_audits.add(id(instance))
            return
    from lead_agent.infrastructure.db.models import NotificationORM, RoutingDecisionORM
    if (
        isinstance(instance, RoutingDecisionORM)
        and instance.id in session.info.get("corral_scope_created_decisions", set())
    ):
        return
    if (
        isinstance(instance, NotificationORM)
        and instance.id in session.info.get("corral_scope_created_notifications", set())
    ):
        return
    if state.pending:
        from lead_agent.infrastructure.db.models import LeadORM, SourceEventORM
        from lead_agent.infrastructure.db.operations import CompanyORM, ContactORM
        from lead_agent.infrastructure.db.teams import WorkspaceTeamMemberORM

        if hasattr(instance, "workspace_id") and instance.workspace_id != scope.workspace_id:
            raise HTTPException(403, "lead data is outside your workspace")
        if isinstance(instance, LeadORM):
            own_team = session.scalar(select(WorkspaceTeamMemberORM.team_id).where(
                WorkspaceTeamMemberORM.workspace_id == scope.workspace_id,
                WorkspaceTeamMemberORM.membership_id == scope.membership_id,
            ).limit(1))
            if own_team is None:
                raise HTTPException(403, "join a team before creating leads")
            if scope.user_id is not None and scope.lead_scope == AccessScope.OWN:
                instance.current_owner_id = scope.user_id
                instance.original_importer_id = instance.original_importer_id or scope.user_id
            instance.primary_team_id = instance.primary_team_id or own_team
            session.info.setdefault("corral_scope_created_leads", set()).add(instance.id)
            return
        if isinstance(instance, CompanyORM | ContactORM | SourceEventORM):
            # Fresh directory records contain no previously stored private data.
            # Reads remain hidden until an authorized lead link is inserted.
            return
        if (
            hasattr(instance, "lead_id")
            and instance.lead_id in session.info.get("corral_scope_created_leads", set())
        ):
            from lead_agent.infrastructure.db.models import RoutingDecisionORM
            if isinstance(instance, RoutingDecisionORM):
                session.info.setdefault("corral_scope_created_decisions", set()).add(instance.id)
            return
        related_id = instance.id if isinstance(instance, SourceEventORM) else getattr(
            instance, "lead_id", None,
        )
        for candidate in session.new:
            if isinstance(candidate, LeadORM) and related_id is not None and related_id == (
                candidate.source_event_id if isinstance(instance, SourceEventORM) else candidate.id
            ):
                _assert_visible(session, candidate)
                return
        if hasattr(instance, "aggregate_id"):
            for candidate in session.new:
                if isinstance(candidate, LeadORM) and candidate.id == instance.aggregate_id:
                    _assert_visible(session, candidate)
                    return
    if state.pending and hasattr(instance, "lead_id"):
        from lead_agent.infrastructure.db.models import LeadORM
        if (
            (not hasattr(instance, "workspace_id") or instance.workspace_id == scope.workspace_id)
            and instance.lead_id is not None
            and session.get(LeadORM, instance.lead_id) is not None
        ):
            return
    if state.pending:
        from lead_agent.infrastructure.db.models import LeadOutcomeORM, NotificationORM, RoutingDecisionORM
        if (
            isinstance(instance, NotificationORM)
            and instance.routing_decision_id in session.info.get(
                "corral_scope_created_decisions", set()
            )
        ):
            session.info.setdefault("corral_scope_created_notifications", set()).add(instance.id)
            return
        indirect_parents: tuple[tuple[str, type[Any]], ...] = (
            ("routing_decision_id", RoutingDecisionORM), ("outcome_id", LeadOutcomeORM),
        )
        for attribute, parent in indirect_parents:
            if (
                hasattr(instance, attribute)
                and session.get(parent, getattr(instance, attribute)) is not None
            ):
                return
    model = state.mapper.class_
    # The listener applies the criterion. Embedding it here would introduce
    # internal Core subqueries into the caller statement validation pass.
    conditions = []
    for index, column in enumerate(state.mapper.primary_key):
        value = state.identity[index] if state.identity else getattr(instance, column.key)
        conditions.append(getattr(model, column.key) == value)
    with session.no_autoflush:
        visible = session.scalar(select(model).where(*conditions).limit(1))
    if visible is None:
        raise HTTPException(403, "lead data is outside your team scope")
    if not check_values:
        return
    # Check new FK/workspace values as well as the persisted row's identity.
    if hasattr(instance, "workspace_id") and instance.workspace_id != scope.workspace_id:
        raise HTTPException(403, "lead data is outside your workspace")
    if hasattr(instance, "lead_id"):
        from lead_agent.infrastructure.db.models import LeadORM
        if session.get(LeadORM, instance.lead_id) is None:
            raise HTTPException(403, "lead data is outside your team scope")
    from lead_agent.infrastructure.db.models import (
        LeadOutcomeORM,
        RoutingDecisionORM,
        SourceEventORM,
    )
    from lead_agent.infrastructure.db.operations import CompanyORM, ContactORM
    relation_parents: tuple[tuple[str, type[Any]], ...] = (
        ("routing_decision_id", RoutingDecisionORM), ("outcome_id", LeadOutcomeORM),
        ("source_event_id", SourceEventORM), ("contact_id", ContactORM), ("company_id", CompanyORM),
    )
    for attribute, parent in relation_parents:
        if attribute in state.attrs and state.attrs[attribute].history.has_changes():
            value = getattr(instance, attribute)
            if value is not None and session.get(parent, value) is None:
                raise HTTPException(403, "related data is outside your team scope")


@event.listens_for(Session, "before_flush")
def _scope_writes(session: Session, _context: Any, _instances: Any) -> None:
    if _KEY not in session.info:
        return
    for instance in session.new | session.dirty | session.deleted:
        _assert_visible(session, instance)
