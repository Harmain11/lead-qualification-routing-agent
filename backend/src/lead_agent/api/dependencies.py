from __future__ import annotations

from functools import lru_cache

from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.settings.config import Settings, get_settings


@lru_cache
def get_policies() -> tuple[IcpConfig, RoutingConfig]:
    return get_settings().load_policies()


def get_runtime_settings() -> Settings:
    return get_settings()
