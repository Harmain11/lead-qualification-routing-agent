import importlib.util
from io import StringIO
from pathlib import Path
from uuid import UUID, uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, inspect, select
from sqlalchemy.orm import Session
from test_team_scope import row, store  # noqa: F401,F811

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.routes.teams import router
from lead_agent.domain.rbac import role_permissions
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models as m
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.db.team_scope import bind_team_scope
from lead_agent.infrastructure.db.teams import (
    WorkspaceTeamLeadORM,
    WorkspaceTeamMemberORM,
    WorkspaceTeamORM,
)


def test_team_api_contract_admin_only_writes_own_scope_reads_and_cross_workspace(store):  # noqa: F811
    session, rep_context, visible, hidden, foreign, team, teammate = store
    session.execute(delete(WorkspaceTeamLeadORM).where(WorkspaceTeamLeadORM.lead_id == hidden.id))
    hidden.primary_team_id = None
    session.commit()
    active = {"workspace": WorkspaceContext(
        workspace_id=rep_context.workspace_id, role="owner", permissions=role_permissions("owner"),
    )}
    app = FastAPI()
    app.include_router(router)

    def db():
        with Session(session.get_bind(), expire_on_commit=False) as request_session:
            yield request_session

    def context(request_session=Depends(get_db)):
        workspace = active["workspace"]
        bind_team_scope(request_session, workspace)
        return workspace

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_workspace_context] = context
    with TestClient(app) as client:
        listed = client.get("/v1/teams")
        assert listed.status_code == 200
        assert len(listed.json()) == 2
        created = client.post("/v1/teams", json={"name": "  Enterprise  ", "description": "West"})
        assert created.status_code == 201, created.text
        result = created.json()
        assert set(result) == {
            "id", "workspace_id", "name", "description", "is_default", "member_ids",
            "manager_ids", "lead_ids", "created_at", "updated_at",
        }
        team_id = result["id"]
        assert result["name"] == "Enterprise"
        assert client.post("/v1/teams", json={"name": "enterprise"}).status_code == 409
        for _ in range(2):
            added = client.put(f"/v1/teams/{team_id}/members/{teammate.id}")
            assert added.status_code == 200, added.text
            assert added.json()["member_ids"] == [str(teammate.id)]
        assigned = client.put(f"/v1/teams/{team_id}/leads/{hidden.id}")
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["lead_ids"] == [str(hidden.id)]
        session.expire_all()
        assert session.get(m.LeadORM, hidden.id).primary_team_id == UUID(team_id)
        assert client.put(f"/v1/teams/{team_id}/leads/{foreign.id}").status_code == 404
        assert client.put(f"/v1/teams/{uuid4()}/leads/{visible.id}").status_code == 404
        assert client.put(f"/v1/teams/{team_id}/members/{uuid4()}").status_code == 404
        assert client.delete(f"/v1/teams/{rep_context.workspace_id}").status_code == 409
        assert client.patch(f"/v1/teams/{team_id}", json={"name": "West"}).status_code == 200

        active["workspace"] = WorkspaceContext(
            workspace_id=rep_context.workspace_id, user_id=rep_context.user_id,
            membership_id=rep_context.membership_id, role="sales_rep",
            permissions=role_permissions("sales_rep") | {"members.manage"},
        )
        own = client.get("/v1/teams")
        assert own.status_code == 200
        assert [item["id"] for item in own.json()] == [str(team.id)]
        assert own.json()[0]["lead_ids"] == [str(visible.id)]
        assert client.post("/v1/teams", json={"name": "Escalation"}).status_code == 403
        blocked = client.put(f"/v1/teams/{team_id}/members/{rep_context.membership_id}")
        assert blocked.status_code == 403

        active["workspace"] = WorkspaceContext(
            workspace_id=rep_context.workspace_id,
            role="admin", permissions=role_permissions("admin"),
        )
        assert client.delete(f"/v1/teams/{team_id}/leads/{hidden.id}").json()["lead_ids"] == []
        session.expire_all()
        assert session.get(m.LeadORM, hidden.id).primary_team_id is None
        removed = client.delete(f"/v1/teams/{team_id}/members/{teammate.id}")
        assert removed.json()["member_ids"] == []
        assert client.delete(f"/v1/teams/{team_id}").status_code == 204


def test_own_default_team_response_does_not_reveal_out_of_scope_lead_ids(store):  # noqa: F811
    from lead_agent.api.routes.teams import list_teams

    session, context, visible, hidden, *_ = store
    session.add(WorkspaceTeamMemberORM(
        team_id=context.workspace_id, workspace_id=context.workspace_id,
        membership_id=context.membership_id,
    ))
    session.commit()
    bind_team_scope(session, context)
    teams = list_teams(session, context)
    assert len(teams) == 2
    assert all(hidden.id not in item.lead_ids for item in teams)
    assert all(item.lead_ids == [visible.id] for item in teams)


def test_team_migration_backfills_active_members_and_all_leads_then_downgrades():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    team_tables = {"workspace_teams", "workspace_team_members", "workspace_team_leads"}
    Base.metadata.create_all(engine, tables=[
        table for table in Base.metadata.sorted_tables if table.name not in team_tables
    ])
    path = Path(__file__).resolve().parents[3] / "infra/migrations/versions/0058_workspace_teams.py"
    spec = importlib.util.spec_from_file_location("team_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        workspace_id, other_id, user_id, active_id, suspended_id = [uuid4() for _ in range(5)]
        for workspace in (workspace_id, other_id):
            value = row(m.WorkspaceORM, id=workspace)
            connection.execute(m.WorkspaceORM.__table__.insert(), {
                key: val for key, val in vars(value).items() if not key.startswith("_")
            })
        for membership_id, status in ((active_id, "active"), (suspended_id, "suspended")):
            user = row(m.UserORM, id=user_id)
            connection.execute(m.UserORM.__table__.insert(), {
                key: val for key, val in vars(user).items() if not key.startswith("_")
            })
            member = row(m.WorkspaceMembershipORM, id=membership_id, workspace_id=workspace_id,
                         user_id=user_id, status=status)
            connection.execute(m.WorkspaceMembershipORM.__table__.insert(), {
                key: val for key, val in vars(member).items() if not key.startswith("_")
            })
            user_id = uuid4()
        lead = row(m.LeadORM, id=uuid4(), workspace_id=workspace_id)
        connection.execute(m.LeadORM.__table__.insert(), {
            key: val for key, val in vars(lead).items() if not key.startswith("_")
        })
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert set(connection.scalars(select(WorkspaceTeamORM.id))) == {workspace_id, other_id}
        assert list(connection.scalars(select(WorkspaceTeamMemberORM.membership_id))) == [active_id]
        assert list(connection.scalars(select(WorkspaceTeamLeadORM.lead_id))) == [lead.id]
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert not team_tables.intersection(inspect(connection).get_table_names())
        assert list(connection.scalars(select(m.LeadORM.id))) == [lead.id]
    engine.dispose()


def test_team_migration_renders_postgresql_offline():
    path = Path(__file__).resolve().parents[3] / "infra/migrations/versions/0058_workspace_teams.py"
    spec = importlib.util.spec_from_file_location("team_migration_offline", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    output = StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={
        "as_sql": True, "literal_binds": True, "output_buffer": output,
    })
    with Operations.context(context):
        migration.upgrade()
    sql = output.getvalue()
    assert "CREATE TABLE workspace_teams" in sql
    assert "INSERT INTO workspace_team_members" in sql
    assert "INSERT INTO workspace_team_leads" in sql
    assert "workspace_memberships.status = 'active'" in sql
