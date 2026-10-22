from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from lead_agent.api.routes.email import (
    GMAIL_SEND_SCOPE,
    GmailSendAccountResponse,
    SendEmailRequest,
    _sanitize_email_html,
    list_gmail_send_accounts,
)


def test_send_email_request_accepts_a_recipient_and_optional_thread_context() -> None:
    request = SendEmailRequest.model_validate({
        "connection_id": str(uuid4()),
        "recipient": "Pat Example <pat@example.test>",
        "subject": "Re: Pricing request",
        "body": "Here is the requested information.",
        "thread_id": "thread_123",
        "in_reply_to": "<message-1@example.test>",
    })
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
