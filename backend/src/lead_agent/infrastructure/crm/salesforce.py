from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode, urlsplit
from uuid import UUID

import httpx

from lead_agent.domain.crm import (
    CanonicalCrmPayload,
    CrmEntityReferences,
    CrmProviderError,
    CrmSyncResult,
    SalesforceCrmConfig,
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


class SalesforceCrmProvider:
    provider_name = "salesforce"
    connection_id = None

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        access_token: str = "",
        instance_url: str = "",
        login_base_url: str,
        api_version: str,
        config: SalesforceCrmConfig,
        timeout_seconds: float = 20.0,
        transport: HttpTransport | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.access_token = access_token
        self.instance_url = instance_url.rstrip("/")
        self.login_base_url = login_base_url.rstrip("/")
        self.api_version = api_version.removeprefix("v")
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.transport = transport or HttpxTransport()

    def _validate_configuration(self) -> None:
        if self.access_token and self.instance_url:
            _safe_instance_url(self.instance_url)
            return
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
        if self.access_token and self.instance_url:
            return self.access_token, _safe_instance_url(self.instance_url)
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
        return access_token, _safe_instance_url(instance_url)

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
        direct_update: bool = False,
    ) -> str:
        if existing_id and direct_update:
            encoded_id = quote(existing_id, safe="")
            url = (
                f"{instance_url}/services/data/v{self.api_version}/sobjects/"
                f"{object_name}/{encoded_id}"
            )
            fields = {**fields, external_field: external_value}
        else:
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
            direct_update=references.contact_id_is_direct,
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


def _safe_instance_url(value: str) -> str:
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or not hostname
        or not (hostname == "salesforce.com" or hostname.endswith(".salesforce.com"))
    ):
        raise CrmProviderError(
            "Salesforce instance URL is not an approved HTTPS Salesforce host",
            code="salesforce_instance_url_invalid",
            category="configuration",
            retryable=False,
        )
    return value.rstrip("/")


class SalesforceConnectionValidator(ProviderConnectionValidator):
    """Authenticated, read-only Salesforce probe for connection verification."""

    provider_name = "salesforce"

    def __init__(
        self,
        *,
        api_version: str,
        timeout_seconds: float = 20.0,
        transport: HttpTransport | None = None,
    ) -> None:
        self.api_version = api_version.removeprefix("v")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or HttpxTransport()

    def validate(
        self,
        provider: str,
        credentials: dict[str, str],
        scopes: list[str],
    ) -> ConnectionValidation:
        if provider != self.provider_name:
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="configuration_error",
                credential_state="configured",
                message="Salesforce validator received an unsupported provider.",
                error_code="salesforce_validator_provider_mismatch",
            )
        access_token = credentials.get("access_token", "")
        instance_url = credentials.get("instance_url", "")
        if not access_token or not instance_url:
            raise ProviderOperationError(
                "Salesforce connection is missing its access token or instance URL",
                provider=self.provider_name,
                code="salesforce_connection_credentials_missing",
                category="configuration",
                retryable=False,
            )
        if scopes and "api" not in scopes:
            raise ProviderOperationError(
                "Salesforce connection does not have the required API scope",
                provider=self.provider_name,
                code="salesforce_api_scope_missing",
                category="insufficient_permissions",
                retryable=False,
            )
        safe_url = _safe_instance_url(instance_url)
        try:
            response = self.transport.request(
                "GET",
                f"{safe_url}/services/data/v{self.api_version}/limits",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException:
            raise provider_timeout_error(self.provider_name) from None
        except httpx.HTTPError:
            raise ProviderOperationError(
                "Salesforce validation request was unavailable",
                provider=self.provider_name,
                code="salesforce_validation_unavailable",
                category="temporarily_unavailable",
                retryable=True,
            ) from None
        if response.status_code != 200:
            raise provider_http_error(self.provider_name, response.status_code)
        try:
            body = response.json()
        except Exception:
            raise provider_malformed_response_error(self.provider_name) from None
        if not isinstance(body, dict):
            raise provider_malformed_response_error(self.provider_name)
        return ConnectionValidation(
            valid=True,
            verified=True,
            status="connected",
            credential_state="configured",
            message="Salesforce authenticated API access verified.",
            metadata={
                "instance_host": urlsplit(safe_url).hostname or "",
                "api_version": self.api_version,
            },
        )


class SalesforceProviderRevoker(ProviderRevoker):
    """Revoke Salesforce access/refresh tokens without exposing token material."""

    provider_name = "salesforce"

    def __init__(
        self,
        *,
        login_base_url: str,
        timeout_seconds: float = 20.0,
        transport: HttpTransport | None = None,
    ) -> None:
        self.login_base_url = login_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or HttpxTransport()

    def revoke(self, provider: str, values: dict[str, str]) -> RevocationResult:
        if provider != self.provider_name:
            return RevocationResult(status="unsupported")
        token = values.get("refresh_token") or values.get("access_token")
        if not token:
            return RevocationResult(status="failed", error_code="salesforce_token_missing")
        try:
            response = self.transport.request(
                "POST",
                f"{self.login_base_url}/services/oauth2/revoke",
                data={"token": token},
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException:
            return RevocationResult(status="failed", error_code="salesforce_revocation_timeout")
        except httpx.HTTPError:
            return RevocationResult(status="failed", error_code="salesforce_revocation_unavailable")
        if response.status_code in {200, 204}:
            return RevocationResult(status="confirmed")
        return RevocationResult(status="failed", error_code="salesforce_revocation_rejected")


_SALESFORCE_INBOUND_FIELDS = (
    "Id",
    "FirstName",
    "LastName",
    "Name",
    "Company",
    "Email",
    "Phone",
    "Title",
    "Industry",
    "NumberOfEmployees",
    "AnnualRevenue",
    "Country",
    "LeadSource",
    "Description",
    "OwnerId",
    "CreatedDate",
    "LastModifiedDate",
    "SystemModstamp",
    "IsDeleted",
)


def _salesforce_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _canonical_fingerprint(values: dict[str, Any]) -> str:
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


class SalesforceInboundCrmProvider:
    """Read-only Salesforce Lead acquisition adapter used by the inbound sync service."""

    provider_name = "salesforce"

    def __init__(
        self,
        *,
        connection_id: UUID,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        access_token: str = "",
        instance_url: str = "",
        login_base_url: str,
        api_version: str,
        config: SalesforceCrmConfig,
        timeout_seconds: float = 20.0,
        transport: HttpTransport | None = None,
    ) -> None:
        self.connection_id = connection_id
        self.api_version = api_version.removeprefix("v")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or HttpxTransport()
        self.config = config
        self._auth = SalesforceCrmProvider(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            access_token=access_token,
            instance_url=instance_url,
            login_base_url=login_base_url,
            api_version=api_version,
            config=config,
            timeout_seconds=timeout_seconds,
            transport=self.transport,
        )

    def _get(self, path: str) -> dict[str, Any]:
        token, instance_url = self._auth._authenticate()
        try:
            response = self.transport.request(
                "GET",
                f"{instance_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException:
            raise provider_timeout_error(self.provider_name) from None
        except httpx.HTTPError:
            raise ProviderOperationError(
                "Salesforce inbound request was unavailable",
                provider=self.provider_name,
                code="salesforce_inbound_unavailable",
                category="temporarily_unavailable",
                retryable=True,
            ) from None
        if response.status_code != 200:
            raise provider_http_error(self.provider_name, response.status_code)
        try:
            body = response.json()
        except Exception:
            raise provider_malformed_response_error(self.provider_name) from None
        if not isinstance(body, dict):
            raise provider_malformed_response_error(self.provider_name)
        return body

    def list_records(
        self,
        *,
        object_type: str,
        cursor: InboundCursor | None,
        limit: int,
    ) -> InboundCrmPage:
        if object_type != "Lead":
            raise ProviderOperationError(
                "Salesforce inbound object type is unsupported",
                provider=self.provider_name,
                code="salesforce_inbound_object_unsupported",
                category="configuration",
                retryable=False,
            )
        bounded_limit = max(1, min(limit, 2000))
        next_path = (cursor or {}).get("next_records_url")
        if isinstance(next_path, str):
            expected = f"/services/data/v{self.api_version}/query/"
            if not next_path.startswith(expected):
                raise ProviderOperationError(
                    "Salesforce inbound cursor is invalid",
                    provider=self.provider_name,
                    code="salesforce_inbound_cursor_invalid",
                    category="invalid_request",
                    retryable=False,
                )
            path = next_path
        else:
            where = ""
            modified_after = (cursor or {}).get("modified_after")
            record_id = (cursor or {}).get("record_id")
            if isinstance(modified_after, str) and modified_after:
                safe_time = modified_after.replace("'", "")
                where = f" WHERE SystemModstamp > {safe_time}"
                if isinstance(record_id, str) and record_id:
                    safe_id = record_id.replace("'", "")
                    where = (
                        f" WHERE (SystemModstamp > {safe_time}) OR "
                        f"(SystemModstamp = {safe_time} AND Id > '{safe_id}')"
                    )
            query = (
                f"SELECT {','.join(_SALESFORCE_INBOUND_FIELDS)} FROM Lead{where} "
                f"ORDER BY SystemModstamp ASC, Id ASC LIMIT {bounded_limit}"
            )
            path = f"/services/data/v{self.api_version}/query?{urlencode({'q': query})}"
        body = self._get(path)
        raw_records = body.get("records")
        if not isinstance(raw_records, list):
            raise provider_malformed_response_error(self.provider_name)
        records: list[ExternalCrmRecord] = []
        for value in raw_records:
            if not isinstance(value, dict) or not isinstance(value.get("Id"), str):
                raise provider_malformed_response_error(self.provider_name)
            modified_at = _salesforce_datetime(
                value.get("SystemModstamp") or value.get("LastModifiedDate")
            )
            records.append(
                ExternalCrmRecord(
                    provider=self.provider_name,
                    object_type=object_type,
                    record_id=value["Id"],
                    values=value,
                    modified_at=modified_at,
                    provider_version=str(
                        value.get("SystemModstamp") or value.get("LastModifiedDate") or ""
                    )
                    or None,
                    deleted=bool(value.get("IsDeleted", False)),
                )
            )
        checkpoint: InboundCursor | None = None
        if records:
            last = records[-1]
            checkpoint = {
                "modified_after": last.provider_version,
                "record_id": last.record_id,
            }
        next_cursor: InboundCursor | None = None
        next_url = body.get("nextRecordsUrl")
        if isinstance(next_url, str) and next_url:
            next_cursor = {"next_records_url": next_url}
        return InboundCrmPage(
            records=records,
            next_cursor=next_cursor,
            checkpoint=checkpoint,
        )

    def fetch_record(self, *, object_type: str, record_id: str) -> ExternalCrmRecord:
        if object_type != "Lead" or not record_id:
            raise ProviderOperationError(
                "Salesforce inbound record identity is invalid",
                provider=self.provider_name,
                code="salesforce_inbound_record_invalid",
                category="invalid_request",
                retryable=False,
            )
        fields = quote(",".join(_SALESFORCE_INBOUND_FIELDS), safe=",")
        body = self._get(
            f"/services/data/v{self.api_version}/sobjects/Lead/{quote(record_id, safe='')}"
            f"?fields={fields}"
        )
        actual_id = body.get("Id")
        if not isinstance(actual_id, str):
            raise provider_malformed_response_error(self.provider_name)
        modified_at = _salesforce_datetime(
            body.get("SystemModstamp") or body.get("LastModifiedDate")
        )
        return ExternalCrmRecord(
            provider=self.provider_name,
            object_type=object_type,
            record_id=actual_id,
            values=body,
            modified_at=modified_at,
            provider_version=str(
                body.get("SystemModstamp") or body.get("LastModifiedDate") or ""
            )
            or None,
            deleted=bool(body.get("IsDeleted", False)),
        )

    def normalize_record(self, record: ExternalCrmRecord) -> CanonicalInboundLead:
        if record.provider != self.provider_name or record.object_type != "Lead":
            raise ValueError("Salesforce adapter received an incompatible record")
        values = record.values
        first = str(values.get("FirstName") or "").strip()
        last = str(values.get("LastName") or "").strip()
        full_name = " ".join(part for part in (first, last) if part) or str(
            values.get("Name") or ""
        ).strip()
        email = str(values.get("Email") or "").strip().lower()
        company = str(values.get("Company") or "").strip()
        if not full_name or not email or not company:
            raise ValueError("Salesforce Lead requires name, email, and company for import")
        provider_source = str(values.get("LeadSource") or "Unknown").strip() or "Unknown"
        description = str(values.get("Description") or "").strip()
        source_owned = {
            "full_name": full_name,
            "email": email,
            "phone": values.get("Phone"),
            "company_name": company,
            "job_title": values.get("Title"),
            "industry": values.get("Industry"),
            "employee_count": values.get("NumberOfEmployees"),
            "annual_revenue": values.get("AnnualRevenue"),
            "geography": values.get("Country"),
            "provider_owner_id": values.get("OwnerId"),
            "provider_source": provider_source,
            "description": description,
        }
        normalized = {
            "source": provider_source.lower().replace(" ", "_")[:64],
            "source_detail": f"crm-import:salesforce:{provider_source}"[:255],
            "external_id": record.record_id,
            "full_name": full_name,
            "email": email,
            "phone": values.get("Phone"),
            "job_title": values.get("Title"),
            "company_name": company,
            "industry": values.get("Industry"),
            "employee_count": values.get("NumberOfEmployees"),
            "annual_revenue": values.get("AnnualRevenue"),
            "geography": values.get("Country"),
            "product_or_service": "CRM lead follow-up",
            "message": description
            or f"Salesforce Lead for {company}; original source: {provider_source}.",
            "received_at": values.get("CreatedDate") or record.modified_at,
        }
        enrichment_values = {
            target: values[target]
            for target in self.config.lead_fields.values()
            if target in values
        }
        origin = values.get(self.config.lead_external_id_field)
        return CanonicalInboundLead(
            provider=self.provider_name,
            object_type=record.object_type,
            provider_record_id=record.record_id,
            normalized_payload=normalized,
            source_fingerprint=_canonical_fingerprint(source_owned),
            provider_modified_at=record.modified_at,
            provider_version=record.provider_version,
            provider_source=provider_source,
            provider_metadata={
                "owner_id": str(values.get("OwnerId") or "") or None,
                "original_source": provider_source,
            },
            synchronization_origin=str(origin) if origin else None,
            enrichment_fingerprint=(
                _canonical_fingerprint(enrichment_values) if enrichment_values else None
            ),
            deleted=record.deleted,
            archived=record.archived,
        )
