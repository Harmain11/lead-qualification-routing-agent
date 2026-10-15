"""Private Integration contact search and explicit contact writeback for HighLevel."""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast
from uuid import UUID

from lead_agent.domain.crm import (
    CanonicalCrmPayload,
    CrmEntityReferences,
    CrmErrorCategory,
    CrmProviderError,
    CrmSyncResult,
)
from lead_agent.domain.crm_inbound import (
    CanonicalInboundLead,
    ExternalCrmRecord,
    InboundCrmPage,
    InboundCursor,
)
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.infrastructure.communications import CommunicationsClient, identifier, timestamp


class GoHighLevelCrmProvider:
    provider_name = "gohighlevel"

    def __init__(
        self,
        connection_id: UUID | None,
        credentials: dict[str, str],
        *,
        client: CommunicationsClient | None = None,
    ):
        self.connection_id = connection_id
        self.location_id = identifier(credentials["location_id"])
        self.client = client or CommunicationsClient(self.provider_name, credentials)

    def _record(self, data: Any) -> ExternalCrmRecord:
        if (
            not isinstance(data, dict)
            or not data.get("id")
            or data.get("locationId") != self.location_id
        ):
            raise ValueError("HighLevel contact identity or location is invalid.")
        changed = timestamp(data.get("dateUpdated"))
        return ExternalCrmRecord(
            provider=self.provider_name,
            object_type="contacts",
            record_id=identifier(data["id"]),
            values=data,
            modified_at=changed,
            provider_version=changed.isoformat() if changed else None,
        )

    def list_records(
        self, *, object_type: str, cursor: InboundCursor | None, limit: int
    ) -> InboundCrmPage:
        if object_type != "contacts":
            raise ValueError("HighLevel imports Contacts only.")
        size = max(1, min(limit, 100))
        current = dict(cursor or {})
        after = current.get("search_after")
        payload: dict[str, Any] = {"locationId": self.location_id, "pageLimit": size}
        if isinstance(after, list) and after:
            payload["searchAfter"] = after
        else:
            payload["page"] = max(1, int(current.get("page") or 1))
        data = self.client.request("POST", "/contacts/search", payload=payload)
        contacts = data.get("contacts")
        if not isinstance(contacts, list):
            raise ValueError("HighLevel returned invalid contact search results.")
        records = [self._record(item) for item in contacts[:size]]
        next_cursor: InboundCursor | None = None
        if len(records) == size:
            last_cursor = contacts[size - 1].get("searchAfter")
            next_cursor = (
                {"search_after": last_cursor}
                if isinstance(last_cursor, list) and last_cursor
                else {"page": int(current.get("page") or 1) + 1}
            )
            if isinstance(next_cursor.get("page"), int) and int(next_cursor["page"]) * size > 10000:
                raise ValueError(
                    "HighLevel must return searchAfter to import more than 10,000 contacts."
                )
        # Full source scan on each completed run. Source fingerprints deduplicate
        # unchanged contacts, including edits older than the latest timestamp.
        return InboundCrmPage(records=records, next_cursor=next_cursor, checkpoint={})

    def fetch_record(self, *, object_type: str, record_id: str) -> ExternalCrmRecord:
        if object_type != "contacts":
            raise ValueError("HighLevel imports Contacts only.")
        return self._record(
            self.client.request("GET", "/contacts/" + identifier(record_id)).get("contact")
        )

    def normalize_record(self, record: ExternalCrmRecord) -> CanonicalInboundLead:
        if record.provider != self.provider_name or record.object_type != "contacts":
            raise ValueError("Incompatible HighLevel record.")
        values = record.values
        email = str(values.get("email") or "").strip().lower()
        name = (
            " ".join(
                str(values.get(key) or "").strip() for key in ("firstName", "lastName")
            ).strip()
            or str(values.get("contactName") or "").strip()
        )
        if not name or "@" not in email:
            raise ValueError("HighLevel contact requires a name and email for import.")
        company = str(values.get("companyName") or email.split("@", 1)[1]).strip()
        source = str(values.get("source") or "HighLevel")[:200]
        owned = {
            key: values.get(key)
            for key in (
                "firstName",
                "lastName",
                "contactName",
                "email",
                "phone",
                "companyName",
                "source",
                "dnd",
                "dndSettings",
            )
        }
        fingerprint = hashlib.sha256(
            json.dumps(owned, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return CanonicalInboundLead(
            provider=self.provider_name,
            object_type="contacts",
            provider_record_id=record.record_id,
            source_fingerprint=fingerprint,
            provider_modified_at=record.modified_at,
            provider_version=record.provider_version,
            provider_source=source,
            provider_metadata={
                "location_id": self.location_id,
                "original_source": source,
                "dnd": bool(values.get("dnd")),
                "dnd_call": bool(
                    (values.get("dndSettings") or {}).get("Call", {}).get("status") == "active"
                ),
                "dnd_email": bool(
                    (values.get("dndSettings") or {}).get("Email", {}).get("status") == "active"
                ),
            },
            normalized_payload={
                "source": "gohighlevel",
                "source_detail": "crm-import:gohighlevel:" + source,
                "external_id": record.record_id,
                "full_name": name,
                "email": email,
                "phone": values.get("phone"),
                "company_name": company,
                "product_or_service": "CRM lead follow-up",
                "message": f"HighLevel contact for {company}; original source: {source}.",
                "received_at": timestamp(values.get("dateAdded")) or record.modified_at,
            },
        )

    def sync(
        self, payload: CanonicalCrmPayload, existing: CrmEntityReferences | None = None
    ) -> CrmSyncResult:
        fields = {
            "firstName": payload.first_name,
            "lastName": payload.last_name,
            "email": payload.email,
            "companyName": payload.company,
        }
        if payload.phone:
            fields["phone"] = payload.phone
        try:
            if existing and existing.contact_id:
                record_id = identifier(existing.contact_id)
                self.fetch_record(object_type="contacts", record_id=record_id)
                data = self.client.request("PUT", "/contacts/" + record_id, payload=fields)
            else:
                data = self.client.request(
                    "POST", "/contacts/upsert", payload={**fields, "locationId": self.location_id}
                )
            contact = self._record(data.get("contact"))
        except ProviderOperationError as error:
            categories = {
                "invalid_credentials": "authentication",
                "insufficient_permissions": "configuration",
                "rate_limited": "rate_limit",
                "temporarily_unavailable": "transient",
                "timeout": "transient",
            }
            raise CrmProviderError(
                str(error),
                code=error.code,
                category=cast(CrmErrorCategory, categories.get(error.category, "validation")),
                retryable=error.retryable,
            ) from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise CrmProviderError(
                "HighLevel contact writeback could not be confirmed.",
                code="gohighlevel_invalid_response",
                category="validation",
                retryable=False,
            ) from None
        return CrmSyncResult(
            provider=self.provider_name, entities=CrmEntityReferences(contact_id=contact.record_id)
        )
