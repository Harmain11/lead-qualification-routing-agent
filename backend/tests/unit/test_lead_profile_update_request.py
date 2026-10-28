from __future__ import annotations

import pytest
from pydantic import ValidationError

from lead_agent.api.routes.leads import LeadProfileUpdateRequest
from lead_agent.domain.lead_assistant import LeadAssistantAction


def test_profile_update_can_explicitly_clear_an_optional_field() -> None:
    request = LeadProfileUpdateRequest.model_validate({"phone": None})

    assert request.model_fields_set == {"phone"}
    assert request.model_dump(exclude_unset=True) == {"phone": None}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"full_name": None},
        {"email": None},
        {"company_name": None},
        {"product_or_service": None},
    ],
)
def test_profile_update_requires_a_change_and_preserves_required_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        LeadProfileUpdateRequest.model_validate(payload)


def test_assistant_can_only_clear_supported_optional_profile_fields() -> None:
    action = LeadAssistantAction.model_validate(
        {"kind": "update_profile", "clear_fields": ["phone", "employee_count"]}
    )

    assert action.clear_fields == ["phone", "employee_count"]
    with pytest.raises(ValidationError):
        LeadAssistantAction.model_validate(
            {"kind": "update_profile", "clear_fields": ["email"]}
        )
