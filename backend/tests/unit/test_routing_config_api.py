from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.main import app
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import UserORM, WorkspaceMembershipORM, WorkspaceORM
from lead_agent.infrastructure.db.session import get_db


def test_routing_config_uses_unambiguous_route_and_saves_weights(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'routing-config-api.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    now = datetime.now(UTC)
    workspace_id, user_id = uuid4(), uuid4()
    session.add(WorkspaceORM(
        id=workspace_id, name="Routing config QA", slug=f"routing-{uuid4().hex}",
        status="active", is_demo=False, created_at=now, updated_at=now,
    ))
    session.add(UserORM(
        id=user_id, email="routing-qa@example.test", display_name="Routing QA",
        password_hash="test-hash", status="active", created_at=now, updated_at=now,
    ))
    session.add(WorkspaceMembershipORM(
        id=uuid4(), workspace_id=workspace_id, user_id=user_id, role="sales_rep",
        status="active", created_at=now, updated_at=now,
    ))
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        workspace_id=workspace_id, user_id=user_id, actor_id="routing-qa",
        permissions=frozenset({"routing.view", "routing.manage"}),
    )
    try:
        with TestClient(app) as client:
            before = client.get("/v1/routing/config")
            saved = client.put(
                "/v1/routing/config", json={"member_weights": {str(user_id): 2.5}}
            )
            after = client.get("/v1/routing/config")
        assert before.status_code == 200
        assert before.json()["members"][0]["user_id"] == str(user_id)
        assert saved.status_code == 200
        assert after.json()["members"][0]["weight"] == 2.5
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()
