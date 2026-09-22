from __future__ import annotations

from pathlib import Path

import pytest

from lead_agent.domain.configuration import IcpConfig, RoutingConfig, load_icp, load_routing
from lead_agent.domain.crm import CrmConfig, load_crm_config
from lead_agent.infrastructure.rate_limit import _cached_rate_limiter


@pytest.fixture(autouse=True)
def reset_rate_limit_memory_between_tests():
    """Keep the development limiter deterministic and isolated across tests."""
    _cached_rate_limiter.cache_clear()
    yield
    _cached_rate_limiter.cache_clear()


@pytest.fixture
def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture
def icp(repository_root: Path) -> IcpConfig:
    return load_icp(repository_root / "config" / "icp.example.yaml")


@pytest.fixture
def routing(repository_root: Path) -> RoutingConfig:
    return load_routing(repository_root / "config" / "routing.example.yaml")


@pytest.fixture
def crm_config(repository_root: Path) -> CrmConfig:
    return load_crm_config(repository_root / "config" / "crm.example.yaml")
