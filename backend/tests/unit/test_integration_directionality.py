from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from lead_agent.api.routes.integrations import list_integrations
from lead_agent.application.crm import manually_sync_lead_to_crm
from lead_agent.application.crm_inbound import (
    claim_due_inbound_sync_states,
    configure_inbound_sync,
    get_inbound_sync_state,
    list_due_inbound_sync_states,
)
from lead_agent.application.services import process_lead, reprocess_lead
from lead_agent.domain.analysis import ReasoningProviderError
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
        "outlook",
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
    }
    assert PROVIDER_SETUP["openai"].display_name == "OpenAI"
    assert PROVIDER_SETUP["openai"].connection_mode == "workspace"
    assert PROVIDER_SETUP["openai"].manual_fields == ["api_key"]
    assert PROVIDER_SETUP["outlook"].display_name == "Microsoft Outlook"
    assert PROVIDER_SETUP["outlook"].availability == "available"
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
