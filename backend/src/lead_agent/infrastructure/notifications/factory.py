from __future__ import annotations

from functools import lru_cache
from typing import cast

from lead_agent.domain.routing import NotificationProvider
from lead_agent.infrastructure.notifications.mock import MockNotificationProvider
from lead_agent.settings.config import Settings, get_settings


def build_notification_provider(settings: Settings) -> NotificationProvider:
    provider = settings.notification_provider.strip().lower()
    if provider in {"", "mock", "none", "deterministic"}:
        return MockNotificationProvider()
    if provider == "slack":
        from lead_agent.infrastructure.slack import get_workspace_slack_notification_resolver

        return cast(NotificationProvider, get_workspace_slack_notification_resolver())
    raise ValueError(f"unsupported NOTIFICATION_PROVIDER: {settings.notification_provider}")


@lru_cache
def get_notification_provider() -> NotificationProvider:
    return build_notification_provider(get_settings())
