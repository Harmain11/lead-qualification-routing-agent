from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from lead_agent.domain.crm import (
    CanonicalCrmPayload,
    CrmEntityReferences,
    CrmOperation,
    CrmProviderError,
    CrmSyncResult,
)


class MockCrmProvider:
    provider_name = "mock"
    connection_id = None

    def __init__(self, failures: list[CrmProviderError] | None = None) -> None:
        self.records: dict[str, CanonicalCrmPayload] = {}
        self.failures = list(failures or [])
        self.calls = 0

    def lookup(self, internal_lead_id: str) -> CanonicalCrmPayload | None:
        return self.records.get(internal_lead_id)

    def sync(
        self,
        payload: CanonicalCrmPayload,
        existing: CrmEntityReferences | None = None,
    ) -> CrmSyncResult:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        key = str(payload.internal_lead_id)
        has_existing = bool(
            existing and (existing.contact_id or existing.company_id or existing.opportunity_id)
        )
        operation: CrmOperation = "update" if key in self.records or has_existing else "create"
        self.records[key] = payload
        return CrmSyncResult(
            provider=self.provider_name,
            operation=operation,
            entities=CrmEntityReferences(
                contact_id=(
                    existing.contact_id
                    if existing and existing.contact_id
                    else f"mock-contact-{uuid5(NAMESPACE_URL, key)}"
                ),
                company_id=(
                    existing.company_id
                    if existing and existing.company_id
                    else f"mock-company-{uuid5(NAMESPACE_URL, payload.company_key)}"
                ),
                opportunity_id=(
                    existing.opportunity_id
                    if existing and existing.opportunity_id
                    else f"mock-deal-{uuid5(NAMESPACE_URL, key)}"
                ),
            ),
        )
