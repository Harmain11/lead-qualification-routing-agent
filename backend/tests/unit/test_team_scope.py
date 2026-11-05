from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Integer,
    Numeric,
    String,
    Uuid,
    create_engine,
    delete,
    func,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.orm import Session, aliased, joinedload, selectinload
from sqlalchemy.pool import StaticPool

from lead_agent.application.team import personal_data_user_ids
from lead_agent.domain.rbac import role_permissions
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models as m
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.operations import AgentInsightORM, LeadNoteORM
from lead_agent.infrastructure.db.team_scope import bind_team_scope
from lead_agent.infrastructure.db.teams import (
    WorkspaceTeamLeadORM,
    WorkspaceTeamMemberORM,
    WorkspaceTeamORM,
)


def row(model, **values):
    """Populate required scalar columns; individual tests exercise access, not domain validation."""
    for column in model.__table__.columns:
        if column.key in values or column.nullable or column.default or column.server_default:
            continue
        kind = column.type
        if isinstance(kind, Uuid):
            values[column.key] = uuid4()
        elif isinstance(kind, DateTime):
            values[column.key] = datetime.now(UTC)
        elif isinstance(kind, Boolean):
            values[column.key] = False
        elif isinstance(kind, (Integer, Numeric)):
            values[column.key] = 1
        elif isinstance(kind, JSON):
            values[column.key] = {}
        elif isinstance(kind, String):
            values[column.key] = f"test-{uuid4().hex}"
        else:
            raise AssertionError(f"unsupported fixture type: {column}")
    return model(**values)


@pytest.fixture
def store():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        workspace, other = row(m.WorkspaceORM, last_lead_number=2), row(m.WorkspaceORM)
        session.add_all([workspace, other])
        session.flush()
        user, colleague = row(m.UserORM), row(m.UserORM)
        session.add_all([user, colleague])
        session.flush()
        member = row(m.WorkspaceMembershipORM, workspace_id=workspace.id, user_id=user.id,
                     status="active", role="sales_rep")
        teammate = row(m.WorkspaceMembershipORM, workspace_id=workspace.id, user_id=colleague.id,
                       status="active", role="sales_rep")
        session.add_all([member, teammate])
        visible = row(m.LeadORM, workspace_id=workspace.id, lead_number=1)
        hidden = row(m.LeadORM, workspace_id=workspace.id, lead_number=2)
        foreign = row(m.LeadORM, workspace_id=other.id, lead_number=1)
        session.add_all([visible, hidden, foreign])
        session.commit()
        session.execute(delete(WorkspaceTeamMemberORM).where(
            WorkspaceTeamMemberORM.membership_id.in_([member.id, teammate.id]),
        ))
        team = row(WorkspaceTeamORM, workspace_id=workspace.id, is_default=False)
        session.add(team)
        session.flush()
        session.add_all([
            WorkspaceTeamMemberORM(workspace_id=workspace.id, team_id=team.id,
                                   membership_id=member.id),
            WorkspaceTeamMemberORM(workspace_id=workspace.id, team_id=team.id,
                                   membership_id=teammate.id),
            WorkspaceTeamLeadORM(workspace_id=workspace.id, team_id=team.id, lead_id=visible.id),
        ])
        session.commit()
        context = WorkspaceContext(
            workspace_id=workspace.id, user_id=user.id, actor_id=str(user.id),
            membership_id=member.id, role="sales_rep", permissions=role_permissions("sales_rep"),
        )
        yield session, context, visible, hidden, foreign, team, teammate
    engine.dispose()


def test_entities_scalars_aggregates_aliases_subqueries_and_cached_get(store):
    session, context, visible, hidden, foreign, *_ = store
    cached = session.get(m.LeadORM, hidden.id)
    bind_team_scope(session, context)
    assert cached not in session
    assert [lead.id for lead in session.scalars(select(m.LeadORM))] == [visible.id]
    assert list(session.scalars(select(m.LeadORM.id))) == [visible.id]
    assert session.scalar(select(func.count()).select_from(m.LeadORM)) == 1
    assert session.scalar(select(func.count(m.LeadORM.id))) == 1
    alias = aliased(m.LeadORM)
    assert list(session.scalars(select(alias.id))) == [visible.id]
    subquery = select(m.LeadORM.id).subquery()
    assert list(session.scalars(select(subquery.c.id))) == [visible.id]
    assert session.get(m.LeadORM, hidden.id) is None
    assert session.get(m.LeadORM, foreign.id) is None
    assert session.get(m.LeadORM, visible.id).id == visible.id
    # A clean preloaded object reattached later must not bypass Session.get.
    session.add(cached)
    assert session.get(m.LeadORM, hidden.id) is None


DIRECT_CHILDREN = [
    mapper.class_ for mapper in Base.registry.mappers
    if hasattr(mapper.class_, "lead_id")
    and mapper.class_ not in {AgentInsightORM, WorkspaceTeamLeadORM}
]


@pytest.mark.parametrize("model", DIRECT_CHILDREN, ids=lambda model: model.__name__)
def test_every_mapped_lead_child_entity_scalar_and_identity_get(store, model):
    session, context, visible, hidden, foreign, *_ = store
    children = []
    for lead in (visible, hidden, foreign):
        values = {"lead_id": lead.id}
        if hasattr(model, "workspace_id"):
            values["workspace_id"] = lead.workspace_id
        child = row(model, **values)
        session.add(child)
        children.append(child)
    session.commit()
    hidden_identity = tuple(getattr(children[1], c.key) for c in model.__mapper__.primary_key)
    bind_team_scope(session, context)
    assert [child.lead_id for child in session.scalars(select(model))] == [visible.id]
    assert list(session.scalars(select(model.lead_id))) == [visible.id]
    assert session.scalar(select(func.count()).select_from(model)) == 1
    assert session.get(model, hidden_identity) is None


def test_indirect_source_receipts_relationships_and_eager_loads(store):
    session, context, visible, hidden, *_ = store
    for lead in (visible, hidden):
        source = row(m.SourceEventORM, workspace_id=lead.workspace_id)
        session.add(source)
        session.flush()
        lead.source_event_id = source.id
        qualification = row(m.QualificationResultORM, lead_id=lead.id)
        decision = row(m.RoutingDecisionORM, workspace_id=lead.workspace_id, lead_id=lead.id,
                       qualification_id=qualification.id or uuid4())
        outcome = row(m.LeadOutcomeORM, workspace_id=lead.workspace_id, lead_id=lead.id)
        session.add_all([qualification, decision, outcome])
        session.flush()
        session.add_all([
            row(m.NotificationORM, workspace_id=lead.workspace_id, routing_decision_id=decision.id),
            row(m.LeadWinNotificationORM, workspace_id=lead.workspace_id, outcome_id=outcome.id),
        ])
    session.commit()
    bind_team_scope(session, context)
    for model in (m.SourceEventORM, m.NotificationORM, m.LeadWinNotificationORM):
        assert session.scalar(select(func.count()).select_from(model)) == 1
    loaded = session.scalar(select(m.LeadORM).options(
        joinedload(m.LeadORM.source_event), selectinload(m.LeadORM.routing_decisions),
    ))
    assert loaded.id == visible.id
    assert loaded.source_event.lead.id == visible.id
    assert len(loaded.routing_decisions[0].notifications) == 1


@pytest.mark.parametrize("statement", [
    text("SELECT id FROM leads"), select(m.LeadORM.__table__),
    select(m.LeadORM.__table__.c.id),
    select(m.LeadORM.id).where(m.LeadORM.id.in_(select(m.LeadORM.__table__.c.id))),
    select(m.LeadORM).from_statement(text("SELECT * FROM leads")),
    insert(m.LeadORM).values({}),
])
def test_unsafe_sql_forms_fail_closed(store, statement):
    session, context, *_ = store
    bind_team_scope(session, context)
    with pytest.raises(HTTPException) as error:
        session.execute(statement)
    assert error.value.status_code == 403


def test_new_child_write_allowed_but_hidden_child_and_retargeting_denied(store):
    session, context, visible, hidden, *_ = store
    bind_team_scope(session, context)
    note = row(LeadNoteORM, workspace_id=context.workspace_id, lead_id=visible.id, body="Allowed")
    session.add(note)
    session.commit()
    assert session.get(LeadNoteORM, note.id).body == "Allowed"
    note.body = "Allowed update"
    session.commit()
    assert session.get(LeadNoteORM, note.id).body == "Allowed update"
    note.lead_id = hidden.id
    with pytest.raises(HTTPException):
        session.flush()
    session.rollback()
    session.add(row(LeadNoteORM, workspace_id=context.workspace_id, lead_id=hidden.id))
    with pytest.raises(HTTPException):
        session.flush()
    session.rollback()


def test_bulk_updates_deletes_cannot_touch_hidden_rows(store):
    session, context, visible, hidden, *_ = store
    bind_team_scope(session, context)
    session.execute(update(m.LeadORM).values(full_name="Visible only"))
    session.commit()
    assert session.get(m.LeadORM, visible.id).full_name == "Visible only"
    session.execute(delete(m.LeadORM).where(m.LeadORM.id == hidden.id))
    session.commit()
    with Session(session.get_bind()) as unscoped:
        assert unscoped.get(m.LeadORM, hidden.id).full_name != "Visible only"


def test_admin_sql_compatibility_and_workspace_criteria(store):
    session, context, visible, hidden, foreign, *_ = store
    admin = WorkspaceContext(workspace_id=context.workspace_id, role="admin")
    bind_team_scope(session, admin)
    assert {lead.id for lead in session.scalars(select(m.LeadORM))} == {visible.id, hidden.id}
    assert session.execute(text("SELECT 1")).scalar() == 1
    assert session.execute(select(m.LeadORM.__table__.c.id)).all()
    assert session.get(m.LeadORM, foreign.id) is None


def test_no_team_means_no_leads_and_scope_cannot_switch(store):
    session, context, *_ = store
    empty = WorkspaceContext(
        workspace_id=context.workspace_id, role="viewer", membership_id=uuid4(),
    )
    bind_team_scope(session, empty)
    assert session.scalar(select(func.count()).select_from(m.LeadORM)) == 0
    with pytest.raises(HTTPException):
        bind_team_scope(session, context)


def test_personal_audiences_and_suspended_team_members(store):
    session, context, *_, teammate = store
    assert personal_data_user_ids(session, context) == {str(context.user_id)}
    manager = WorkspaceContext(workspace_id=context.workspace_id, user_id=context.user_id,
                               membership_id=context.membership_id, role="sales_manager")
    assert personal_data_user_ids(session, manager) == {str(context.user_id), str(teammate.user_id)}
    teammate.status = "suspended"
    session.commit()
    assert personal_data_user_ids(session, manager) == {str(context.user_id)}
    assert personal_data_user_ids(session, WorkspaceContext(
        workspace_id=context.workspace_id, role="viewer")) == set()


def test_revocation_get_refresh_and_expired_column_fail_closed(store):
    session, context, visible, *_ = store
    bind_team_scope(session, context)
    loaded = session.get(m.LeadORM, visible.id)
    lead_id = loaded.id
    with Session(session.get_bind()) as admin:
        admin.execute(delete(WorkspaceTeamLeadORM).where(
            WorkspaceTeamLeadORM.lead_id == lead_id,
            WorkspaceTeamLeadORM.team_id != context.workspace_id,
        ))
        admin.commit()
    assert session.get(m.LeadORM, lead_id) is None
    with pytest.raises(HTTPException):
        session.refresh(loaded)
    session.expire(loaded, ["full_name"])
    with pytest.raises(HTTPException):
        _ = loaded.full_name


@pytest.mark.parametrize("method,args", [
    ("bulk_save_objects", ([],)), ("bulk_insert_mappings", (m.LeadORM, [])),
    ("bulk_update_mappings", (m.LeadORM, [])), ("connection", ()),
])
def test_legacy_bulk_and_direct_connection_fail_closed(store, method, args):
    session, context, *_ = store
    bind_team_scope(session, context)
    with pytest.raises(HTTPException):
        getattr(session, method)(*args)


def test_default_enrollment_existing_active_member_not_readded_after_manual_removal(store):
    session, context, visible, *_ = store
    default = session.get(WorkspaceTeamORM, context.workspace_id)
    assert default.is_default
    assert session.get(WorkspaceTeamLeadORM, (default.id, visible.id))
    assert session.get(WorkspaceTeamMemberORM, (default.id, context.membership_id)) is None
    member = session.get(m.WorkspaceMembershipORM, context.membership_id)
    member.updated_at = datetime.now(UTC)
    session.commit()
    assert session.get(WorkspaceTeamMemberORM, (default.id, member.id)) is None


def test_new_workspace_member_and_intake_lead_enroll_defaults(store):
    session, *_ = store
    workspace = row(m.WorkspaceORM)
    session.add(workspace)
    session.commit()
    assert session.get(WorkspaceTeamORM, workspace.id).is_default
    user = row(m.UserORM)
    session.add(user)
    session.flush()
    member = row(m.WorkspaceMembershipORM, workspace_id=workspace.id, user_id=user.id,
                 status="active", role="sales_rep")
    lead = row(m.LeadORM, workspace_id=workspace.id)
    session.add_all([member, lead])
    session.commit()
    assert session.get(WorkspaceTeamMemberORM, (workspace.id, member.id))
    assert session.get(WorkspaceTeamLeadORM, (workspace.id, lead.id))


def test_restricted_new_lead_is_assigned_to_actor_teams(store):
    session, context, *_, team, _teammate = store
    bind_team_scope(session, context)
    lead = row(m.LeadORM, workspace_id=context.workspace_id, lead_number=3)
    session.add(lead)
    session.commit()
    assert session.get(m.LeadORM, lead.id)
    assert session.get(WorkspaceTeamLeadORM, (team.id, lead.id))
    assert session.get(WorkspaceTeamLeadORM, (context.workspace_id, lead.id)) is None


def test_restricted_pipeline_new_graph_can_flush(store, icp, routing):
    from lead_agent.application.services import process_lead
    from lead_agent.infrastructure.reasoning.deterministic import DeterministicReasoningProvider

    session, context, *_ = store
    bind_team_scope(session, context)
    _qualification, _routing, lead = process_lead(session, {
        "source": "website", "full_name": "New Scope Lead", "email": "new-scope@example.test",
        "company_name": "New Scope Company", "product_or_service": "Lead routing",
        "message": "Please show a demo for our company", "external_id": "new-scope-test",
    }, icp, routing, provider=DeterministicReasoningProvider(), workspace_id=context.workspace_id)
    assert session.get(m.LeadORM, lead.id)


def test_tool_audit_creation_completion_and_lead_scoped_reads(store):
    session, context, visible, hidden, *_ = store
    bind_team_scope(session, context)
    visible_audit = row(m.AgentToolAuditORM, workspace_id=context.workspace_id,
                        input_summary={"lead_id": str(visible.id)}, status="pending")
    denied_audit = row(m.AgentToolAuditORM, workspace_id=context.workspace_id,
                       input_summary={"lead_id": str(hidden.id)}, status="pending")
    session.add_all([visible_audit, denied_audit])
    session.commit()
    visible_audit.status = "completed"
    denied_audit.status = "denied"
    session.commit()
    assert list(session.scalars(select(m.AgentToolAuditORM.id))) == [visible_audit.id]
