from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from lead_agent.api.routes.email import SendEmailRequest


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
