from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.main import app
from lead_agent.application.crm_inbound import configure_inbound_sync, run_inbound_sync
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.crm_inbound import (
    CanonicalInboundLead,
    ExternalCrmRecord,
    InboundCrmPage,
    InboundCursor,
    InboundSyncMode,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    CrmInboundSyncRunORM,
    CrmProviderRecordLinkORM,
    LeadORM,
    ProviderConnectionORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.gmail import (
    GMAIL_READONLY_SCOPE,
    GmailConnectionValidator,
    GmailInboundProvider,
    candidate_reason,
    parse_mime_body,
)
from lead_agent.infrastructure.repositories import ensure_workspace


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_gmail_validator_requires_readonly_scope_and_real_profile_response() -> None:
    validator = GmailConnectionValidator()
    missing = validator.validate("gmail", {"access_token": "secret"}, [])
    assert not missing.verified
    assert missing.status == "insufficient_permissions"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/profile")
        assert request.headers["Authorization"] == "Bearer test-access-token"
        return httpx.Response(200, json={"emailAddress": "reader@example.test", "historyId": "901"})

    result = GmailConnectionValidator(client=_client(handler)).validate(
        "gmail",
        {"access_token": "test-access-token"},
        [GMAIL_READONLY_SCOPE],
    )
    assert result.verified
    assert result.metadata == {"gmail_email": "reader@example.test", "history_id": "901"}


def test_gmail_validator_classifies_api_disabled_scope_and_transport() -> None:
    def disabled(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "message": "Gmail API is disabled",
                    "errors": [{"reason": "accessNotConfigured"}],
                }
            },
        )

    disabled_result = GmailConnectionValidator(client=_client(disabled)).validate(
        "gmail", {"access_token": "token"}, [GMAIL_READONLY_SCOPE]
    )
    assert disabled_result.status == "configuration_error"
    assert disabled_result.error_code == "gmail_api_disabled"

    def insufficient(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "message": "insufficient authentication scopes",
                    "errors": [{"reason": "insufficientPermissions"}],
                }
            },
        )

    scope_result = GmailConnectionValidator(client=_client(insufficient)).validate(
        "gmail", {"access_token": "token"}, [GMAIL_READONLY_SCOPE]
    )
    assert scope_result.status == "insufficient_permissions"


def test_mime_parser_prefers_nested_plain_text_and_html_fallback() -> None:
    plain = parse_mime_body(
        {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/html", "body": {"data": _encoded("<p>HTML offer</p>")}},
                        {
                            "mimeType": "text/plain",
                            "body": {"data": _encoded("Please send pricing for our team")},
                        },
                    ],
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "proposal.pdf",
                    "body": {"attachmentId": "not-downloaded"},
                },
            ],
        }
    )
    assert plain == "Please send pricing for our team"

    html = parse_mime_body(
        {
            "mimeType": "text/html",
            "body": {
                "data": _encoded(
                    "<style>secret()</style><p>Contact our team &amp; discuss a demo</p>"
                )
            },
        }
    )
    assert "Contact our team & discuss a demo" in html
    assert "secret" not in html


def test_mime_parser_bounds_body_size() -> None:
    body = parse_mime_body({"mimeType": "text/plain", "body": {"data": _encoded("x" * 100_000)}})
    assert len(body) <= 32_000


def test_candidate_filter_is_deterministic_and_excludes_own_and_automated_mail() -> None:
    assert (
        candidate_reason(
            {"from": "Owner <owner@client.test>", "subject": "Pricing for our project"},
            "Could we schedule a demo?",
            "intake@company.test",
        )
        is None
    )
    assert (
        candidate_reason(
            {"from": "Intake <intake@company.test>", "subject": "Pricing"},
            "Please call",
            "intake@company.test",
        )
        == "own_or_missing_sender"
    )
    assert (
        candidate_reason(
            {"from": "Updates <no-reply@provider.test>", "subject": "Pricing"},
            "Please call",
            "intake@company.test",
        )
        == "automated_sender"
    )
    assert (
        candidate_reason(
            {"from": "Buyer <buyer@client.test>", "subject": "Weekly newsletter"},
            "Pricing and demo",
            "intake@company.test",
        )
        == "bulk_or_transactional"
    )
    assert (
        candidate_reason(
            {"from": "Buyer <buyer@client.test>", "subject": "Hello"},
            "Just saying hi",
            "intake@company.test",
        )
        == "no_inquiry_signal"
    )


def test_gmail_initial_sync_is_bounded_to_recent_query_and_has_checkpoint() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path.endswith("/profile"):
            return httpx.Response(
                200, json={"historyId": "500", "emailAddress": "inbox@example.test"}
            )
        return httpx.Response(200, json={"messages": [{"id": "m1", "threadId": "t1"}]})

    provider = GmailInboundProvider(
        uuid4(), "access", "inbox@example.test", client=_client(handler)
    )
    page = provider.list_records(object_type="message", cursor=None, limit=25)
    assert [item.record_id for item in page.records] == ["m1"]
    assert page.checkpoint == {"history_id": "500"}
    assert "newer_than%3A30d" in requests[-1]
    assert "maxResults=25" in requests[-1]


def test_gmail_incremental_history_and_expired_history_reconciliation() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/history"):
            if request.url.params.get("startHistoryId") == "expired":
                return httpx.Response(404, json={"error": {"message": "history not found"}})
            return httpx.Response(
                200,
                json={
                    "historyId": "700",
                    "history": [
                        {"messagesAdded": [{"message": {"id": "new", "threadId": "thread"}}]}
                    ],
                },
            )
        if request.url.path.endswith("/profile"):
            return httpx.Response(
                200, json={"historyId": "701", "emailAddress": "inbox@example.test"}
            )
        return httpx.Response(
            200, json={"messages": [{"id": "recent", "threadId": "recent-thread"}]}
        )

    provider = GmailInboundProvider(
        uuid4(), "access", "inbox@example.test", client=_client(handler)
    )
    incremental = provider.list_records(
        object_type="message", cursor={"history_id": "600"}, limit=25
    )
    assert [item.record_id for item in incremental.records] == ["new"]
    assert incremental.checkpoint == {"history_id": "700"}

    reconciled = provider.list_records(
        object_type="message", cursor={"history_id": "expired"}, limit=25
    )
    assert [item.record_id for item in reconciled.records] == ["recent"]
    assert reconciled.checkpoint == {"history_id": "701"}
    assert calls.count("/gmail/v1/users/me/history") == 2
    assert calls[-1].endswith("messages")


def test_gmail_normalization_preserves_provider_and_message_thread_provenance() -> None:
    provider = GmailInboundProvider(uuid4(), "access", "inbox@example.test")
    record = ExternalCrmRecord(
        provider="gmail",
        object_type="message",
        record_id="message-1",
        values={
            "thread_id": "thread-9",
            "headers": {
                "from": "Mira Example <Mira@Client.example>",
                "reply-to": "sales@client.example",
                "subject": "Pricing request",
            },
            "body": "Please send pricing for our team.",
        },
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    canonical = provider.normalize_record(record)
    assert canonical.provider == "gmail"
    assert canonical.provider_record_id == "message-1"
    assert canonical.provider_metadata["gmail_thread_id"] == "thread-9"
    assert canonical.normalized_payload["source"] == "email"
    assert canonical.normalized_payload["gmail_message_id"] == "message-1"
    assert canonical.normalized_payload["reply_to"] == "sales@client.example"


class _CanonicalGmailProvider:
    provider_name = "gmail"

    def __init__(self, connection_id: UUID, messages: list[ExternalCrmRecord]) -> None:
        self.connection_id = connection_id
        self.messages = messages

    def list_records(
        self, *, object_type: str, cursor: InboundCursor | None, limit: int
    ) -> InboundCrmPage:
        del object_type, cursor
        return InboundCrmPage(
            records=self.messages[:limit],
            checkpoint={"history_id": str(len(self.messages) + 100)},
        )

    def fetch_record(self, *, object_type: str, record_id: str) -> ExternalCrmRecord:
        del object_type
        return next(message for message in self.messages if message.record_id == record_id)

    def normalize_record(self, record: ExternalCrmRecord) -> CanonicalInboundLead:
        values = record.values
        content = str(values["body"])
        payload = {
            "source": "email",
            "source_detail": "Gmail",
            "external_id": record.record_id,
            "full_name": "Mira Example",
            "email": "mira@client.example",
            "company_name": "Client Example",
            "product_or_service": "Inbound inquiry",
            "message": content,
            "received_at": record.modified_at,
            "gmail_message_id": record.record_id,
            "gmail_thread_id": str(values["thread_id"]),
            "gmail_mailbox": "inbox@example.test",
        }
        return CanonicalInboundLead(
            provider="gmail",
            object_type="message",
            provider_record_id=record.record_id,
            normalized_payload=payload,
            source_fingerprint=hashlib.sha256(content.encode()).hexdigest(),
            provider_modified_at=record.modified_at,
            provider_source="Gmail",
            provider_metadata={
                "gmail_message_id": record.record_id,
                "gmail_thread_id": str(values["thread_id"]),
                "gmail_mailbox": "inbox@example.test",
                "sender_email": "mira@client.example",
            },
        )


def test_gmail_message_ids_deduplicate_and_new_thread_message_updates_same_contact(
    tmp_path: Path, icp: IcpConfig, routing: RoutingConfig
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gmail.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        workspace_id = uuid4()
        ensure_workspace(session, workspace_id, name="Gmail test workspace")
        now = datetime.now(UTC)
        connection = ProviderConnectionORM(
            id=uuid4(),
            workspace_id=workspace_id,
            provider_type="gmail",
            external_account_id="inbox@example.test",
            account_name="inbox@example.test",
            auth_type="oauth2",
            status="verified",
            scopes=[GMAIL_READONLY_SCOPE],
            secret_reference="encrypted-reference",
            credential_state="configured",
            connection_metadata={"gmail_email": "inbox@example.test"},
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(connection)
        session.commit()

        def message(message_id: str, body: str) -> ExternalCrmRecord:
            return ExternalCrmRecord(
                provider="gmail",
                object_type="message",
                record_id=message_id,
                values={"thread_id": "thread-1", "body": body},
                modified_at=now,
            )

        def run(provider: _CanonicalGmailProvider, mode: InboundSyncMode) -> CrmInboundSyncRunORM:
            configure_inbound_sync(
                session, connection, enabled=True, object_type="message", actor_id="admin"
            )
            return run_inbound_sync(
                session,
                connection,
                provider,
                mode=mode,
                object_type="message",
                limit=10,
                page_size=10,
                actor_id="scheduler",
                icp=icp,
                routing=routing,
            )

        first = run(
            _CanonicalGmailProvider(connection.id, [message("m1", "We need a demo for our team.")]),
            "initial",
        )
        lead = session.scalar(select(LeadORM))
        assert lead is not None and lead.source == "email" and lead.external_id == "m1"
        first_decisions = session.scalar(
            select(func.count()).select_from(models.RoutingDecisionORM)
        )
        assert first_decisions is not None
        second = run(
            _CanonicalGmailProvider(
                connection.id, [message("m2", "Please schedule a demo next week for our team.")]
            ),
            "incremental",
        )
        links = list(
            session.scalars(
                select(CrmProviderRecordLinkORM).where(
                    CrmProviderRecordLinkORM.connection_id == connection.id
                )
            )
        )
        assert first.records_created == 1
        assert second.records_updated == 1
        assert len(links) == 2
        assert {link.provider_record_id for link in links} == {"m1", "m2"}
        assert all(link.provider == "gmail" and link.object_type == "message" for link in links)
        assert all(link.provider_metadata["gmail_thread_id"] == "thread-1" for link in links)

        repeated = run(
            _CanonicalGmailProvider(
                connection.id, [message("m2", "Please schedule a demo next week for our team.")]
            ),
            "manual",
        )
        assert repeated.records_unchanged == 1
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 1
        assert (
            session.scalar(select(func.count()).select_from(models.RoutingDecisionORM))
            == first_decisions + 1
        )


def test_gmail_admin_settings_require_manage_and_sync_requires_sync_permission(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gmail-rbac.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id = uuid4()
    ensure_workspace(session, workspace_id, name="Gmail RBAC workspace")
    now = datetime.now(UTC)
    connection = ProviderConnectionORM(
        id=uuid4(),
        workspace_id=workspace_id,
        provider_type="gmail",
        external_account_id="inbox@example.test",
        account_name="inbox@example.test",
        auth_type="oauth2",
        status="verified",
        scopes=[GMAIL_READONLY_SCOPE],
        secret_reference="encrypted-reference",
        credential_state="configured",
        connection_metadata={"gmail_email": "inbox@example.test"},
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(connection)
    session.commit()

    def override_db() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        workspace_id=workspace_id,
        actor_id="integration-admin",
        role="custom",
        permissions=frozenset({"integrations.manage", "integrations.view"}),
    )
    try:
        with TestClient(app) as client:
            configured = client.put(
                f"/v1/integrations/connections/{connection.id}/inbound-sync/configuration",
                json={"enabled": True, "object_type": "message"},
            )
            assert configured.status_code == 200
            app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
                workspace_id=workspace_id,
                actor_id="limited-agent",
                role="custom",
                permissions=frozenset({"agents.execute"}),
            )
            configure_denied = client.put(
                f"/v1/integrations/connections/{connection.id}/inbound-sync/configuration",
                json={"enabled": False, "object_type": "message"},
            )
            sync_denied = client.post(
                f"/v1/integrations/connections/{connection.id}/inbound-sync",
                json={"mode": "manual", "object_type": "message", "limit": 1},
            )
        assert configure_denied.status_code == 403
        assert sync_denied.status_code == 403
    finally:
        app.dependency_overrides.clear()
        session.close()
