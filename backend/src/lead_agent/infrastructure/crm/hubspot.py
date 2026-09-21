from __future__ import annotations

from typing import Any

from lead_agent.domain.crm import (
    CanonicalCrmPayload,
    CrmEntityReferences,
    CrmProviderError,
    CrmSyncResult,
    HubSpotCrmConfig,
)
from lead_agent.infrastructure.crm.http import HttpResponse, HttpTransport, HttpxTransport
from lead_agent.infrastructure.crm.mapping import mapped_properties, provider_error


class HubSpotCrmProvider:
    provider_name = "hubspot"
    connection_id = None

    def __init__(
        self,
        *,
        access_token: str,
        api_base_url: str,
        config: HubSpotCrmConfig,
        timeout_seconds: float = 20.0,
        transport: HttpTransport | None = None,
    ) -> None:
        self.access_token = access_token
        self.api_base_url = api_base_url.rstrip("/")
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.transport = transport or HttpxTransport()

    def _validate_configuration(self) -> None:
        if not self.access_token or self.access_token.startswith("replace_"):
            raise CrmProviderError(
                "HubSpot private-app access token is not configured",
                code="hubspot_not_configured",
                category="configuration",
                retryable=False,
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _json(self, response: HttpResponse) -> dict[str, Any]:
        try:
            value = response.json()
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def _upsert_object(
        self,
        object_name: str,
        id_property: str,
        identity: str,
        properties: dict[str, Any],
    ) -> str:
        response = self.transport.request(
            "POST",
            f"{self.api_base_url}/crm/v3/objects/{object_name}/batch/upsert",
            headers=self._headers,
            json={
                "inputs": [
                    {
                        "id": identity,
                        "idProperty": id_property,
                        "properties": {
                            key: str(value) if not isinstance(value, str) else value
                            for key, value in properties.items()
                            if value is not None
                        },
                    }
                ]
            },
            timeout=self.timeout_seconds,
        )
        if response.status_code not in {200, 201, 207}:
            raise provider_error(self.provider_name, response.status_code)
        results = self._json(response).get("results")
        entity_id = results[0].get("id") if isinstance(results, list) and results else None
        if not isinstance(entity_id, str) or not entity_id:
            raise CrmProviderError(
                f"HubSpot {object_name} upsert did not return a record ID",
                code="hubspot_missing_entity_id",
                category="validation",
                retryable=False,
            )
        return entity_id

    def _associate(self, from_object: str, from_id: str, to_object: str, to_id: str) -> None:
        response = self.transport.request(
            "PUT",
            (
                f"{self.api_base_url}/crm/v4/objects/{from_object}/{from_id}/"
                f"associations/default/{to_object}/{to_id}"
            ),
            headers=self._headers,
            timeout=self.timeout_seconds,
        )
        if response.status_code not in {200, 201, 204}:
            raise provider_error(self.provider_name, response.status_code)

    def contact_properties(self, payload: CanonicalCrmPayload) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "email": payload.email,
            "firstname": payload.first_name,
            "lastname": payload.last_name,
            "company": payload.company,
            "jobtitle": payload.title,
            "phone": payload.phone,
        }
        properties.update(mapped_properties(payload, self.config.contact_fields))
        if payload.assigned_rep_id in self.config.owner_mapping:
            properties["hubspot_owner_id"] = self.config.owner_mapping[payload.assigned_rep_id]
        return {key: value for key, value in properties.items() if value is not None}

    def company_properties(self, payload: CanonicalCrmPayload) -> dict[str, Any]:
        properties: dict[str, Any] = {"name": payload.company}
        properties.update(mapped_properties(payload, self.config.company_fields))
        return properties

    def deal_properties(self, payload: CanonicalCrmPayload) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "dealname": f"{payload.company} - {payload.requested_service}",
            "pipeline": self.config.deal_pipeline,
            "dealstage": self.config.deal_stage_by_band.get(
                payload.qualification_band, "appointmentscheduled"
            ),
        }
        properties.update(mapped_properties(payload, self.config.deal_fields))
        if payload.assigned_rep_id in self.config.owner_mapping:
            properties["hubspot_owner_id"] = self.config.owner_mapping[payload.assigned_rep_id]
        return properties

    def _sync(
        self,
        payload: CanonicalCrmPayload,
        existing: CrmEntityReferences | None = None,
    ) -> CrmSyncResult:
        self._validate_configuration()
        references = existing or CrmEntityReferences()
        contact_id = self._upsert_object(
            self.config.contact_object,
            self.config.contact_id_property,
            payload.email,
            self.contact_properties(payload),
        )
        company_id = references.company_id
        if self.config.sync_company:
            company_id = self._upsert_object(
                self.config.company_object,
                self.config.company_id_property,
                payload.company_key,
                self.company_properties(payload),
            )
            self._associate(
                self.config.contact_object,
                contact_id,
                self.config.company_object,
                company_id,
            )
        opportunity_id = references.opportunity_id
        if self.config.sync_deal:
            opportunity_id = self._upsert_object(
                self.config.deal_object,
                self.config.deal_id_property,
                str(payload.internal_lead_id),
                self.deal_properties(payload),
            )
            self._associate(
                self.config.deal_object,
                opportunity_id,
                self.config.contact_object,
                contact_id,
            )
            if company_id:
                self._associate(
                    self.config.deal_object,
                    opportunity_id,
                    self.config.company_object,
                    company_id,
                )
        return CrmSyncResult(
            provider=self.provider_name,
            operation="update" if references.contact_id else "upsert",
            entities=CrmEntityReferences(
                contact_id=contact_id,
                company_id=company_id,
                opportunity_id=opportunity_id,
            ),
        )

    def sync(
        self,
        payload: CanonicalCrmPayload,
        existing: CrmEntityReferences | None = None,
    ) -> CrmSyncResult:
        try:
            return self._sync(payload, existing)
        except CrmProviderError:
            raise
        except Exception as error:
            raise CrmProviderError(
                "HubSpot network request failed",
                code="hubspot_network_failure",
                category="transient",
                retryable=True,
            ) from error
