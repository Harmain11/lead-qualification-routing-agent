from pathlib import Path

import pytest
from pydantic import ValidationError

from lead_agent.domain.configuration import load_icp, load_routing


def test_malformed_icp_configuration_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "invalid-icp.yaml"
    path.write_text(
        "\n".join(
            [
                "profile_name: Invalid",
                "profile_version: '1'",
                "scoring:",
                "  weights:",
                "    icp_fit: 80",
                "    intent_urgency: 10",
                "    engagement_quality: 10",
                "  component_points:",
                "    icp_fit:",
                "      industry_match: 40",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_icp(path)


def test_malformed_routing_configuration_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "invalid-routing.yaml"
    path.write_text(
        "\n".join(
            [
                "policy_version: '1'",
                "score_bands:",
                "  - key: hot",
                "    minimum: 80",
                "    maximum: 100",
                "    queue: senior",
                "    action: follow_up",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_routing(path)
