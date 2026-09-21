from __future__ import annotations

from pathlib import Path

import pytest

from lead_agent.domain.configuration import IcpConfig, RoutingConfig, load_icp, load_routing
from lead_agent.domain.crm import CrmConfig, load_crm_config


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
