from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx

from lead_agent.domain.crm import (
    CanonicalCrmPayload,
    CrmEntityReferences,
    CrmProviderError,
    CrmSyncResult,
    HubSpotCrmConfig,
)
from lead_agent.domain.crm_inbound import (
    CanonicalInboundLead,
    ExternalCrmRecord,
    InboundCrmPage,
    InboundCursor,
)
from lead_agent.domain.integrations import (
    ConnectionValidation,
    ProviderConnectionValidator,
    ProviderRevoker,
    RevocationResult,
)
from lead_agent.domain.provider_errors import (
    ProviderOperationError,
    provider_http_error,
    provider_malformed_response_error,
    provider_timeout_error,
)
from lead_agent.infrastructure.crm.http import HttpResponse, HttpTransport, HttpxTransport
from lead_agent.infrastructure.crm.mapping import mapped_properties, provider_error


class HubSpotCrmProvider:
    provider_name = "hubspot"
    connection_id = None
    _numeric_contact_fields = {
        "qualification_score",
        "icp_score",
        "intent_urgency_score",
        "engagement_score",
    }
    _long_text_contact_fields = {
        "summary",
        "recommended_action",
        "rationale_text",
        "action_plan_json",
    }

    def __init__(
        self,
        *,
        access_token: str,
        api_base_url: str,
        config: HubSpotCrmConfig,
        properties_api_version: str = "2026-09",
        timeout_seconds: float = 20.0,
        transport: HttpTransport | None = None,
    ) -> None:
        self.access_token = access_token
        self.api_base_url = api_base_url.rstrip("/")
        self.config = config
        self.properties_api_version = properties_api_version
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

    def _list_contact_properties(self) -> dict[str, dict[str, Any]]:
        url = (
            f"{self.api_base_url}/crm/properties/"
            f"{self.properties_api_version}/contacts"
        )
        response = self.transport.request(
            "GET",
            url,
            headers=self._headers,
            timeout=self.timeout_seconds,
        )
        if response.status_code != 200:
            raise provider_error(self.provider_name, response.status_code)
        body = self._json(response)
        results = body.get("results")
        if not isinstance(results, list):
            raise CrmProviderError(
                "HubSpot returned an invalid contact properties response",
                code="hubspot_malformed_properties_response",
                category="validation",
                retryable=False,
            )
        return {
            item["name"]: item
            for item in results
            if (
                isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and item["name"]
            )
        }

    @classmethod
    def _contact_property_definition(
        cls, source: str, target: str
    ) -> dict[str, str]:
        numeric = source in cls._numeric_contact_fields
        label = " ".join(part.capitalize() for part in target.replace("-", "_").split("_"))
        return {
            "groupName": "contactinformation",
            "name": target,
            "label": label,
            "type": "number" if numeric else "string",
            "fieldType": (
                "number"
                if numeric
                else "textarea"
                if source in cls._long_text_contact_fields
                else "text"
            ),
            "description": "Managed by Corral for lead qualification data.",
        }

    def _ensure_contact_properties(self, payload: CanonicalCrmPayload) -> None:
        if not self.config.contact_fields:
            return

        # Validate canonical sources before making any schema changes.
        mapped_properties(payload, self.config.contact_fields)
        definitions: dict[str, dict[str, str]] = {}
        for source, target in self.config.contact_fields.items():
            definition = self._contact_property_definition(source, target)
            previous = definitions.get(target)
            if previous and previous["type"] != definition["type"]:
                raise CrmProviderError(
                    f"HubSpot property mapping for '{target}' has conflicting types",
                    code="hubspot_property_mapping_conflict",
                    category="mapping",
                    retryable=False,
                )
            definitions[target] = definition

        existing = self._list_contact_properties()
        for name, definition in definitions.items():
            current = existing.get(name)
            if current is not None:
                if current.get("type") != definition["type"]:
                    raise CrmProviderError(
                        f"HubSpot contact property '{name}' has type "
                        f"'{current.get('type', 'unknown')}', expected '{definition['type']}'.",
                        code="hubspot_property_type_mismatch",
                        category="validation",
                        retryable=False,
                    )
                continue

            response = self.transport.request(
                "POST",
                (
                    f"{self.api_base_url}/crm/properties/"
                    f"{self.properties_api_version}/contacts"
                ),
                headers=self._headers,
                json=definition,
                timeout=self.timeout_seconds,
            )
            if response.status_code in {200, 201}:
                existing[name] = definition
                continue
            if response.status_code == 409:
                # Another Corral request may have created it after our schema read.
                refreshed = self._list_contact_properties()
                current = refreshed.get(name)
                if current and current.get("type") == definition["type"]:
                    existing[name] = current
                    continue
                if current:
                    raise CrmProviderError(
                        f"HubSpot contact property '{name}' was created concurrently "
                        f"with type '{current.get('type', 'unknown')}', expected "
                        f"'{definition['type']}'.",
                        code="hubspot_property_type_mismatch",
                        category="validation",
                        retryable=False,
                    )
            raise provider_error(self.provider_name, response.status_code)

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

    def _update_object(self, object_name: str, record_id: str, properties: dict[str, Any]) -> str:
        response = self.transport.request(
            "PATCH",
            f"{self.api_base_url}/crm/v3/objects/{object_name}/{record_id}",
            headers=self._headers,
            json={
                "properties": {
                    key: str(value) if not isinstance(value, str) else value
                    for key, value in properties.items()
                    if value is not None
                }
            },
            timeout=self.timeout_seconds,
        )
        if response.status_code not in {200, 204}:
            raise provider_error(self.provider_name, response.status_code)
        return record_id

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
        self._ensure_contact_properties(payload)
        references = existing or CrmEntityReferences()
        contact_properties = self.contact_properties(payload)
        contact_id = (
            self._update_object(
                self.config.contact_object, references.contact_id, contact_properties
            )
            if references.contact_id
            else self._upsert_object(
                self.config.contact_object,
                self.config.contact_id_property,
                payload.email,
                contact_properties,
            )
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


_HUBSPOT_CONTACT_PROPERTIES = (
    "email,firstname,lastname,company,jobtitle,phone,hs_analytics_source,"
    "createdate,lastmodifieddate,hubspot_owner_id,description"
)


def _hubspot_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _fingerprint(values: dict[str, Any]) -> str:
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


class HubSpotInboundCrmProvider:
    """Read-only Contacts adapter for the shared provider-neutral inbound service."""

    provider_name = "hubspot"

    def __init__(
        self,
        *,
        connection_id: UUID,
        access_token: str,
        api_base_url: str = "https://api.hubapi.com",
        timeout_seconds: float = 20.0,
        transport: HttpTransport | None = None,
    ) -> None:
        self.connection_id = connection_id
        self.access_token = access_token
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or HttpxTransport()

    def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self.access_token or self.access_token.startswith("replace_"):
            raise ProviderOperationError(
                "HubSpot OAuth access token is unavailable",
                provider=self.provider_name,
                code="hubspot_credentials_missing",
                category="configuration",
                retryable=False,
            )
        try:
            response = self.transport.request(
                method,
                f"{self.api_base_url}{path}",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json=json_body,
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException:
            raise provider_timeout_error(self.provider_name) from None
        except httpx.HTTPError:
            raise ProviderOperationError(
                "HubSpot inbound API was unavailable",
                provider=self.provider_name,
                code="hubspot_inbound_unavailable",
                category="temporarily_unavailable",
                retryable=True,
            ) from None
        if response.status_code not in {200, 201}:
            raise provider_http_error(self.provider_name, response.status_code)
        try:
            body = response.json()
        except Exception:
            raise provider_malformed_response_error(self.provider_name) from None
        if not isinstance(body, dict):
            raise provider_malformed_response_error(self.provider_name)
        return body

    def list_records(
        self, *, object_type: str, cursor: InboundCursor | None, limit: int
    ) -> InboundCrmPage:
        if object_type != "contacts":
            raise ProviderOperationError(
                "HubSpot inbound supports Contacts only",
                provider=self.provider_name,
                code="hubspot_inbound_object_unsupported",
                category="configuration",
                retryable=False,
            )
        page_size = max(1, min(limit, 100))
        current = dict(cursor or {})
        mode = str(
            current.get("mode") or ("incremental" if "modified_after_ms" in current else "initial")
        )
        if mode == "incremental":
            body = self._request(
                "POST",
                "/crm/v3/objects/contacts/search",
                json_body={
                    "filterGroups": [
                        {
                            "filters": [
                                {
                                    "propertyName": "lastmodifieddate",
                                    "operator": "GTE",
                                    # Re-read overlap to avoid skipping equal-time records;
                                    # source fingerprints make this overlap idempotent.
                                    "value": str(
                                        max(0, int(current.get("modified_after_ms") or 0) - 60_000)
                                    ),
                                }
                            ]
                        }
                    ],
                    "sorts": ["lastmodifieddate"],
                    "properties": _HUBSPOT_CONTACT_PROPERTIES.split(","),
                    "limit": page_size,
                    **({"after": str(current["after"])} if current.get("after") else {}),
                },
            )
        else:
            query = {"limit": page_size, "properties": _HUBSPOT_CONTACT_PROPERTIES}
            if current.get("after"):
                query["after"] = str(current["after"])
            body = self._request("GET", f"/crm/v3/objects/contacts?{urlencode(query)}")
        raw_results = body.get("results")
        if not isinstance(raw_results, list):
            raise provider_malformed_response_error(self.provider_name)
        records: list[ExternalCrmRecord] = []
        for value in raw_results:
            if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                raise provider_malformed_response_error(self.provider_name)
            props = value.get("properties")
            if not isinstance(props, dict):
                raise provider_malformed_response_error(self.provider_name)
            modified = _hubspot_datetime(value.get("updatedAt") or props.get("lastmodifieddate"))
            records.append(
                ExternalCrmRecord(
                    provider=self.provider_name,
                    object_type="contacts",
                    record_id=value["id"],
                    values=props,
                    modified_at=modified,
                    provider_version=modified.isoformat() if modified else None,
                    archived=bool(value.get("archived", False)),
                )
            )
        paging = body.get("paging")
        next_value = paging.get("next") if isinstance(paging, dict) else None
        after = next_value.get("after") if isinstance(next_value, dict) else None
        next_cursor: InboundCursor | None = None
        if isinstance(after, (str, int)):
            next_cursor = {"mode": mode, "after": str(after)}
            if mode == "incremental":
                next_cursor["modified_after_ms"] = str(current.get("modified_after_ms") or 0)
        checkpoint: InboundCursor | None = None
        if records and records[-1].modified_at is not None:
            checkpoint = {"modified_after_ms": str(int(records[-1].modified_at.timestamp() * 1000))}
        if mode == "incremental" and next_cursor is not None:
            next_cursor["modified_after_ms"] = str(current.get("modified_after_ms") or 0)
        return InboundCrmPage(records=records, next_cursor=next_cursor, checkpoint=checkpoint)

    def fetch_record(self, *, object_type: str, record_id: str) -> ExternalCrmRecord:
        if object_type != "contacts" or not record_id:
            raise ValueError("HubSpot Contact identity is invalid")
        query = urlencode({"properties": _HUBSPOT_CONTACT_PROPERTIES})
        body = self._request("GET", f"/crm/v3/objects/contacts/{record_id}?{query}")
        if not isinstance(body.get("id"), str) or not isinstance(body.get("properties"), dict):
            raise provider_malformed_response_error(self.provider_name)
        modified = _hubspot_datetime(body.get("updatedAt"))
        return ExternalCrmRecord(
            provider=self.provider_name,
            object_type=object_type,
            record_id=body["id"],
            values=body["properties"],
            modified_at=modified,
            provider_version=modified.isoformat() if modified else None,
            archived=bool(body.get("archived", False)),
        )

    def normalize_record(self, record: ExternalCrmRecord) -> CanonicalInboundLead:
        if record.provider != self.provider_name or record.object_type != "contacts":
            raise ValueError("HubSpot adapter received an incompatible record")
        values = record.values
        first = str(values.get("firstname") or "").strip()
        last = str(values.get("lastname") or "").strip()
        full_name = " ".join(part for part in (first, last) if part)
        email = str(values.get("email") or "").strip().lower()
        company = str(values.get("company") or "").strip()
        if not full_name or not email:
            raise ValueError("HubSpot Contact requires name and email for import")
        # HubSpot Contacts may legitimately have no Primary company. Keep the
        # contact visible in the lead archive instead of dropping the record.
        if not company:
            company = email.split("@", 1)[-1] if "@" in email else "Unknown company"
        source = str(values.get("hs_analytics_source") or "Unknown").strip() or "Unknown"
        description = str(values.get("description") or "").strip()
        source_owned = {
            "full_name": full_name,
            "email": email,
            "phone": values.get("phone"),
            "company_name": company,
            "job_title": values.get("jobtitle"),
            "provider_source": source,
            "description": description,
            "owner_id": values.get("hubspot_owner_id"),
        }
        normalized = {
            "source": source.lower().replace(" ", "_")[:64],
            "source_detail": f"crm-import:hubspot:{source}"[:255],
            "external_id": record.record_id,
            "full_name": full_name,
            "email": email,
            "phone": values.get("phone"),
            "job_title": values.get("jobtitle"),
            "company_name": company,
            "product_or_service": "CRM lead follow-up",
            "message": description or f"HubSpot Contact for {company}; original source: {source}.",
            "received_at": values.get("createdate") or record.modified_at,
        }
        return CanonicalInboundLead(
            provider=self.provider_name,
            object_type="contacts",
            provider_record_id=record.record_id,
            normalized_payload=normalized,
            source_fingerprint=_fingerprint(source_owned),
            provider_modified_at=record.modified_at,
            provider_version=record.provider_version,
            provider_source=source,
            provider_metadata={
                "owner_id": str(values.get("hubspot_owner_id") or "") or None,
                "original_source": source,
                "description_hash": _fingerprint({"description": description}),
            },
            enrichment_fingerprint=_fingerprint({}),
            archived=record.archived,
        )


class HubSpotConnectionValidator(ProviderConnectionValidator):
    """Read-only token introspection plus Contacts and property permission probes."""

    provider_name = "hubspot"
    required_scopes = {
        "crm.objects.contacts.read",
        "crm.objects.contacts.write",
        "crm.schemas.contacts.read",
        "crm.schemas.contacts.write",
    }

    def __init__(
        self,
        *,
        oauth_api_version: str = "2026-09",
        timeout_seconds: float = 20.0,
        client_id: str = "",
        client_secret: str = "",
        transport: HttpTransport | None = None,
    ) -> None:
        self.oauth_api_version = oauth_api_version
        self.timeout_seconds = timeout_seconds
        self.client_id = client_id
        self.client_secret = client_secret
        self.transport = transport or HttpxTransport()

    def validate(
        self, provider: str, credentials: dict[str, str], scopes: list[str]
    ) -> ConnectionValidation:
        if provider != self.provider_name:
            return ConnectionValidation(
                valid=False,
                status="configuration_error",
                credential_state="configured",
                message="HubSpot validator received an unsupported provider.",
                error_code="hubspot_validator_provider_mismatch",
            )
        token = credentials.get("access_token", "")
        client_id = credentials.get("client_id", self.client_id)
        client_secret = credentials.get("client_secret", self.client_secret)
        if not token or not client_id or not client_secret:
            raise ProviderOperationError(
                "HubSpot OAuth credentials are incomplete",
                provider=provider,
                code="hubspot_credentials_missing",
                category="configuration",
                retryable=False,
            )
        try:
            response = self.transport.request(
                "POST",
                f"https://api.hubspot.com/oauth/{self.oauth_api_version}/token/introspect",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "token_type_hint": "access_token",
                    "token": token,
                },
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException:
            raise provider_timeout_error(provider) from None
        except httpx.HTTPError:
            raise ProviderOperationError(
                "HubSpot token verification is unavailable",
                provider=provider,
                code="hubspot_validation_unavailable",
                category="temporarily_unavailable",
                retryable=True,
            ) from None
        if response.status_code != 200:
            raise provider_http_error(provider, response.status_code)
        try:
            body = response.json()
        except Exception:
            raise provider_malformed_response_error(provider) from None
        if not isinstance(body, dict) or body.get("active") is not True:
            raise ProviderOperationError(
                "HubSpot access token is inactive",
                provider=provider,
                code="hubspot_token_inactive",
                category="invalid_credentials",
                retryable=False,
            )
        raw_scopes = body.get("scopes")
        if not isinstance(raw_scopes, list) or not all(
            isinstance(scope, str) for scope in raw_scopes
        ):
            raise provider_malformed_response_error(provider)
        actual_scopes = set(raw_scopes)
        missing = self.required_scopes - actual_scopes
        if missing:
            raise ProviderOperationError(
                "HubSpot authorization is missing required Contacts or contact-property scopes",
                provider=provider,
                code="hubspot_required_scope_missing",
                category="insufficient_permissions",
                retryable=False,
                metadata={"missing_scopes": ", ".join(sorted(missing))},
            )
        try:
            probe = self.transport.request(
                "GET",
                "https://api.hubapi.com/crm/v3/objects/contacts?limit=1&properties=email",
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException:
            raise provider_timeout_error(provider) from None
        except httpx.HTTPError:
            raise ProviderOperationError(
                "HubSpot Contacts permission probe is unavailable",
                provider=provider,
                code="hubspot_validation_unavailable",
                category="temporarily_unavailable",
                retryable=True,
            ) from None
        if probe.status_code != 200:
            raise provider_http_error(provider, probe.status_code)
        properties_probe = self.transport.request(
            "GET",
            (
                f"https://api.hubapi.com/crm/properties/"
                    f"{self.oauth_api_version}/contacts"
            ),
            headers={"Authorization": f"Bearer {token}"},
            timeout=self.timeout_seconds,
        )
        if properties_probe.status_code != 200:
            raise provider_http_error(provider, properties_probe.status_code)
        return ConnectionValidation(
            valid=True,
            verified=True,
            status="connected",
            credential_state="configured",
            message=(
                "HubSpot OAuth token, Contacts permissions, and contact-property "
                "permissions verified."
            ),
            metadata={"hub_id": str(body.get("hub_id") or ""), "scopes": sorted(actual_scopes)},
        )


class HubSpotProviderRevoker(ProviderRevoker):
    provider_name = "hubspot"

    def __init__(
        self,
        *,
        oauth_api_version: str = "2026-09",
        timeout_seconds: float = 20.0,
        client_id: str = "",
        client_secret: str = "",
        transport: HttpTransport | None = None,
    ) -> None:
        self.oauth_api_version = oauth_api_version
        self.timeout_seconds = timeout_seconds
        self.client_id = client_id
        self.client_secret = client_secret
        self.transport = transport or HttpxTransport()

    def revoke(self, provider: str, values: dict[str, str]) -> RevocationResult:
        if provider != self.provider_name:
            return RevocationResult(status="unsupported")
        refresh_token = values.get("refresh_token", "")
        if not refresh_token:
            return RevocationResult(status="failed", error_code="hubspot_refresh_token_missing")
        try:
            response = self.transport.request(
                "POST",
                f"https://api.hubspot.com/oauth/{self.oauth_api_version}/token/revoke",
                data={
                    "client_id": values.get("client_id", self.client_id),
                    "client_secret": values.get("client_secret", self.client_secret),
                    "token": refresh_token,
                },
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException:
            return RevocationResult(status="failed", error_code="hubspot_revocation_timeout")
        except httpx.HTTPError:
            return RevocationResult(status="failed", error_code="hubspot_revocation_unavailable")
        if response.status_code in {200, 204}:
            return RevocationResult(status="confirmed")
        return RevocationResult(status="failed", error_code="hubspot_revocation_rejected")
