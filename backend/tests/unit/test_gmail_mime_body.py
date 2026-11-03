import base64

from lead_agent.infrastructure.gmail import parse_mime_body


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def test_parse_mime_body_ignores_attached_rfc822_message() -> None:
    body = parse_mime_body(
        {
            "mimeType": "multipart/mixed",
            "body": {},
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _encoded("Latest lead reply.")}},
                {
                    "mimeType": "message/rfc822",
                    "filename": "forwarded.eml",
                    "body": {"data": _encoded("From: old@example.test\nSubject: Old\n\nOld message")},
                },
            ],
        }
    )

    assert body == "Latest lead reply."
