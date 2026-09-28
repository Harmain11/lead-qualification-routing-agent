from __future__ import annotations

import logging
import smtplib
import ssl
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


class SmtpEmailProvider:
    provider_name = "smtp"
    configured = True

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send_verification_email(self, message: VerificationEmail) -> None:
        email = EmailMessage()
        email["From"] = self._settings.email_from
        email["To"] = message.recipient
        email["Subject"] = "Verify your Ingenuity Systems account"
        email.set_content(
            "Verify your email address to activate your Ingenuity Systems workspace.\n\n"
            f"Open this link within {self._settings.email_verification_expiry_minutes} minutes:\n"
            f"{message.verification_url}\n\n"
            "If you did not request this account, you can ignore this message."
        )
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
    raise ValueError(f"unsupported EMAIL_PROVIDER: {settings.email_provider}")


def get_email_provider() -> EmailProvider:
    # The app's settings are already cached; the provider is intentionally resolved per
    # dependency call so tests and deployments can replace it without global state leaks.
    return build_email_provider(get_settings())
