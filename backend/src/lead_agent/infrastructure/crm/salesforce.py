from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from lead_agent.domain.crm import (
    CanonicalCrmPayload,
    CrmEntityReferences,
    CrmProviderError,
    CrmSyncResult,
    SalesforceCrmConfig,
)
from lead_agent.infrastructure.crm.http import HttpResponse, HttpTransport, HttpxTransport
from lead_agent.infrastructure.crm.mapping import mapped_properties, provider_error


class SalesforceCrmProvider:
    provider_name = "salesforce"
    connection_id = None

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        login_base_url: str,
        api_version: str,
        config: SalesforceCrmConfig,
        timeout_seconds: float = 20.0,
        transport: HttpTransport | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.login_base_url = login_base_url.rstrip("/")
        self.api_version = api_version.removeprefix("v")
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.transport = transport or HttpxTransport()

    def _validate_configuration(self) -> None:
        required = {
            "SALESFORCE_CLIENT_ID": self.client_id,
            "SALESFORCE_CLIENT_SECRET": self.client_secret,
            "SALESFORCE_REFRESH_TOKEN": self.refresh_token,
        }
        missing = [
            name for name, value in required.items() if not value or value.startswith("replace_")
        ]
        if missing:
            raise CrmProviderError(
                f"Salesforce configuration is missing: {', '.join(missing)}",
                code="salesforce_not_configured",
                category="configuration",
                retryable=False,
            )

    def _json(self, response: HttpResponse) -> dict[str, Any]:
        try:
            value = response.json()
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def _authenticate(self) -> tuple[str, str]:
        self._validate_configuration()
        response = self.transport.request(
            "POST",
            f"{self.login_base_url}/services/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
            },
            timeout=self.timeout_seconds,
        )
        if response.status_code != 200:
            raise provider_error(self.provider_name, response.status_code)
        data = self._json(response)
        access_token = data.get("access_token")
        instance_url = data.get("instance_url")
        if not isinstance(access_token, str) or not isinstance(instance_url, str):
            raise CrmProviderError(
                "Salesforce OAuth response is missing required fields",
                code="salesforce_invalid_oauth_response",
                category="authentication",
                retryable=False,
            )
        return access_token, instance_url.rstrip("/")

    def _upsert(
        self,
        *,
        token: str,
        instance_url: str,
        object_name: str,
        external_field: str,
        external_value: str,
        fields: dict[str, Any],
        existing_id: str | None,
    ) -> str:
        encoded = quote(external_value, safe="")
        url = (
            f"{instance_url}/services/data/v{self.api_version}/sobjects/"
            f"{object_name}/{external_field}/{encoded}"
        )
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        response = self.transport.request(
            "PATCH",
            url,
            headers=headers,
            json=fields,
            timeout=self.timeout_seconds,
        )
        if response.status_code not in {200, 201, 204}:
            raise provider_error(self.provider_name, response.status_code)
        data = self._json(response)
        entity_id = data.get("id") or existing_id
        if not entity_id:
            lookup = self.transport.request(
                "GET", url, headers=headers, timeout=self.timeout_seconds
            )
            if lookup.status_code != 200:
                raise provider_error(self.provider_name, lookup.status_code)
            entity_id = self._json(lookup).get("Id")
        if not isinstance(entity_id, str) or not entity_id:
            raise CrmProviderError(
                "Salesforce upsert did not return a record ID",
                code="salesforce_missing_entity_id",
                category="validation",
                retryable=False,
            )
        return entity_id

    def lead_fields(self, payload: CanonicalCrmPayload) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "FirstName": payload.first_name,
            "LastName": payload.last_name,
            "Company": payload.company,
            "Email": payload.email,
            "Title": payload.title,
            "Phone": payload.phone,
        }
        fields.update(mapped_properties(payload, self.config.lead_fields))
        if payload.assigned_rep_id in self.config.owner_mapping:
            fields["OwnerId"] = self.config.owner_mapping[payload.assigned_rep_id]
        return {key: value for key, value in fields.items() if value is not None}

    def opportunity_fields(self, payload: CanonicalCrmPayload) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "Name": f"{payload.company} - {payload.requested_service}"[:120],
            "StageName": self.config.opportunity_stage_by_band.get(
                payload.qualification_band, "Prospecting"
            ),
            "CloseDate": (datetime.now(UTC) + timedelta(days=30)).date().isoformat(),
        }
        fields.update(mapped_properties(payload, self.config.opportunity_fields))
        if payload.assigned_rep_id in self.config.owner_mapping:
            fields["OwnerId"] = self.config.owner_mapping[payload.assigned_rep_id]
        return fields

    def _sync(
        self,
        payload: CanonicalCrmPayload,
        existing: CrmEntityReferences | None = None,
    ) -> CrmSyncResult:
        references = existing or CrmEntityReferences()
        token, instance_url = self._authenticate()
        lead_id = self._upsert(
            token=token,
            instance_url=instance_url,
            object_name=self.config.lead_object,
            external_field=self.config.lead_external_id_field,
            external_value=str(payload.internal_lead_id),
            fields=self.lead_fields(payload),
            existing_id=references.contact_id,
        )
        opportunity_id = references.opportunity_id
        if self.config.sync_opportunity:
            opportunity_id = self._upsert(
                token=token,
                instance_url=instance_url,
                object_name=self.config.opportunity_object,
                external_field=self.config.opportunity_external_id_field,
                external_value=str(payload.internal_lead_id),
                fields=self.opportunity_fields(payload),
                existing_id=references.opportunity_id,
            )
        return CrmSyncResult(
            provider=self.provider_name,
            operation="update" if references.contact_id else "upsert",
            entities=CrmEntityReferences(
                contact_id=lead_id,
                company_id=references.company_id,
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
                "Salesforce network request failed",
                code="salesforce_network_failure",
                category="transient",
                retryable=True,
            ) from error
