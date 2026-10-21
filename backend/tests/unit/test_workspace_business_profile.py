from __future__ import annotations

import pytest
from pydantic import ValidationError

from lead_agent.domain.operations import OperationsPolicy, WorkspaceBusinessProfile


def test_business_profile_is_optional_for_existing_workspace_policies() -> None:
    policy = OperationsPolicy.model_validate({})

    assert not policy.business_profile.has_context
    assert policy.business_profile.business_name == ""


def test_business_profile_normalizes_names_and_email_domains() -> None:
    profile = WorkspaceBusinessProfile(
        business_name="Corral Systems",
        alternate_names=[" Corral ", "Corral", "Ingenuity Systems"],
        email_domains=["@CORRAL.EXAMPLE", "corral.example"],
    )

    assert profile.alternate_names == ["Corral", "Ingenuity Systems"]
    assert profile.email_domains == ["corral.example"]
    assert profile.has_context


@pytest.mark.parametrize("website", ["http://example.com", "https://user:pass@example.com"])
def test_business_profile_rejects_unsafe_websites(website: str) -> None:
    with pytest.raises(ValidationError):
        WorkspaceBusinessProfile(website=website)


def test_business_profile_rejects_invalid_email_domains() -> None:
    with pytest.raises(ValidationError):
        WorkspaceBusinessProfile(email_domains=["not a domain"])
