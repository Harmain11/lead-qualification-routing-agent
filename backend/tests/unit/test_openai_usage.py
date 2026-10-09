from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.api.routes.integrations import get_openai_usage, get_reasoning_usage
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


def test_reasoning_usage_summarizes_each_provider_and_model_without_leaking_details(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'reasoning-usage.db'}")
    Base.metadata.create_all(engine)
    workspace_id, other_id, empty_id = uuid4(), uuid4(), uuid4()
    with Session(engine) as session:
        for workspace in (workspace_id, other_id, empty_id):
            ensure_workspace(session, workspace)
        session.commit()

        _workspace_usage_recorder(session, workspace_id, "openai")({
            "operation_id": "openai-success",
            "model": "gpt-test",
            "outcome": "success",
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "prompt": "private prompt",
            "lead_content": "private lead content",
            "api_key": "private key",
        })
        _workspace_usage_recorder(session, workspace_id, "openai")({
            "operation_id": "openai-failure",
            "model": "gpt-test",
            "outcome": "invalid_output",
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        })
        _workspace_usage_recorder(session, workspace_id, "gemini")({
            "operation_id": "gemini-success",
            "model": "gemini-test",
            "outcome": "success",
            "input_tokens": 80,
            "output_tokens": 12,
            "total_tokens": 92,
        })
        _workspace_usage_recorder(session, workspace_id, "claude")({
            "operation_id": "claude-failure",
            "model": "claude-test",
            "outcome": "invalid_output",
            "input_tokens": 60,
            "output_tokens": 8,
            "total_tokens": 68,
        })
        _workspace_usage_recorder(session, other_id, "claude")({
            "operation_id": "other-workspace",
            "model": "claude-test",
            "outcome": "success",
            "input_tokens": 900,
            "output_tokens": 100,
            "total_tokens": 1000,
        })
        session.commit()

        report = get_reasoning_usage(
            period="monthly",
            session=session,
            workspace=WorkspaceContext(workspace_id),
        )
        by_provider = {item["provider"]: item for item in report["providers"]}

        assert list(by_provider) == ["openai", "gemini", "claude"]
        assert by_provider["openai"]["requests"] == 2
        assert by_provider["openai"]["successful_requests"] == 1
        assert by_provider["openai"]["failed_requests"] == 1
        assert by_provider["openai"]["input_tokens"] == 110
        assert by_provider["openai"]["output_tokens"] == 25
        assert by_provider["openai"]["total_tokens"] == 135
        assert by_provider["openai"]["models"] == [{
            "model": "gpt-test",
            "requests": 2,
            "successful_requests": 1,
            "failed_requests": 1,
            "input_tokens": 110,
            "output_tokens": 25,
            "total_tokens": 135,
        }]
        assert by_provider["gemini"]["requests"] == 1
        assert by_provider["gemini"]["successful_requests"] == 1
        assert by_provider["gemini"]["models"][0]["model"] == "gemini-test"
        assert by_provider["claude"]["requests"] == 1
        assert by_provider["claude"]["failed_requests"] == 1
        assert by_provider["claude"]["total_tokens"] == 68

        provider_fields = {
            "provider", "requests", "successful_requests", "failed_requests",
            "input_tokens", "output_tokens", "total_tokens", "models",
        }
        assert all(set(provider) == provider_fields for provider in report["providers"])
        serialized = str(report)
        for private_value in ("private prompt", "private lead content", "private key", "other-workspace"):
            assert private_value not in serialized

        empty_report = get_reasoning_usage(
            period="weekly",
            session=session,
            workspace=WorkspaceContext(empty_id),
        )
        assert len(empty_report["providers"]) == 3
        assert all(provider["requests"] == 0 for provider in empty_report["providers"])
        assert all(provider["models"] == [] for provider in empty_report["providers"])
    engine.dispose()
