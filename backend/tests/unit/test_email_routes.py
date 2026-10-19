from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from lead_agent.api.routes.email import SendEmailRequest, _sanitize_email_html


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
        "cc": "team@example.test",
        "bcc": "private@example.test",
        "subject": "Formatted update",
        "body": "Hello",
        "html_body": "<p><strong>Hello</strong></p>",
        "attachments": [{
            "filename": "notes.txt",
            "content_type": "text/plain",
            "content_base64": "Q2FsbCBub3Rlcw==",
        }],
    })
    assert request.cc == "team@example.test"
    assert request.bcc == "private@example.test"
    assert request.attachments[0].filename == "notes.txt"


def test_email_html_sanitizer_keeps_basic_formatting_and_removes_unsafe_links() -> None:
    safe = _sanitize_email_html(
        '<p><strong>Hi</strong> <a href="javascript:alert(1)">there</a></p>'
    )
    assert safe is not None
    assert "<strong>Hi</strong>" in safe
    assert 'href="javascript:' not in safe
    assert "there" in safe


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
