from __future__ import annotations

import json
from typing import Any

from lead_agent.domain.crm import CanonicalCrmPayload, CrmProviderError


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
    if status_code in {401, 403}:
        return CrmProviderError(
            f"{provider} authentication was rejected",
            code=f"{provider}_authentication_failed",
            category="authentication",
            retryable=False,
        )
    if status_code == 429:
        return CrmProviderError(
            f"{provider} rate limit reached",
            code=f"{provider}_rate_limited",
            category="rate_limit",
            retryable=True,
        )
    if status_code in {408, 425} or status_code >= 500:
        return CrmProviderError(
            f"{provider} is temporarily unavailable (HTTP {status_code})",
            code=f"{provider}_temporary_failure",
            category="transient",
            retryable=True,
        )
    if status_code in {400, 404, 409, 422}:
        return CrmProviderError(
            f"{provider} rejected the mapped CRM payload (HTTP {status_code})",
            code=f"{provider}_validation_failed",
            category="validation",
            retryable=False,
        )
    return CrmProviderError(
        f"{provider} request failed (HTTP {status_code})",
        code=f"{provider}_request_failed",
        category="unknown",
        retryable=False,
    )
