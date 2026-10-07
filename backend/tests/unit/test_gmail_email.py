from __future__ import annotations

import base64
import json
import subprocess
from email import policy
from email.parser import BytesParser
from unittest.mock import patch

import pytest

from lead_agent.infrastructure.email import (
    EmailDeliveryError,
    GmailEmailProvider,
    VerificationEmail,
    build_email_provider,
)
from lead_agent.settings.config import Settings


def provider() -> GmailEmailProvider:
    settings = Settings(app_env="test", email_provider="gmail", _env_file=None)
    result = build_email_provider(settings)
    assert isinstance(result, GmailEmailProvider)
    return result


def reply(body: dict[str, object], code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["node"], code, json.dumps(body), "")


def test_gmail_uses_connected_sender_and_sends_real_verification_message() -> None:
    with patch("lead_agent.infrastructure.email.subprocess.run") as run:
        run.side_effect = [
            reply({"ok": True, "emailAddress": "app@gmail.com"}),
            reply({"ok": True, "id": "accepted-message"}),
        ]
        provider().send_verification_email(
            VerificationEmail("user@example.com", "https://app.example.com/verify?t=abc", "later")
        )
    assert run.call_count == 2
    first = json.loads(run.call_args_list[0].kwargs["input"])
    assert first == {"action": "profile"}
    payload = json.loads(run.call_args_list[1].kwargs["input"])
    assert payload["action"] == "send"
    message = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(payload["raw"])
    )
    assert message["From"] == "app@gmail.com"
    assert message["To"] == "user@example.com"
    assert "https://app.example.com/verify?t=abc" in message.get_content()


@pytest.mark.parametrize(
    "response",
    [
        reply({"ok": False, "status": 401}, 1),
        reply({"ok": False, "status": 429}, 1),
        reply({"ok": True}),  # Missing mailbox profile.
        reply({"ok": True, "emailAddress": "bad\nsender@gmail.com"}),
        subprocess.CompletedProcess(["node"], 0, "not json", "sensitive provider output"),
    ],
)
def test_gmail_rejects_failed_or_malformed_profiles_without_sending(
    response: subprocess.CompletedProcess[str],
) -> None:
    with patch("lead_agent.infrastructure.email.subprocess.run", return_value=response) as run:
        with pytest.raises(EmailDeliveryError):
            provider().send_verification_email(
                VerificationEmail("user@example.com", "https://app.example.com/verify", "later")
            )
    assert run.call_count == 1


def test_timed_out_send_is_not_automatically_retried() -> None:
    with patch("lead_agent.infrastructure.email.subprocess.run") as run:
        run.side_effect = [
            reply({"ok": True, "emailAddress": "app@gmail.com"}),
            subprocess.TimeoutExpired(["node"], 30),
        ]
        with pytest.raises(EmailDeliveryError, match="request failed"):
            provider().send_verification_email(
                VerificationEmail("user@example.com", "https://app.example.com/verify", "later")
            )
    assert run.call_count == 2


def test_gmail_requires_message_acceptance_confirmation() -> None:
    with patch("lead_agent.infrastructure.email.subprocess.run") as run:
        run.side_effect = [
            reply({"ok": True, "emailAddress": "app@gmail.com"}),
            reply({"ok": True}),
        ]
        with pytest.raises(EmailDeliveryError, match="did not confirm"):
            provider().send_verification_email(
                VerificationEmail("user@example.com", "https://app.example.com/verify", "later")
            )
