from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.api.routes import email as email_routes
from lead_agent.api.routes.email import (
    GMAIL_SEND_SCOPE,
    ComposeWithAIRequest,
    GmailSendAccountResponse,
    SaveEmailDraftRequest,
    SendEmailRequest,
    _sanitize_email_html,
    compose_email_with_ai,
    create_email_draft,
    delete_email_draft,
    get_email_draft,
    list_email_drafts,
    list_gmail_send_accounts,
    update_email_draft,
)
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import WorkspaceEmailDraftORM, WorkspaceORM


def test_ai_composer_uses_matching_thread_and_preserves_reply_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(workspace_id=uuid4(), actor_id="owner")
    account = SimpleNamespace(
        id=uuid4(), connection_metadata={"gmail_email": "team@example.test"}, account_name="Team"
    )
    thread = [
        {
            "sender": "client@example.test",
            "recipients": "team@example.test",
            "direction": "inbound",
            "subject": "Re: Demo",
            "body": "Can you show a demo tomorrow?",
        },
        {
            "sender": "other@example.test",
            "recipients": "team@example.test",
            "direction": "inbound",
            "subject": "Other",
            "body": "Unrelated",
        },
    ]
    captured: dict = {}
    monkeypatch.setattr(email_routes, "_gmail_connection", lambda *_a, **_k: (account, "token"))
    monkeypatch.setattr(
        email_routes,
        "GmailInboundProvider",
        lambda *_a: SimpleNamespace(fetch_mailbox_thread=lambda _id: thread),
    )
    monkeypatch.setattr(
        email_routes,
        "operations_policy",
        lambda *_a: SimpleNamespace(business_profile=SimpleNamespace(model_dump=lambda **_k: {})),
    )
    monkeypatch.setattr(
        email_routes, "apply_workspace_email_signature", lambda body, _profile: body
    )

    def draft(facts: dict) -> dict:
        captured.update(facts)
        return {
            "subject": "New subject",
            "body": "I can help arrange a demo tomorrow.",
            "rationale": "Customer requested demo",
        }

    monkeypatch.setattr(
        email_routes,
        "resolve_workspace_reasoning_provider",
        lambda *_a: SimpleNamespace(draft_follow_up=draft),
    )

    result = compose_email_with_ai(
        ComposeWithAIRequest(
            connection_id=account.id,
            thread_id="thread_1",
            recipient="client@example.test",
            subject="Re: Demo",
            instruction="Answer their demo request",
        ),
        MagicMock(),
        workspace,
    )

    assert result.subject == "Re: Demo"
    assert len(captured["recent_activity"]) == 1
    assert "tomorrow" in captured["recent_activity"][0]["body"]


def test_ai_composer_rejects_changed_verified_meeting_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(workspace_id=uuid4(), actor_id="owner")
    monkeypatch.setattr(
        email_routes,
        "operations_policy",
        lambda *_a: SimpleNamespace(business_profile=SimpleNamespace(model_dump=lambda **_k: {})),
    )
    monkeypatch.setattr(
        email_routes,
        "resolve_workspace_reasoning_provider",
        lambda *_a: SimpleNamespace(
            draft_follow_up=lambda _facts: {
                "subject": "Meeting times",
                "body": "Please book another time tomorrow.",
                "rationale": "Meeting request",
            }
        ),
    )
    with pytest.raises(HTTPException) as error:
        compose_email_with_ai(
            ComposeWithAIRequest(
                recipient="client@example.test",
                subject="Meeting times",
                current_body="1. Thursday 2 pm\n2. Friday 3 pm\nReply with the option number.",
            ),
            MagicMock(),
            workspace,
        )
    assert error.value.status_code == 409


def test_send_email_request_accepts_a_recipient_and_optional_thread_context() -> None:
    request = SendEmailRequest.model_validate(
        {
            "connection_id": str(uuid4()),
            "recipient": "Pat Example <pat@example.test>",
            "subject": "Re: Pricing request",
            "body": "Here is the requested information.",
            "thread_id": "thread_123",
            "in_reply_to": "<message-1@example.test>",
        }
    )
    assert request.recipient == "Pat Example <pat@example.test>"
    assert request.thread_id == "thread_123"


def test_send_email_request_accepts_cc_bcc_rich_text_and_attachments() -> None:
    request = SendEmailRequest.model_validate({
        "connection_id": str(uuid4()),
        "recipient": "pat@example.test",
        "cc": "Team One <team@example.test>, team-two@example.test",
        "bcc": "Private One <private@example.test>, private-two@example.test",
        "subject": "Formatted update",
        "body": "Hello",
        "html_body": "<p><strong>Hello</strong></p>",
        "attachments": [{
            "filename": "notes.txt",
            "content_type": "text/plain",
            "content_base64": "Q2FsbCBub3Rlcw==",
        }],
    })
    assert request.cc == "Team One <team@example.test>, team-two@example.test"
    assert request.bcc == "Private One <private@example.test>, private-two@example.test"
    assert request.attachments[0].filename == "notes.txt"


def test_email_html_sanitizer_keeps_basic_formatting_and_removes_unsafe_links() -> None:
    safe = _sanitize_email_html(
        '<p><strong>Hi</strong> <a href="javascript:alert(1)">there</a></p>'
    )
    assert safe is not None
    assert "<strong>Hi</strong>" in safe
    assert 'href="javascript:' not in safe
    assert "there" in safe


def test_gmail_send_account_list_exposes_only_sendable_sender_identity() -> None:
    workspace_id = uuid4()
    sendable = SimpleNamespace(
        id=uuid4(),
        account_name="Sales inbox",
        connection_metadata={"gmail_email": "sales@example.test"},
        scopes=[GMAIL_SEND_SCOPE],
    )
    no_send_scope = SimpleNamespace(
        id=uuid4(),
        account_name="Read-only inbox",
        connection_metadata={"gmail_email": "read@example.test"},
        scopes=[],
    )
    invalid_sender = SimpleNamespace(
        id=uuid4(),
        account_name="Broken sender",
        connection_metadata={},
        scopes=[GMAIL_SEND_SCOPE],
    )
    session = MagicMock()
    session.scalars.return_value = [sendable, no_send_scope, invalid_sender]

    accounts = list_gmail_send_accounts(
        session,
        SimpleNamespace(workspace_id=workspace_id),
    )

    assert accounts == [
        GmailSendAccountResponse(
            id=sendable.id,
            account_name="Sales inbox",
            sender_email="sales@example.test",
        )
    ]
    query = session.scalars.call_args.args[0]
    assert "provider_connections.workspace_id" in str(query)
    assert "provider_connections.is_active" in str(query)
    assert "provider_connections.status" in str(query)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("recipient", "pat@example.test\r\nBcc: other@example.test"),
        ("subject", "Hello\r\nBcc: other@example.test"),
        ("thread_id", "../other-thread"),
    ],
)
def test_send_email_request_rejects_header_injection_and_invalid_thread_ids(
    field: str, value: str
) -> None:
    payload = {
        "connection_id": str(uuid4()),
        "recipient": "pat@example.test",
        "subject": "Hello",
        "body": "Message body.",
        field: value,
    }
    with pytest.raises(ValidationError):
        SendEmailRequest.model_validate(payload)


def test_workspace_drafts_are_private_and_versioned() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[WorkspaceORM.__table__, WorkspaceEmailDraftORM.__table__])
    workspace_id = uuid4()
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add(WorkspaceORM(id=workspace_id, name="Test", slug="test-drafts", created_at=now, updated_at=now))
        session.commit()
        alice = SimpleNamespace(workspace_id=workspace_id, actor_id="alice")
        bob = SimpleNamespace(workspace_id=workspace_id, actor_id="bob")
        payload = SaveEmailDraftRequest(subject="Hello", body="Draft content")
        draft = create_email_draft(payload, session, alice)
        summaries = list_email_drafts(session, alice)
        assert len(summaries) == 1
        assert summaries[0].subject == "Hello"
        assert not hasattr(summaries[0], "data")
        assert list_email_drafts(session, bob) == []
        assert get_email_draft(draft.id, session, alice).data["body"] == "Draft content"
        changed = update_email_draft(
            draft.id,
            SaveEmailDraftRequest(subject="Updated", body="Draft content", expected_version=1),
            session, alice,
        )
        assert changed.version == 2
        assert changed.data["subject"] == "Updated"
        delete_email_draft(draft.id, session, alice)
        assert list_email_drafts(session, alice) == []
