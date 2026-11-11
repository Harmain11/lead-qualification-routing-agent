from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from lead_agent.api.routes import integrations as integrations_api
from lead_agent.api.routes.integrations import (
    _enqueue_reasoning_backfill,
    list_integrations,
    reasoning_backfill_status,
)
from lead_agent.application.crm import manually_sync_lead_to_crm
from lead_agent.application.crm_inbound import (
    claim_due_inbound_sync_states,
    configure_inbound_sync,
    get_inbound_sync_state,
    list_due_inbound_sync_states,
)
from lead_agent.application.services import process_lead, reprocess_lead
from lead_agent.domain.analysis import ReasoningProviderError, ReasoningProviderTimeout
from lead_agent.domain.integration_directionality import directionality_for
from lead_agent.domain.integrations import PROVIDER_SETUP
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.crm.factory import ConnectedCrmProvider
from lead_agent.infrastructure.crm.mock import MockCrmProvider
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    CrmProviderRecordLinkORM,
    CrmSyncORM,
    NotificationORM,
    ProviderConnectionORM,
    ProviderLifecycleAuditORM,
)
from lead_agent.infrastructure.repositories import ensure_workspace, get_current_routing
from lead_agent.settings.config import get_settings


def _session(tmp_path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'directionality.db'}")
    Base.metadata.create_all(engine)
    return Session(engine)


def _payload(external_id: str = "directionality-1") -> dict[str, object]:
    return {
        "external_id": external_id,
        "source": "website",
        "full_name": "Jamie Rivera",
        "email": "jamie@example.test",
        "job_title": "VP Operations",
        "company_name": "Directionality Labs",
        "industry": "SaaS",
        "employee_count": 150,
        "geography": "United States",
        "budget": 25_000,
        "product_or_service": "Lead routing",
        "message": "We need qualification and routing this month for our sales team.",
    }


def _connection(session: Session, workspace_id, provider: str = "salesforce"):
    ensure_workspace(session, workspace_id)
    now = datetime.now(UTC)
    row = ProviderConnectionORM(
        id=uuid4(),
        workspace_id=workspace_id,
        provider_type=provider,
        account_name="Directionality test",
        auth_type="oauth2",
        status="verified",
        scopes=["api", "refresh_token"],
        credential_state="configured",
        last_validation_result={"verified": True},
        connection_metadata={},
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return row


def test_provider_directionality_policy_is_explicit() -> None:
    crm = directionality_for("salesforce")
    assert crm.automatic_inbound is True
    assert crm.manual_outbound is True
    assert crm.automatic_outbound is False
    assert directionality_for("hubspot") == crm
    assert directionality_for("gmail").automatic_inbound is True
    assert directionality_for("gmail").manual_outbound is False
    assert directionality_for("slack").outbound_action is True
    assert directionality_for("slack").automatic_inbound is False
    assert directionality_for("slack").description == (
        "Notifications are outbound delivery actions produced by routing policy."
    )
    assert directionality_for("openai").request_response is True
    assert directionality_for("gemini").request_response is True
    assert directionality_for("claude").request_response is True
    assert directionality_for("deepseek").request_response is True
    assert directionality_for("xai").request_response is True


def test_integrations_catalog_loads_all_ai_provider_cards(tmp_path) -> None:
    workspace_id = uuid4()
    with _session(tmp_path) as session:
        cards = list_integrations(
            session=session,
            workspace=WorkspaceContext(workspace_id),
            settings=get_settings(),
        )

    reasoning_cards = {
        card.provider: card for card in cards
        if card.provider in {"openai", "gemini", "claude", "deepseek", "xai"}
    }
    assert set(reasoning_cards) == {"openai", "gemini", "claude", "deepseek", "xai"}
    assert reasoning_cards["gemini"].supported_models
    assert reasoning_cards["claude"].supported_models
    assert reasoning_cards["deepseek"].supported_models == ["deepseek-flash", "deepseek-v4-pro"]
    assert reasoning_cards["xai"].supported_models == ["grok-4.7"]


def test_release_provider_catalog_separates_available_managed_and_coming_soon() -> None:
    assert {
        provider
        for provider, setup in PROVIDER_SETUP.items()
        if setup.availability == "available" and setup.connection_mode == "workspace"
    } == {
        "gmail",
        "salesforce",
        "hubspot",
        "slack",
        "openai",
        "gemini",
        "claude",
        "deepseek",
        "xai",
        "google_calendar",
        "calendly",
        "gohighlevel",
        "twilio",
        "aircall",
        "ringcentral",
        "vapi",
        "retell",
            "cloudtalk",
    }
    assert PROVIDER_SETUP["openai"].display_name == "OpenAI"
    assert PROVIDER_SETUP["openai"].connection_mode == "workspace"
    assert PROVIDER_SETUP["openai"].manual_fields == ["api_key"]
    assert PROVIDER_SETUP["outlook"].display_name == "Microsoft Outlook"
    assert PROVIDER_SETUP["outlook"].availability == "future"
    assert PROVIDER_SETUP["linkedin"].availability == "future"


def test_local_creation_and_requalification_never_write_to_crm(
    tmp_path, icp, routing
) -> None:
    provider = MockCrmProvider()
    with _session(tmp_path) as session:
        _, _, lead = process_lead(session, _payload(), icp, routing)
        reprocess_lead(session, lead, _payload(), icp, routing)
        assert provider.calls == 0
        assert session.scalar(select(func.count()).select_from(CrmSyncORM)) == 0


def test_analysis_backfill_reroutes_without_sending_new_notifications(
    tmp_path, icp, routing
) -> None:
    with _session(tmp_path) as session:
        _, _, lead = process_lead(session, _payload(), icp, routing)
        before = session.scalar(select(func.count()).select_from(NotificationORM))
        reprocess_lead(
            session,
            lead,
            _payload(),
            icp,
            routing,
            send_notification=False,
            reroute_reason="Lead reanalyzed with the verified workspace model.",
        )
        current = get_current_routing(session, lead.id)
        assert current is not None
        assert current.notification_state == "suppressed"
        assert session.scalar(select(func.count()).select_from(NotificationORM)) == before


def test_analysis_backfill_keeps_existing_result_when_provider_fails(
    tmp_path, icp, routing
) -> None:
    class FailingProvider:
        provider_name = "failing"
        model_name = "none"
        prompt_version = "test"

        def analyze(self, lead, configuration):
            del lead, configuration
            raise RuntimeError("provider unavailable")

    with _session(tmp_path) as session:
        _, _, lead = process_lead(session, _payload(), icp, routing)
        previous_status = lead.analysis.status
        with pytest.raises(ReasoningProviderError):
            reprocess_lead(
                session,
                lead,
                _payload(),
                icp,
                routing,
                provider=FailingProvider(),
                send_notification=False,
                require_provider_success=True,
            )
        session.refresh(lead.analysis)
        assert lead.analysis.status == previous_status


def test_new_and_materially_updated_leads_enqueue_background_reasoning(
    tmp_path, icp, routing, monkeypatch
) -> None:
    class FailingProvider:
        provider_name = "openai"
        model_name = "test-model"
        prompt_version = "test-prompt"

        def analyze(self, lead, configuration):
            del lead, configuration
            raise RuntimeError("temporary provider failure")

    queued: list[tuple[object, object]] = []
    monkeypatch.setattr(
        "lead_agent.application.services.resolve_workspace_reasoning_provider",
        lambda session, workspace_id: FailingProvider(),
    )
    monkeypatch.setattr(
        "lead_agent.application.services._queue_reasoning_analysis",
        lambda workspace_id, lead_id: queued.append((workspace_id, lead_id)),
    )
    workspace_id = uuid4()
    with _session(tmp_path) as session:
        _, _, lead = process_lead(
            session, _payload(), icp, routing, workspace_id=workspace_id
        )
        reprocess_lead(
            session, lead, _payload("updated-source"), icp, routing
        )
    assert [item[1] for item in queued] == [lead.id, lead.id]


def test_reasoning_backfill_persists_no_eligible_state_and_exposes_status(
    tmp_path, monkeypatch
) -> None:
    class FakeRedis:
        values: dict[str, str] = {}

        @classmethod
        def from_url(cls, *_args, **_kwargs):
            return cls()

        def ping(self):
            return True

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value, nx=False, ex=None):
            del ex
            if nx and key in self.values:
                return False
            self.values[key] = value
            return True

        def eval(self, _script, _count, key, job_id):
            if self.values.get(key) == job_id:
                del self.values[key]

        def close(self):
            pass

    monkeypatch.setattr("lead_agent.api.routes.integrations.Redis", FakeRedis)
    monkeypatch.setattr(
        "lead_agent.api.routes.integrations._reasoning_backfill_snapshot",
        lambda *_args: ("openai", "test-model", "openai|test-model|prompt-v1"),
    )
    workspace_id = uuid4()
    with _session(tmp_path) as session:
        connection = _connection(session, workspace_id, "openai")
        result = _enqueue_reasoning_backfill(session, connection, get_settings())
        assert result["status"] == "no_eligible_leads"
        status_result = reasoning_backfill_status(
            connection.id,
            str(result["job_id"]),
            session=session,
            workspace=WorkspaceContext(workspace_id),
        )
        assert status_result == {
            "job_id": result["job_id"],
            "status": "no_eligible_leads",
            "total": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "failure_categories": {},
            "failures": [],
            "provider": "openai",
            "model": "test-model",
        }


def test_reasoning_backfill_status_reports_failed_leads_in_workspace(
    tmp_path,
) -> None:
    from datetime import UTC, datetime

    workspace_id = uuid4()
    other_workspace_id = uuid4()
    with _session(tmp_path) as session:
        connection = _connection(session, workspace_id, "openai")
        other_connection = _connection(session, other_workspace_id, "openai")
        job_id = str(uuid4())
        now = datetime.now(UTC)
        session.add_all([
            ProviderLifecycleAuditORM(
                id=uuid4(),
                workspace_id=workspace_id,
                connection_id=connection.id,
                provider="openai",
                actor_id="system",
                action="reasoning_backfill",
                outcome="partial_failure",
                correlation_id=job_id,
                details={
                    "status": "partial_failure",
                    "total": 2,
                    "completed": 1,
                    "failed": 1,
                    "skipped": 0,
                    "provider": "openai",
                    "model": "test-model",
                },
                created_at=now,
            ),
            ProviderLifecycleAuditORM(
                id=uuid4(),
                workspace_id=workspace_id,
                connection_id=connection.id,
                provider="openai",
                actor_id="worker",
                action="reasoning_backfill",
                outcome="lead_failed",
                correlation_id=job_id,
                details={"lead_id": "failed-lead", "failure_code": "provider_timeout"},
                created_at=now,
            ),
            ProviderLifecycleAuditORM(
                id=uuid4(),
                workspace_id=other_workspace_id,
                connection_id=other_connection.id,
                provider="openai",
                actor_id="worker",
                action="reasoning_backfill",
                outcome="lead_failed",
                correlation_id=job_id,
                details={"lead_id": "other-workspace-lead", "failure_code": "provider_error"},
                created_at=now,
            ),
        ])
        session.commit()
        result = reasoning_backfill_status(
            connection.id,
            job_id,
            session=session,
            workspace=WorkspaceContext(workspace_id),
        )
        assert result["failures"] == [
            {"lead_id": "failed-lead", "code": "provider_timeout"}
        ]


def test_reasoning_backfill_records_provider_change_as_terminal_failure(
    tmp_path, icp, routing, monkeypatch
) -> None:
    from types import SimpleNamespace

    from lead_agent.worker.celery_app import reanalyze_workspace_leads

    workspace_id = uuid4()
    job_id = str(uuid4())
    with _session(tmp_path) as session:
        connection = _connection(session, workspace_id, "openai")
        session_factory = sessionmaker(bind=session.get_bind())
        monkeypatch.setattr(
            "lead_agent.worker.celery_app.get_session_factory",
            lambda: session_factory,
        )
        monkeypatch.setattr(
            "lead_agent.worker.celery_app.settings",
            SimpleNamespace(
                app_env="test",
                load_policies=lambda: (icp, routing),
            ),
        )
        monkeypatch.setattr(
            "lead_agent.worker.celery_app.resolve_workspace_reasoning_provider",
            lambda *_args: SimpleNamespace(
                provider_name="openai",
                model_name="new-model",
                prompt_version="prompt-v2",
            ),
        )

        result = reanalyze_workspace_leads.run(
            str(workspace_id),
            str(connection.id),
            job_id,
            "openai|old-model|prompt-v1",
        )
        assert result.startswith("failed:")
        status = reasoning_backfill_status(
            connection.id,
            job_id,
            session=session,
            workspace=WorkspaceContext(workspace_id),
        )
        assert status["status"] == "failed"
        assert status["message"].startswith(
            "The verified workspace AI configuration changed"
        )


def test_verified_reasoning_connection_automatically_enqueues_backfill(
    tmp_path, monkeypatch
) -> None:
    from lead_agent.domain.integrations import ConnectionValidation

    workspace_id = uuid4()
    with _session(tmp_path) as session:
        connection = _connection(session, workspace_id, "openai")
        validation = ConnectionValidation(
            valid=True,
            verified=True,
            status="connected",
            credential_state="configured",
            message="Verified",
        )
        monkeypatch.setattr(
            "lead_agent.api.routes.integrations.validate_connection",
            lambda *_args: (connection, validation),
        )
        monkeypatch.setattr(
            "lead_agent.api.routes.integrations._enqueue_reasoning_backfill",
            lambda *_args: {"job_id": "job-123", "status": "queued", "queued_count": 1},
        )
        response = integrations_api.test_connection(
            connection.id,
            session=session,
            workspace=WorkspaceContext(workspace_id),
            secret_store=None,
            broker=None,
            validator=None,
            settings=get_settings(),
        )
        assert response.verified is True
        assert response.backfill_job_id == "job-123"


def test_reasoning_backfill_deduplicates_an_active_configuration(
    tmp_path, icp, routing, monkeypatch
) -> None:
    from types import SimpleNamespace

    class FakeRedis:
        values: dict[str, str] = {}

        @classmethod
        def from_url(cls, *_args, **_kwargs):
            return cls()

        def ping(self):
            return True

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value, nx=False, ex=None):
            del ex
            if nx and key in self.values:
                return False
            self.values[key] = value
            return True

        def close(self):
            pass

    monkeypatch.setattr("lead_agent.api.routes.integrations.Redis", FakeRedis)
    monkeypatch.setattr(
        "lead_agent.api.routes.integrations._reasoning_backfill_snapshot",
        lambda *_args: ("openai", "test-model", "openai|test-model|prompt-v1"),
    )
    queued: list[tuple[str, ...]] = []
    from lead_agent.worker.celery_app import reanalyze_workspace_leads

    monkeypatch.setattr(reanalyze_workspace_leads, "delay", lambda *args: queued.append(args))
    production_settings = SimpleNamespace(
        app_env="production",
        redis_required=True,
        redis_url="redis://unused",
    )
    workspace_id = uuid4()
    with _session(tmp_path) as session:
        connection = _connection(session, workspace_id, "openai")
        process_lead(session, _payload(), icp, routing, workspace_id=workspace_id)
        first = _enqueue_reasoning_backfill(session, connection, production_settings)
        second = _enqueue_reasoning_backfill(session, connection, production_settings)
        assert first["status"] == "queued"
        assert second["status"] == "already_queued"
        assert first["job_id"] == second["job_id"]
        assert len(queued) == 1


def test_preview_reasoning_backfill_uses_local_worker_without_redis(
    tmp_path, icp, routing, monkeypatch
) -> None:
    from types import SimpleNamespace

    workspace_id = uuid4()
    reserved: dict[str, str] = {}
    queued: list[tuple[str, ...]] = []

    def reserve(lock_key: str, job_id: str) -> str | None:
        current = reserved.get(lock_key)
        if current:
            return current
        reserved[lock_key] = job_id
        return None

    monkeypatch.setattr(
        "lead_agent.api.routes.integrations._reasoning_backfill_snapshot",
        lambda *_args: ("openai", "test-model", "openai|test-model|prompt-v1"),
    )
    monkeypatch.setattr(
        "lead_agent.worker.preview_sync.reserve_reasoning_backfill", reserve
    )
    monkeypatch.setattr(
        "lead_agent.worker.preview_sync.enqueue_reserved_reasoning_backfill",
        lambda *args: queued.append(args),
    )
    monkeypatch.setattr(
        "lead_agent.api.routes.integrations.Redis.from_url",
        lambda *_args, **_kwargs: pytest.fail("Preview backfills must not require Redis"),
    )

    with _session(tmp_path) as session:
        connection = _connection(session, workspace_id, "openai")
        process_lead(session, _payload(), icp, routing, workspace_id=workspace_id)
        result = _enqueue_reasoning_backfill(
            session,
            connection,
            SimpleNamespace(
                app_env="development",
                redis_required=False,
                redis_url="redis://unused",
            ),
        )

    assert result["status"] == "queued"
    assert result["queued_count"] == 1
    assert len(queued) == 1


def test_background_analysis_retries_provider_failure_and_remembers_completion(
    tmp_path, icp, routing, monkeypatch
) -> None:
    from contextlib import nullcontext
    from types import SimpleNamespace

    import lead_agent.worker.celery_app as worker

    class Provider:
        provider_name = "openai"
        model_name = "test-model"
        prompt_version = "prompt-v1"

    workspace_id = uuid4()
    with _session(tmp_path) as session:
        _connection(session, workspace_id, "openai")
        _, _, lead = process_lead(
            session, _payload(), icp, routing, workspace_id=workspace_id
        )
        monkeypatch.setattr(
            worker, "get_session_factory", lambda: lambda: nullcontext(session)
        )
        monkeypatch.setattr(
            worker, "resolve_workspace_reasoning_provider", lambda *_args: Provider()
        )
        monkeypatch.setattr(
            worker, "settings", SimpleNamespace(load_policies=lambda: (icp, routing))
        )
        attempts = 0

        def flaky_reprocess(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ReasoningProviderTimeout("temporary provider failure")

        monkeypatch.setattr(worker, "reprocess_lead", flaky_reprocess)
        result = worker.analyze_workspace_lead.run(str(workspace_id), str(lead.id))
        assert result == "completed"
        assert attempts == 3
        assert worker.analyze_workspace_lead.run(str(workspace_id), str(lead.id)).startswith(
            "skipped:"
        )
        assert attempts == 3

        # A same-provider receipt is not enough after the lead changes.
        lead.message = "The lead now has a different inquiry context."
        session.commit()
        assert worker.analyze_workspace_lead.run(str(workspace_id), str(lead.id)) == "completed"
        assert attempts == 4
        assert worker.analyze_workspace_lead.run(str(workspace_id), str(lead.id)).startswith(
            "skipped:"
        )
        assert attempts == 4


def test_new_connection_is_disabled_until_deliberately_enabled(tmp_path) -> None:
    workspace_id = uuid4()
    with _session(tmp_path) as session:
        connection = _connection(session, workspace_id)
        enabled = configure_inbound_sync(
            session,
            connection,
            enabled=True,
            interval_seconds=600,
            actor_id="admin@example.test",
        )
        assert enabled.enabled is True
        assert enabled.initial_import_completed is False
        assert enabled.next_sync_at is not None
        assert enabled.sync_interval_seconds == 600
        assert list_due_inbound_sync_states(session, now=datetime.now(UTC)) == [enabled]


def test_due_claim_advances_schedule_and_prevents_duplicate_dispatch(tmp_path) -> None:
    with _session(tmp_path) as session:
        connection = _connection(session, uuid4())
        state = configure_inbound_sync(
            session,
            connection,
            enabled=True,
            interval_seconds=300,
            actor_id="admin",
        )
        due_at = datetime.now(UTC)
        assert [row.id for row in claim_due_inbound_sync_states(session, now=due_at)] == [
            state.id
        ]
        assert list_due_inbound_sync_states(session, now=due_at) == []


def test_disabling_inbound_preserves_links_and_local_business_data(
    tmp_path, icp, routing
) -> None:
    with _session(tmp_path) as session:
        workspace_id = uuid4()
        connection = _connection(session, workspace_id)
        _, _, lead = process_lead(
            session, _payload(), icp, routing, workspace_id=workspace_id
        )
        now = datetime.now(UTC)
        session.add(
            CrmProviderRecordLinkORM(
                id=uuid4(),
                workspace_id=workspace_id,
                connection_id=connection.id,
                provider="salesforce",
                object_type="Lead",
                provider_record_id="00Q-preserved",
                lead_id=lead.id,
                status="active",
                matching_strategy="provider_id",
                provider_metadata={},
                source_fingerprint="f" * 64,
                synchronization_origin="provider",
                last_change_kind="source_changed",
                last_seen_at=now,
                last_imported_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        configure_inbound_sync(session, connection, enabled=True, actor_id="admin")
        state = configure_inbound_sync(session, connection, enabled=False, actor_id="admin")
        assert state.enabled is False and state.next_sync_at is None
        assert session.get(type(lead), lead.id) is not None
        assert session.scalar(select(func.count()).select_from(CrmProviderRecordLinkORM)) == 1


def test_manual_outbound_command_records_actor_and_is_retry_safe(
    tmp_path, icp, routing
) -> None:
    with _session(tmp_path) as session:
        workspace_id = uuid4()
        connection = _connection(session, workspace_id, provider="mock")
        _, _, lead = process_lead(
            session, _payload(), icp, routing, workspace_id=workspace_id
        )
        mock = MockCrmProvider()
        provider = ConnectedCrmProvider(mock, connection.id)
        first = manually_sync_lead_to_crm(
            session, lead, provider, actor_id="seller@example.test"
        )
        replay = manually_sync_lead_to_crm(
            session, lead, provider, actor_id="seller@example.test"
        )
        assert first.id == replay.id
        assert mock.calls == 1
        audits = list(
            session.scalars(
                select(ProviderLifecycleAuditORM).where(
                    ProviderLifecycleAuditORM.connection_id == connection.id,
                    ProviderLifecycleAuditORM.action == "manual_crm_send",
                )
            )
        )
        assert {row.outcome for row in audits} == {"started", "completed"}
        assert {row.actor_id for row in audits} == {"seller@example.test"}
        assert get_inbound_sync_state(session, workspace_id, connection.id) is None
