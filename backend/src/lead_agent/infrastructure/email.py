from __future__ import annotations

import base64
import json
import logging
import smtplib
import ssl
import subprocess
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from lead_agent.settings.config import Settings, get_settings

logger = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    """Raised when a verification message cannot be handed to the configured provider."""


@dataclass(frozen=True)
class VerificationEmail:
    recipient: str
    verification_url: str
    expires_at: str
    purpose: str = "verify_email"  # or "reset_password"


class EmailProvider(Protocol):
    provider_name: str
    configured: bool

    def send_verification_email(self, message: VerificationEmail) -> None: ...


class DisabledEmailProvider:
    provider_name = "none"
    configured = False

    def send_verification_email(self, message: VerificationEmail) -> None:
        raise EmailDeliveryError("password signup email delivery is not configured")


class CaptureEmailProvider:
    """Safe local/test provider; captures metadata without sending external mail."""

    provider_name = "console"
    configured = True

    def __init__(self) -> None:
        self.messages: list[VerificationEmail] = []

    def send_verification_email(self, message: VerificationEmail) -> None:
        self.messages.append(message)
        logger.info(
            "auth.email_verification_queued",
            extra={"fields": {"recipient": message.recipient, "expires_at": message.expires_at}},
        )


def _verification_message(
    settings: Settings, message: VerificationEmail, sender: str
) -> EmailMessage:
    email = EmailMessage()
    email["From"] = sender
    email["To"] = message.recipient
    if message.purpose == "reset_password":
        email["Subject"] = "Reset your Ingenuity Systems password"
        email.set_content(
            "Someone asked to reset the password for this Ingenuity Systems account.\n\n"
            f"Open this link within {settings.password_reset_expiry_minutes} minutes to choose "
            f"a new password:\n{message.verification_url}\n\n"
            "If you did not ask for this, ignore this message. Your password stays the same."
        )
        return email
    email["Subject"] = "Verify your Ingenuity Systems account"
    email.set_content(
        "Verify your email address to activate your Ingenuity Systems workspace.\n\n"
        f"Open this link within {settings.email_verification_expiry_minutes} minutes:\n"
        f"{message.verification_url}\n\n"
        "If you did not request this account, you can ignore this message."
    )
    return email


class GmailEmailProvider:
    """Send using the Replit-connected application mailbox, not workspace mailboxes."""

    provider_name = "gmail"
    configured = True

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _request(self, payload: dict[str, str]) -> dict[str, object]:
        bridge = self._settings.repository_root / "scripts" / "gmail_connector.mjs"
        try:
            response = subprocess.run(
                ["node", str(bridge)],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=self._settings.email_gmail_timeout_seconds,
                check=False,
            )
            body = json.loads(response.stdout)
            if response.returncode or not isinstance(body, dict) or body.get("ok") is not True:
                raise EmailDeliveryError("Gmail connector rejected the request")
            return body
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            # No automatic retries: a timed-out send may already have been delivered.
            raise EmailDeliveryError("Gmail connector request failed") from error

    def sender_address(self) -> str:
        address = self._request({"action": "profile"}).get("emailAddress")
        if not isinstance(address, str) or "@" not in address or "\n" in address or "\r" in address:
            raise EmailDeliveryError("Gmail mailbox profile is unavailable")
        return address

    def send_verification_email(self, message: VerificationEmail) -> None:
        email = _verification_message(self._settings, message, self.sender_address())
        raw = base64.urlsafe_b64encode(email.as_bytes()).decode("ascii")
        result = self._request({"action": "send", "raw": raw})
        if not isinstance(result.get("id"), str) or not result["id"]:
            raise EmailDeliveryError("Gmail did not confirm message acceptance")


class SmtpEmailProvider:
    provider_name = "smtp"
    configured = True

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send_verification_email(self, message: VerificationEmail) -> None:
        email = _verification_message(self._settings, message, self._settings.email_from)
        try:
            context = ssl.create_default_context()
            with smtplib.SMTP(
                self._settings.email_smtp_host,
                self._settings.email_smtp_port,
                timeout=self._settings.email_smtp_timeout_seconds,
            ) as client:
                if self._settings.email_smtp_starttls:
                    client.starttls(context=context)
                if self._settings.email_smtp_username:
                    client.login(
                        self._settings.email_smtp_username,
                        self._settings.email_smtp_password,
                    )
                client.send_message(email)
        except (OSError, smtplib.SMTPException) as error:
            raise EmailDeliveryError("email delivery provider failed") from error


def build_email_provider(settings: Settings) -> EmailProvider:
    provider = settings.email_provider.strip().lower()
    if provider == "none":
        return DisabledEmailProvider()
    if provider == "console":
        return CaptureEmailProvider()
    if provider == "smtp":
        return SmtpEmailProvider(settings)
    if provider == "gmail":
        return GmailEmailProvider(settings)
    raise ValueError(f"unsupported EMAIL_PROVIDER: {settings.email_provider}")


def get_email_provider() -> EmailProvider:
    # The app's settings are already cached; the provider is intentionally resolved per
    # dependency call so tests and deployments can replace it without global state leaks.
    return build_email_provider(get_settings())
