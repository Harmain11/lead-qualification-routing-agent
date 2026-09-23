from __future__ import annotations

import json
from typing import Any

from lead_agent.domain.crm import CanonicalCrmPayload, CrmErrorCategory, CrmProviderError
from lead_agent.domain.provider_errors import provider_http_error


def canonical_values(payload: CanonicalCrmPayload) -> dict[str, Any]:
    values = payload.model_dump(mode="json")
    values["internal_lead_id"] = str(payload.internal_lead_id)
    values["rationale_text"] = "\n".join(payload.rationale)
    values["action_plan_json"] = json.dumps(payload.action_plan, separators=(",", ":"))
    return values


def mapped_properties(
    payload: CanonicalCrmPayload, field_mappings: dict[str, str]
) -> dict[str, Any]:
    values = canonical_values(payload)
    properties: dict[str, Any] = {}
    for source, target in field_mappings.items():
        if source not in values:
            raise CrmProviderError(
                f"CRM mapping references unknown canonical field '{source}'",
                code="invalid_field_mapping",
                category="mapping",
                retryable=False,
            )
        value = values[source]
        if value is not None:
            properties[target] = value
    return properties


def provider_error(provider: str, status_code: int) -> CrmProviderError:
    translated = provider_http_error(provider, status_code)
    category_by_provider_error: dict[str, CrmErrorCategory] = {
        "invalid_credentials": "authentication",
        "insufficient_permissions": "authentication",
        "rate_limited": "rate_limit",
        "temporarily_unavailable": "transient",
        "timeout": "transient",
        "invalid_request": "validation",
        "not_found": "validation",
        "conflict": "validation",
    }
    category = category_by_provider_error.get(translated.category, "unknown")
    return CrmProviderError(
        str(translated),
        code=translated.code,
        category=category,
        retryable=translated.retryable,
    )
