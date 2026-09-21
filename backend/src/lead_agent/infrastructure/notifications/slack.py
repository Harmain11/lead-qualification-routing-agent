from __future__ import annotations

from importlib import import_module
from typing import Any

from lead_agent.domain.routing import NotificationDelivery, NotificationMessage


class SlackNotificationProvider:
    """Slack Web API adapter kept behind the notification port."""

    provider_name = "slack"

    def __init__(self, *, bot_token: str, channel_id: str) -> None:
        self._configuration_error: str | None = None
        self._client: Any = None
        self.channel_id = channel_id
        if not bot_token or bot_token.startswith("replace_with_"):
            self._configuration_error = "SLACK_BOT_TOKEN is not configured"
        if not channel_id or channel_id.startswith("replace_with_"):
            self._configuration_error = "SLACK_CHANNEL_ID is not configured"
        if self._configuration_error:
            self._client = None
            return
        try:
            web_client = import_module("slack_sdk").WebClient
        except ImportError:
            self._configuration_error = "slack-sdk is not installed"
            self._client = None
            return
        self._client = web_client(token=bot_token)

    def send(self, message: NotificationMessage) -> NotificationDelivery:
        if self._configuration_error:
            return NotificationDelivery(
                state="failed",
                error_message=self._configuration_error,
                retryable=False,
            )
        try:
            response = self._client.chat_postMessage(
                channel=self.channel_id,
                text=message.text,
                unfurl_links=False,
                unfurl_media=False,
            )
            return NotificationDelivery(
                state="sent",
                external_id=str(response.get("ts")) if response.get("ts") else None,
            )
        except Exception as error:
            return NotificationDelivery(
                state="retryable",
                error_message=str(error)[:2000],
                retryable=True,
            )
