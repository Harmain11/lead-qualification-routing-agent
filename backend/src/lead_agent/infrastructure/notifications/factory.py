from __future__ import annotations

from functools import lru_cache

from lead_agent.domain.routing import NotificationProvider
from lead_agent.infrastructure.notifications.mock import MockNotificationProvider
from lead_agent.infrastructure.notifications.slack import SlackNotificationProvider
from lead_agent.settings.config import Settings, get_settings


def build_notification_provider(settings: Settings) -> NotificationProvider:
    provider = settings.notification_provider.strip().lower()
    if provider in {"", "mock", "none", "deterministic"}:
        return MockNotificationProvider()
    if provider == "slack":
        return SlackNotificationProvider(
            bot_token=settings.slack_bot_token,
            channel_id=settings.slack_channel_id,
        )
    raise ValueError(f"unsupported NOTIFICATION_PROVIDER: {settings.notification_provider}")


@lru_cache
def get_notification_provider() -> NotificationProvider:
    return build_notification_provider(get_settings())
