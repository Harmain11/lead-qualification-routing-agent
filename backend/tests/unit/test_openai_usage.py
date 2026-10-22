from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.api.routes.integrations import get_openai_usage
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import Base, ProviderLifecycleAuditORM
from lead_agent.infrastructure.reasoning.factory import _workspace_usage_recorder
from lead_agent.infrastructure.repositories import ensure_workspace


def test_usage_is_measured_scoped_and_windowed(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'metering.db'}")
    Base.metadata.create_all(engine)
    workspace_id, other_id = uuid4(), uuid4()
    with Session(engine) as session:
        ensure_workspace(session, workspace_id)
        ensure_workspace(session, other_id)
        session.commit()
        record = _workspace_usage_recorder(session, workspace_id)
        record({
            "operation_id": "operation-1", "model": "test-model", "outcome": "success",
            "input_tokens": 120, "output_tokens": 30, "total_tokens": 150,
            "request_body": "must-not-be-stored", "api_key": "dummy-do-not-store",
        })
        _workspace_usage_recorder(session, other_id)({
            "operation_id": "operation-2", "model": "test-model", "outcome": "success",
            "input_tokens": 900, "output_tokens": 100, "total_tokens": 1000,
        })
        session.add(ProviderLifecycleAuditORM(
            workspace_id=workspace_id, provider="openai", actor_id="test",
            action="openai_api_usage", outcome="success", correlation_id="older",
            details={"model": "test-model", "total_tokens": 10},
            created_at=datetime.now(UTC) - timedelta(days=40),
        ))
        session.commit()
        weekly = get_openai_usage(period="weekly", session=session, workspace=WorkspaceContext(workspace_id))
        assert weekly["requests"] == 1
        assert weekly["input_tokens"] == 120
        assert weekly["total_tokens"] == 150
        quarterly = get_openai_usage(period="quarterly", session=session, workspace=WorkspaceContext(workspace_id))
        assert quarterly["requests"] == 2
        assert quarterly["total_tokens"] == 160
        row = session.query(ProviderLifecycleAuditORM).filter_by(correlation_id="operation-1").one()
        assert "request_body" not in row.details
        assert "api_key" not in row.details
    engine.dispose()
