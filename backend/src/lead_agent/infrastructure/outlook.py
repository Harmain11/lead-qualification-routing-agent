from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any, cast
from urllib.parse import quote
from uuid import UUID

import httpx

from lead_agent.domain.crm_inbound import (
    CanonicalInboundLead,
    ExternalCrmRecord,
    InboundCrmPage,
    InboundCursor,
)
from lead_agent.domain.integrations import ConnectionValidation
from lead_agent.domain.provider_errors import ProviderOperationError

OUTLOOK_SCOPES = ["openid", "profile", "email", "offline_access", "Mail.Read"]
OUTLOOK_READ_SCOPE = "Mail.Read"
_GRAPH_API = "https://graph.microsoft.com/v1.0"
_MAX_BODY_CHARS = 32_000
_MAX_RESPONSE_BYTES = 2_000_000
_AUTO_LOCAL = re.compile(
    r"(?:^|[+._-])(no.?reply|do.?not.?reply|mailer-daemon|postmaster)(?:$|[+._-])", re.I
)
_INQUIRY = re.compile(
    r"\b(contact|interested|inquiry|enquir|looking for|need|quote|pricing|price|demo|call|meeting|"
    r"proposal|evaluate|help us|reach out|reply|follow up)\b",
    re.I,
)
_BUSINESS = re.compile(
    r"\b(company|business|team|organization|enterprise|agency|startup|project|service|solution|"
    r"budget|implementation|software|vendor|client)\b",
    re.I,
)
_EXCLUDED_SUBJECT = re.compile(
    r"\b(newsletter|receipt|order confirmation|delivery (?:notice|status)|shipping|password reset|"
    r"security alert|verification code|statement|invoice|your subscription|unsubscribe)\b",
    re.I,
)


class _TextHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "head", "svg", "noscript"}:
            self.hidden += 1
        elif tag in {"br", "p", "div", "li", "tr", "blockquote"} and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "head", "svg", "noscript"} and self.hidden:
            self.hidden -= 1
        elif tag in {"p", "div", "li", "tr", "blockquote"} and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def html_to_text(value: str) -> str:
    parser = _TextHTML()
    parser.feed(value)
    return " ".join(" ".join(parser.parts).split())[:_MAX_BODY_CHARS]


def _iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC)


def _mail_address(value: object) -> tuple[str, str]:
    if not isinstance(value, dict):
        return "", ""
    address = value.get("emailAddress")
    if not isinstance(address, dict):
        return "", ""
    return str(address.get("name") or "").strip(), str(address.get("address") or "").strip().lower()


def _message_body(message: dict[str, Any]) -> str:
    body = message.get("body")
    if isinstance(body, dict):
        content = body.get("content")
        if isinstance(content, str):
            if str(body.get("contentType", "text")).lower() == "html":
                return html_to_text(content)
            return " ".join(content.split())[:_MAX_BODY_CHARS]
    preview = message.get("bodyPreview")
    return str(preview or "").strip()[:_MAX_BODY_CHARS]


def candidate_reason(message: dict[str, Any], mailbox_email: str) -> str | None:
    sender_name, sender_email = _mail_address(message.get("from"))
    del sender_name
    reply_to = ""
    reply_values = message.get("replyTo")
    if isinstance(reply_values, list) and reply_values:
        _, reply_to = _mail_address(reply_values[0])
    subject = str(message.get("subject") or "")
    text = f"{subject} {message.get('bodyPreview') or ''}"
    mailbox = mailbox_email.strip().lower()
    if not sender_email or sender_email == mailbox or reply_to == mailbox:
        return "own_or_missing_sender"
    local = sender_email.split("@", 1)[0]
    if _AUTO_LOCAL.search(local) or any(
        word in sender_email for word in ("notifications@", "notify@", "updates@", "alerts@")
    ):
        return "automated_sender"
    if _EXCLUDED_SUBJECT.search(subject) or "unsubscribe" in text.lower():
        return "bulk_or_transactional"
    if not (_INQUIRY.search(text) or _BUSINESS.search(text)):
        return "no_inquiry_signal"
    return None


class OutlookTransport:
    def __init__(
        self,
        access_token: str,
        *,
        timeout_seconds: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.access_token = access_token
        self.client = client or httpx.Client(timeout=timeout_seconds)

    def get(
        self,
        path_or_url: str,
        *,
        params: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        url = (
            path_or_url
            if path_or_url.startswith("http")
            else f"{_GRAPH_API}/{path_or_url.lstrip('/')}"
        )
        try:
            response = self.client.get(
                url,
                params=cast(Any, params),
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept": "application/json",
                },
            )
        except httpx.TimeoutException:
            raise ProviderOperationError(
                "Microsoft Graph request timed out.",
                provider="outlook",
                code="outlook_timeout",
                category="timeout",
                retryable=True,
            ) from None
        except httpx.HTTPError:
            raise ProviderOperationError(
                "Microsoft Graph is temporarily unavailable.",
                provider="outlook",
                code="outlook_transport_error",
                category="temporarily_unavailable",
                retryable=True,
            ) from None
        if response.status_code >= 400:
            raise _outlook_http_error(response, path=path_or_url)
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise ProviderOperationError(
                "Microsoft Graph returned a response that is too large.",
                provider="outlook",
                code="outlook_response_too_large",
                category="invalid_request",
                retryable=False,
            )
        try:
            payload = response.json()
        except ValueError:
            raise ProviderOperationError(
                "Microsoft Graph returned an unreadable response.",
                provider="outlook",
                code="outlook_malformed_response",
                category="malformed_response",
                retryable=False,
            ) from None
        if not isinstance(payload, dict):
            raise ProviderOperationError(
                "Microsoft Graph returned an unreadable response.",
                provider="outlook",
                code="outlook_malformed_response",
                category="malformed_response",
                retryable=False,
            )
        return cast(dict[str, Any], payload)


def _outlook_http_error(response: httpx.Response, *, path: str) -> ProviderOperationError:
    status_code = response.status_code
    try:
        payload = response.json()
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        code = str(error.get("code", "")).lower() if isinstance(error, dict) else ""
        message = str(error.get("message", "")).lower() if isinstance(error, dict) else ""
    except (ValueError, AttributeError, TypeError):
        code, message = "", ""
    if status_code == 401:
        error_code, category, reason, retryable = (
            "outlook_reauthorization_required",
            "revoked",
            "Microsoft rejected the Outlook authorization; reconnect the mailbox.",
            False,
        )
    elif status_code == 403:
        error_code, category, reason, retryable = (
            "outlook_insufficient_scope",
            "insufficient_permissions",
            "Outlook authorization is missing the required read-only Mail.Read permission.",
            False,
        )
    elif (
        status_code == 410
        or "syncstatenotfound" in code
        or "invalidcontinuationtoken" in code
        or "deltatoken" in message
    ):
        error_code, category, reason, retryable = (
            "outlook_delta_expired",
            "not_found",
            "The Outlook delta checkpoint expired and requires bounded reconciliation.",
            False,
        )
    elif status_code == 404 and "mailfolders/inbox" in path.lower():
        error_code, category, reason, retryable = (
            "outlook_mailbox_unavailable",
            "not_found",
            "The Outlook Inbox is unavailable for this mailbox.",
            False,
        )
    elif status_code == 429:
        error_code, category, reason, retryable = (
            "outlook_rate_limited",
            "rate_limited",
            "Microsoft Graph rate limit was reached.",
            True,
        )
    elif status_code >= 500:
        error_code, category, reason, retryable = (
            "outlook_provider_unavailable",
            "temporarily_unavailable",
            "Microsoft Graph is temporarily unavailable.",
            True,
        )
    else:
        error_code, category, reason, retryable = (
            "outlook_provider_error",
            "unknown",
            "Microsoft Graph rejected the read request.",
            False,
        )
    retry_after = response.headers.get("Retry-After")
    metadata: dict[str, str | int | bool] = {}
    if retry_after and retry_after.isdigit():
        metadata["retry_after_seconds"] = int(retry_after)
    return ProviderOperationError(
        reason,
        provider="outlook",
        code=error_code,
        category=cast(Any, category),
        retryable=retryable,
        status_code=status_code,
        metadata=metadata,
    )


class OutlookConnectionValidator:
    def __init__(self, timeout_seconds: float = 10.0, client: httpx.Client | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.client = client

    def validate(
        self, provider: str, credentials: dict[str, str], scopes: list[str]
    ) -> ConnectionValidation:
        del provider
        if OUTLOOK_READ_SCOPE not in scopes:
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="insufficient_permissions",
                credential_state="configured",
                message="Outlook connection is missing the required delegated Mail.Read scope.",
                error_code="outlook_insufficient_scope",
            )
        token = credentials.get("access_token", "")
        if not token:
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="invalid_credentials",
                credential_state="invalid",
                message="Outlook access token is unavailable.",
                error_code="outlook_access_token_unavailable",
            )
        try:
            api = OutlookTransport(token, timeout_seconds=self.timeout_seconds, client=self.client)
            profile = api.get("me", params={"$select": "id,displayName,mail,userPrincipalName"})
            api.get("me/mailFolders/inbox", params={"$select": "id,displayName"})
        except ProviderOperationError as error:
            status = (
                "insufficient_permissions"
                if error.category == "insufficient_permissions"
                else "revoked"
                if error.category == "revoked"
                else "temporarily_unavailable"
                if error.retryable
                else "configuration_error"
                if error.code == "outlook_mailbox_unavailable"
                else "invalid_credentials"
            )
            return ConnectionValidation(
                valid=False,
                verified=False,
                status=cast(Any, status),
                credential_state=(
                    "invalid" if status in {"invalid_credentials", "revoked"} else "configured"
                ),
                message=str(error),
                retryable=error.retryable,
                error_code=error.code,
                metadata={"category": error.category},
            )
        email = str(profile.get("mail") or profile.get("userPrincipalName") or "").strip().lower()
        account_id = str(profile.get("id") or "").strip()
        if "@" not in email or not account_id:
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="malformed_response",
                credential_state="configured",
                message="Microsoft Graph returned an invalid Outlook mailbox identity.",
                error_code="outlook_profile_malformed",
            )
        return ConnectionValidation(
            valid=True,
            verified=True,
            status="connected",
            credential_state="configured",
            message="Microsoft Outlook read-only access verified.",
            metadata={
                "outlook_email": email,
                "outlook_account_id": account_id,
                "outlook_display_name": str(profile.get("displayName") or "")[:255],
            },
        )


class OutlookInboundProvider:
    provider_name = "outlook"

    def __init__(
        self,
        connection_id: UUID,
        access_token: str,
        mailbox_email: str,
        *,
        initial_sync_days: int = 30,
        timeout_seconds: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._connection_id = connection_id
        self.mailbox_email = mailbox_email.strip().lower()
        self.initial_sync_days = max(1, min(initial_sync_days, 365))
        self.api = OutlookTransport(access_token, timeout_seconds=timeout_seconds, client=client)

    @property
    def connection_id(self) -> UUID:
        return self._connection_id

    def _initial_url_params(self, limit: int) -> dict[str, object]:
        since = (datetime.now(UTC) - timedelta(days=self.initial_sync_days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        return {
            "$select": (
                "id,conversationId,internetMessageId,subject,receivedDateTime,from,replyTo,"
                "bodyPreview,parentFolderId"
            ),
            "$filter": f"receivedDateTime ge {since}",
            "$top": min(limit, 100),
        }

    def list_records(
        self, *, object_type: str, cursor: InboundCursor | None, limit: int
    ) -> InboundCrmPage:
        if object_type != "message":
            raise ValueError("Outlook synchronization supports message records only")
        cursor = cursor or {}
        delta_url = cursor.get("delta_url")
        recovery = cursor.get("reconciliation") is True
        try:
            payload = self.api.get(
                (
                    str(delta_url)
                    if isinstance(delta_url, str) and delta_url
                    else "me/mailFolders/inbox/messages/delta"
                ),
                params=None if delta_url else self._initial_url_params(limit),
            )
        except ProviderOperationError as error:
            if error.code != "outlook_delta_expired" or not delta_url:
                raise
            recovery = True
            payload = self.api.get(
                "me/mailFolders/inbox/messages/delta",
                params=self._initial_url_params(limit),
            )
        values = payload.get("value", [])
        records: list[ExternalCrmRecord] = []
        if isinstance(values, list):
            for value in values:
                if not isinstance(value, dict) or not value.get("id"):
                    continue
                removed = value.get("@removed")
                records.append(
                    ExternalCrmRecord(
                        provider="outlook",
                        object_type="message",
                        record_id=str(value["id"]),
                        values=value,
                        modified_at=_iso_datetime(value.get("receivedDateTime")),
                        provider_version=str(
                            value.get("changeKey") or value.get("receivedDateTime") or ""
                        ),
                        deleted=isinstance(removed, dict),
                    )
                )
        next_url = payload.get("@odata.nextLink")
        delta_link = payload.get("@odata.deltaLink")
        next_cursor = (
            {"delta_url": str(next_url), "reconciliation": recovery}
            if isinstance(next_url, str) and next_url
            else None
        )
        checkpoint = (
            {"delta_url": str(delta_link), "reconciliation": recovery}
            if isinstance(delta_link, str) and delta_link
            else None
        )
        return InboundCrmPage(records=records, next_cursor=next_cursor, checkpoint=checkpoint)

    def fetch_record(self, *, object_type: str, record_id: str) -> ExternalCrmRecord:
        del object_type
        data = self.api.get(
            f"me/messages/{quote(record_id, safe='')}",
            params={
                "$select": (
                    "id,conversationId,internetMessageId,subject,receivedDateTime,from,replyTo,"
                    "body,bodyPreview,parentFolderId"
                )
            },
        )
        return ExternalCrmRecord(
            provider="outlook",
            object_type="message",
            record_id=record_id,
            values=data,
            modified_at=_iso_datetime(data.get("receivedDateTime")),
            provider_version=str(data.get("changeKey") or data.get("receivedDateTime") or ""),
        )

    def normalize_record(self, record: ExternalCrmRecord) -> CanonicalInboundLead:
        if record.deleted:
            fingerprint = hashlib.sha256(f"deleted:{record.record_id}".encode()).hexdigest()
            return CanonicalInboundLead(
                provider="outlook",
                object_type="message",
                provider_record_id=record.record_id,
                normalized_payload={"source": "email", "source_detail": "Microsoft Outlook"},
                source_fingerprint=fingerprint,
                provider_modified_at=record.modified_at,
                provider_version=record.provider_version,
                provider_source="Microsoft Outlook",
                provider_metadata={"outlook_message_id": record.record_id},
                deleted=True,
            )
        values = record.values
        if not values.get("body") and not values.get("bodyPreview"):
            record = self.fetch_record(object_type="message", record_id=record.record_id)
            values = record.values
        reason = candidate_reason(values, self.mailbox_email)
        if reason:
            raise ValueError(reason)
        sender_name, sender_email = _mail_address(values.get("from"))
        body = _message_body(values)
        if not sender_email or not body:
            raise ValueError("message_missing_sender_or_body")
        subject = str(values.get("subject") or "(no subject)")[:500]
        display = sender_name or sender_email.split("@", 1)[0]
        domain = sender_email.split("@")[-1]
        company = re.sub(r"\.(com|net|org|co|io|ai|biz|uk|us)$", "", domain, flags=re.I)
        company = company.replace(".", " ").title() or "Unknown company"
        reply_to = ""
        reply_values = values.get("replyTo")
        if isinstance(reply_values, list) and reply_values:
            _, reply_to = _mail_address(reply_values[0])
        received_at = record.modified_at or datetime.now(UTC)
        content = f"Subject: {subject}\n\n{body}"[:_MAX_BODY_CHARS]
        internet_id = str(values.get("internetMessageId") or "")
        conversation_id = str(values.get("conversationId") or "")
        payload = {
            "source": "email",
            "source_detail": "Microsoft Outlook",
            "external_id": record.record_id,
            "full_name": display[:255],
            "email": sender_email,
            "company_name": company[:255],
            "product_or_service": "Inbound inquiry",
            "message": content,
            "received_at": received_at,
            "outlook_message_id": record.record_id,
            "outlook_conversation_id": conversation_id,
            "outlook_internet_message_id": internet_id,
            "outlook_mailbox": self.mailbox_email,
            "sender_display_name": sender_name[:255],
            "reply_to": reply_to,
        }
        fingerprint = hashlib.sha256(f"{record.record_id}\0{content}".encode()).hexdigest()
        return CanonicalInboundLead(
            provider="outlook",
            object_type="message",
            provider_record_id=record.record_id,
            normalized_payload=payload,
            source_fingerprint=fingerprint,
            provider_modified_at=received_at,
            provider_version=record.provider_version or received_at.isoformat(),
            provider_source="Microsoft Outlook",
            provider_metadata={
                "outlook_message_id": record.record_id,
                "outlook_conversation_id": conversation_id,
                "outlook_internet_message_id": internet_id,
                "outlook_mailbox": self.mailbox_email,
                "sender_email": sender_email,
            },
        )
