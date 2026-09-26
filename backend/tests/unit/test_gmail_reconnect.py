from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.application.crm_inbound import run_inbound_sync
from lead_agent.application.integrations import _persist_validation
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.crm_inbound import (
    CanonicalInboundLead,
    ExternalCrmRecord,
    InboundCrmPage,
    InboundCursor,
)
from lead_agent.domain.integrations import ConnectionValidation
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    CrmInboundSyncStateORM,
    CrmProviderRecordLinkORM,
    LeadORM,
    NotificationORM,
    ProviderConnectionORM,
    RoutingDecisionORM,
)
from lead_agent.infrastructure.provider_lifecycle import (
    WorkspaceAuthorizedConnectionResolver,
)
from lead_agent.infrastructure.repositories import ensure_workspace
from lead_agent.infrastructure.secrets import DevelopmentSecretStore


class _OneMessageProvider:
    provider_name = "gmail"

    def __init__(self, connection_id, *, body: str) -> None:
        self.connection_id = connection_id
        self.body = body
        self.cursor_seen: InboundCursor | None = None

    def list_records(
        self, *, object_type: str, cursor: InboundCursor | None, limit: int
    ) -> InboundCrmPage:
        del object_type, limit
        self.cursor_seen = cursor
        return InboundCrmPage(
            records=[
                ExternalCrmRecord(
                    provider="gmail",
                    object_type="message",
                    record_id="gmail-message-1",
                    values={"thread_id": "gmail-thread-1", "body": self.body},
                )
            ],
            checkpoint={"history_id": "902"},
        )

    def fetch_record(self, *, object_type: str, record_id: str) -> ExternalCrmRecord:
        del object_type
        return ExternalCrmRecord(
            provider="gmail",
            object_type="message",
            record_id=record_id,
            values={"thread_id": "gmail-thread-1", "body": self.body},
        )

    def normalize_record(self, record: ExternalCrmRecord) -> CanonicalInboundLead:
        payload = {
            "source": "email",
            "source_detail": "Gmail",
            "external_id": record.record_id,
            "full_name": "Reconnect Example",
            "email": "contact@example.test",
            "company_name": "Example Co",
            "product_or_service": "Routing",
            "message": self.body,
            "gmail_message_id": record.record_id,
            "gmail_thread_id": "gmail-thread-1",
            "gmail_mailbox": "mailbox@example.test",
        }
        return CanonicalInboundLead(
            provider="gmail",
            object_type="message",
            provider_record_id=record.record_id,
            normalized_payload=payload,
            source_fingerprint=hashlib.sha256(self.body.encode()).hexdigest(),
            provider_source="Gmail",
            provider_metadata={
                "gmail_message_id": record.record_id,
                "gmail_thread_id": "gmail-thread-1",
                "gmail_mailbox": "mailbox@example.test",
                "sender_email": "contact@example.test",
            },
        )


def _connection(workspace_id, account: str, *, status="verified", active=True):
    now = datetime.now(UTC)
    return ProviderConnectionORM(
        id=uuid4(),
        workspace_id=workspace_id,
        provider_type="gmail",
        external_account_id=account,
        account_name=account,
        auth_type="oauth2",
        status=status,
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        secret_reference="new-credential-reference" if active else None,
        credential_state="configured" if active else "missing",
        connection_metadata={"gmail_email": account},
        is_active=active,
        created_at=now,
        updated_at=now,
    )


def _db(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gmail-reconnect.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return engine


def test_same_mailbox_reconnect_rebinds_checkpoint_links_and_deduplicates(
    tmp_path: Path, icp: IcpConfig, routing: RoutingConfig
) -> None:
    engine = _db(tmp_path)
    workspace_id = uuid4()
    store = DevelopmentSecretStore()
    with Session(engine, expire_on_commit=False) as session:
        ensure_workspace(session, workspace_id, name="Gmail reconnect workspace")
        old = _connection(workspace_id, "mailbox@example.test", status="verified", active=True)
        session.add(old)
        session.commit()

        first_provider = _OneMessageProvider(old.id, body="We need a demo for our routing team.")
        from lead_agent.application.crm_inbound import configure_inbound_sync

        configure_inbound_sync(
            session,
            old,
            enabled=True,
            object_type="message",
            interval_seconds=300,
            actor_id="admin",
        )
        first = run_inbound_sync(
            session,
            old,
            first_provider,
            mode="initial",
            object_type="message",
            limit=10,
            page_size=10,
            actor_id="scheduler",
            icp=icp,
            routing=routing,
        )
        assert first.status == "completed"
        old.status = "disconnected"
        old.is_active = False
        old.secret_reference = None
        old.credential_state = "missing"
        old.disconnected_at = datetime.now(UTC)
        old_state = session.scalar(
            select(CrmInboundSyncStateORM).where(CrmInboundSyncStateORM.connection_id == old.id)
        )
        assert old_state is not None and old_state.initial_import_completed
        old_state.last_successful_cursor = {"history_id": "901"}
        session.commit()

        old_lead = session.scalar(select(LeadORM))
        assert old_lead is not None
        routing_count = session.scalar(select(func.count()).select_from(RoutingDecisionORM))
        notification_count = session.scalar(select(func.count()).select_from(NotificationORM))
        assert routing_count is not None and notification_count is not None

        new = _connection(workspace_id, "mailbox@example.test", status="connected", active=False)
        session.add(new)
        session.commit()
        validated, result = _persist_validation(
            session,
            new,
            ConnectionValidation(
                valid=True,
                verified=True,
                status="connected",
                credential_state="configured",
                message="Gmail read-only access verified.",
                metadata={"gmail_email": "MAILBOX@example.test", "history_id": "901"},
            ),
            "gmail-admin",
        )
        assert result.verified and validated.is_active
        assert old.secret_reference is None and not old.is_active

        resumed = session.scalar(
            select(CrmInboundSyncStateORM).where(CrmInboundSyncStateORM.connection_id == new.id)
        )
        assert resumed is not None
        assert resumed.id == old_state.id
        assert resumed.enabled
        assert resumed.initial_import_completed
        assert resumed.last_successful_cursor == {"history_id": "901"}
        assert resumed.next_sync_at is not None

        links = list(
            session.scalars(
                select(CrmProviderRecordLinkORM).where(
                    CrmProviderRecordLinkORM.workspace_id == workspace_id,
                    CrmProviderRecordLinkORM.provider == "gmail",
                )
            )
        )
        assert len(links) == 1
        assert links[0].connection_id == new.id
        assert links[0].provider_record_id == "gmail-message-1"

        provider = _OneMessageProvider(new.id, body="We need a demo for our routing team.")
        resumed_run = run_inbound_sync(
            session,
            new,
            provider,
            mode="incremental",
            object_type="message",
            limit=10,
            page_size=10,
            actor_id="scheduler",
            icp=icp,
            routing=routing,
        )
        assert provider.cursor_seen == {"history_id": "901"}
        assert resumed_run.cursor_start == {"history_id": "901"}
        assert resumed_run.records_unchanged == 1
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 1
        assert (
            session.scalar(select(func.count()).select_from(RoutingDecisionORM))
            == routing_count
        )
        assert (
            session.scalar(select(func.count()).select_from(NotificationORM))
            == notification_count
        )
        assert session.scalar(
            select(func.count()).select_from(CrmInboundSyncStateORM).where(
                CrmInboundSyncStateORM.workspace_id == workspace_id,
                CrmInboundSyncStateORM.provider == "gmail",
                CrmInboundSyncStateORM.object_type == "message",
                CrmInboundSyncStateORM.enabled.is_(True),
            )
        ) == 1

        assert (
            WorkspaceAuthorizedConnectionResolver(store).resolve_connection(
                session, workspace_id, old.id, actor_id="scheduler"
            )
            is None
        )

        duplicate = _connection(workspace_id, "mailbox@example.test")
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


@pytest.mark.parametrize("mismatch", ["different-mailbox", "different-workspace"])
def test_gmail_reconnect_does_not_inherit_across_mailbox_or_workspace(
    tmp_path: Path, mismatch: str
) -> None:
    engine = _db(tmp_path)
    old_workspace = uuid4()
    target_workspace = old_workspace if mismatch == "different-mailbox" else uuid4()
    with Session(engine, expire_on_commit=False) as session:
        ensure_workspace(session, old_workspace, name="Source workspace")
        ensure_workspace(session, target_workspace, name="Target workspace")
        old = _connection(
            old_workspace,
            "mailbox@example.test",
            status="disconnected",
            active=False,
        )
        old.secret_reference = None
        old.credential_state = "missing"
        old_state = CrmInboundSyncStateORM(
            id=uuid4(),
            workspace_id=old_workspace,
            connection_id=old.id,
            provider="gmail",
            object_type="message",
            enabled=True,
            initial_import_completed=True,
            sync_interval_seconds=300,
            next_sync_at=None,
            status="ready",
            last_successful_cursor={"history_id": "901"},
            records_processed=3,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        new_mailbox = (
            "other@example.test" if mismatch == "different-mailbox" else "mailbox@example.test"
        )
        new = _connection(target_workspace, new_mailbox, status="connected", active=False)
        session.add_all([old, old_state, new])
        session.commit()

        _persist_validation(
            session,
            new,
            ConnectionValidation(
                valid=True,
                verified=True,
                status="connected",
                credential_state="configured",
                message="Gmail read-only access verified.",
                metadata={"gmail_email": new_mailbox, "history_id": "990"},
            ),
            "gmail-admin",
        )
        assert new.is_active
        assert session.get(CrmInboundSyncStateORM, old_state.id).connection_id == old.id
        assert session.scalar(
            select(CrmInboundSyncStateORM).where(
                CrmInboundSyncStateORM.connection_id == new.id,
            )
        ) is None
