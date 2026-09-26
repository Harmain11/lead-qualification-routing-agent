from __future__ import annotations

import base64
import binascii
import hashlib
import re
from datetime import UTC, datetime
from email.utils import parseaddr
from html.parser import HTMLParser
from typing import Any, cast
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

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_MAX_BODY_CHARS = 32_000
_MAX_MESSAGE_BYTES = 1_000_000
_AUTO_LOCAL = re.compile(
    r"(?:^|[+._-])(no.?reply|do.?not.?reply|mailer-daemon|postmaster)(?:$|[+._-])", re.I
)
_INQUIRY = re.compile(
    r"\b(contact|interested|inquiry|enquir|looking for|need|quote|pricing|price|demo|call|meeting|"
    r"proposal|evaluate|help us|reach out|reply|follow up)\b",
    re.I,
)
_BUSINESS = re.compile(
    r"\b(company|business|team|organization|enterprise|agency|startup|project|service|solution|budget|implementation|software|vendor|client)\b",
    re.I,
)
_EXCLUDED_SUBJECT = re.compile(
    r"\b(newsletter|receipt|order confirmation|delivery (?:notice|status)|shipping|password reset|"
    r"security alert|verification code|statement|invoice|your subscription|unsubscribe)\b",
    re.I,
)


class GmailTransport:
    def __init__(
        self,
        access_token: str,
        *,
        timeout_seconds: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.client = client or httpx.Client(timeout=timeout_seconds)
        self.access_token = access_token

    def get(self, path: str, **params: object) -> dict[str, Any]:
        try:
            response = self.client.get(
                f"{_API}/{path.lstrip('/')}",
                params=cast(Any, params),
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
        except httpx.TimeoutException:
            raise ProviderOperationError(
                "Gmail request timed out.",
                provider="gmail",
                code="gmail_timeout",
                category="timeout",
                retryable=True,
            ) from None
        except httpx.HTTPError:
            raise ProviderOperationError(
                "Gmail is temporarily unavailable.",
                provider="gmail",
                code="gmail_transport_error",
                category="temporarily_unavailable",
                retryable=True,
            ) from None
        if response.status_code >= 400:
            raise _gmail_http_error(response, path=path)
        if len(response.content) > _MAX_MESSAGE_BYTES:
            raise ProviderOperationError(
                "Gmail message exceeds the permitted size.",
                provider="gmail",
                code="gmail_message_too_large",
                category="invalid_request",
                retryable=False,
            )
        try:
            payload = response.json()
        except ValueError:
            raise ProviderOperationError(
                "Gmail returned an unreadable response.",
                provider="gmail",
                code="gmail_malformed_response",
                category="malformed_response",
                retryable=False,
            ) from None
        if not isinstance(payload, dict):
            raise ProviderOperationError(
                "Gmail returned an unreadable response.",
                provider="gmail",
                code="gmail_malformed_response",
                category="malformed_response",
                retryable=False,
            )
        return cast(dict[str, Any], payload)


def _gmail_http_error(response: httpx.Response, *, path: str) -> ProviderOperationError:
    status_code = response.status_code
    try:
        error_data = response.json().get("error", {})
        reasons = {
            str(item.get("reason", "")).lower()
            for item in error_data.get("errors", [])
            if isinstance(item, dict)
        }
        message = str(error_data.get("message", "")).lower()
    except (ValueError, AttributeError, TypeError):
        reasons, message = set(), ""
    if status_code == 403 and (
        "accessnotconfigured" in reasons
        or "gmail api has not been used" in message
        or "gmail api is disabled" in message
    ):
        code, category, reason, retryable = (
            "gmail_api_disabled",
            "configuration",
            "Gmail API is disabled for this Google project.",
            False,
        )
    elif status_code == 403 and (
        "insufficientpermissions" in reasons
        or "insufficient authentication scopes" in message
        or "insufficient_scope" in message
    ):
        code, category, reason, retryable = (
            "gmail_insufficient_scope",
            "insufficient_permissions",
            "Gmail authorization is missing the read-only mailbox scope.",
            False,
        )
    elif status_code == 401:
        code, category, reason, retryable = (
            "gmail_reauthorization_required",
            "revoked",
            "Google rejected the Gmail authorization; reconnect the mailbox.",
            False,
        )
    elif status_code == 404 and path.lstrip("/") == "history":
        code, category, reason, retryable = (
            "gmail_history_expired",
            "not_found",
            "Gmail history checkpoint expired.",
            False,
        )
    elif status_code == 404:
        code, category, reason, retryable = (
            "gmail_resource_not_found",
            "not_found",
            "The requested Gmail resource is no longer available.",
            False,
        )
    elif status_code == 429 or status_code >= 500:
        code, category, reason, retryable = (
            "gmail_provider_unavailable",
            "temporarily_unavailable",
            "Gmail is temporarily unavailable.",
            True,
        )
    else:
        code, category, reason, retryable = (
            "gmail_provider_error",
            "unknown",
            "Gmail rejected the read request.",
            False,
        )
    return ProviderOperationError(
        reason,
        provider="gmail",
        code=code,
        category=cast(Any, category),
        retryable=retryable,
        status_code=status_code,
    )


class GmailConnectionValidator:
    def __init__(self, timeout_seconds: float = 10.0, client: httpx.Client | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.client = client

    def validate(
        self, provider: str, credentials: dict[str, str], scopes: list[str]
    ) -> ConnectionValidation:
        del provider
        if GMAIL_READONLY_SCOPE not in scopes:
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="insufficient_permissions",
                credential_state="configured",
                message="Gmail connection is missing the required read-only scope.",
                error_code="gmail_insufficient_scope",
            )
        token = credentials.get("access_token", "")
        if not token:
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="invalid_credentials",
                credential_state="invalid",
                message="Gmail access token is unavailable.",
                error_code="gmail_access_token_unavailable",
            )
        try:
            profile = GmailTransport(
                token, timeout_seconds=self.timeout_seconds, client=self.client
            ).get("profile")
        except ProviderOperationError as error:
            status = (
                "insufficient_permissions"
                if error.category == "insufficient_permissions"
                else "configuration_error"
                if error.code == "gmail_api_disabled"
                else "revoked"
                if error.category == "revoked"
                else "temporarily_unavailable"
                if error.retryable
                else "invalid_credentials"
            )
            return ConnectionValidation(
                valid=False,
                verified=False,
                status=cast(Any, status),
                credential_state="expired"
                if status == "expired"
                else "invalid"
                if status in {"invalid_credentials", "revoked"}
                else "configured",
                message=str(error),
                retryable=error.retryable,
                error_code=error.code,
                metadata={"category": error.category},
            )
        email = profile.get("emailAddress")
        if not isinstance(email, str) or "@" not in email:
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="malformed_response",
                credential_state="configured",
                message="Gmail returned an invalid mailbox identity.",
                error_code="gmail_profile_malformed",
            )
        return ConnectionValidation(
            valid=True,
            verified=True,
            status="connected",
            credential_state="configured",
            message="Gmail read-only access verified.",
            metadata={
                "gmail_email": email.strip().lower(),
                "history_id": str(profile.get("historyId", "")),
            },
        )


def candidate_reason(headers: dict[str, str], snippet: str, mailbox_email: str) -> str | None:
    sender_name, sender_email = parseaddr(headers.get("from", ""))
    sender_email = sender_email.strip().lower()
    subject = headers.get("subject", "")
    reply_to = parseaddr(headers.get("reply-to", ""))[1].strip().lower()
    text = f"{subject} {snippet}"
    if (
        not sender_email
        or sender_email == mailbox_email.strip().lower()
        or reply_to == mailbox_email.strip().lower()
    ):
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


class _TextHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "head", "svg"}:
            self.hidden += 1
        elif tag.lower() in {"br", "p", "div", "li", "tr"} and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "head", "svg"} and self.hidden:
            self.hidden -= 1
        elif tag.lower() in {"p", "div", "li", "tr"} and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _decode_body(data: object) -> str:
    if not isinstance(data, str):
        return ""
    try:
        decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (ValueError, binascii.Error):
        return ""
    return decoded[: _MAX_BODY_CHARS * 4].decode("utf-8", errors="replace")[:_MAX_BODY_CHARS]


def parse_mime_body(payload: dict[str, Any]) -> str:
    plain: list[str] = []
    html: list[str] = []

    def visit(part: dict[str, Any]) -> None:
        mime = str(part.get("mimeType", "")).lower()
        filename = str(part.get("filename", ""))
        body = part.get("body", {})
        if filename or not isinstance(body, dict):
            return
        data = _decode_body(body.get("data"))
        if mime == "text/plain" and data:
            plain.append(data)
        elif mime == "text/html" and data:
            html.append(data)
        children = part.get("parts", [])
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    visit(child)

    visit(payload)
    content = "\n".join(plain).strip()
    if not content and html:
        parser = _TextHTML()
        parser.feed("\n".join(html))
        content = " ".join(" ".join(parser.parts).split())
    return content[:_MAX_BODY_CHARS]


class GmailInboundProvider:
    provider_name = "gmail"

    def __init__(
        self,
        connection_id: UUID,
        access_token: str,
        mailbox_email: str,
        timeout_seconds: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._connection_id = connection_id
        self.mailbox_email = mailbox_email.strip().lower()
        self.api = GmailTransport(access_token, timeout_seconds=timeout_seconds, client=client)

    @property
    def connection_id(self) -> UUID:
        return self._connection_id

    def list_records(
        self, *, object_type: str, cursor: InboundCursor | None, limit: int
    ) -> InboundCrmPage:
        if object_type != "message":
            raise ValueError("Gmail synchronization supports message records only")
        cursor = cursor or {}
        page_token = cursor.get("page_token")
        history_id = cursor.get("history_id")
        initial = cursor.get("initial") is True
        reconcile = cursor.get("reconcile") is True
        if history_id and not reconcile and not initial:
            params: dict[str, object] = {
                "startHistoryId": history_id,
                "historyTypes": "messageAdded",
                "maxResults": min(limit, 100),
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                result = self.api.get("history", **params)
            except ProviderOperationError as error:
                if error.status_code != 404:
                    raise
                history_id = None
                reconcile = True
                cursor = {"reconcile": True}
        if history_id and not reconcile and not initial:
            message_ids: dict[str, str] = {}
            histories = result.get("history", [])
            for item in histories if isinstance(histories, list) else []:
                for added in item.get("messagesAdded", []) if isinstance(item, dict) else []:
                    message = added.get("message", {}) if isinstance(added, dict) else {}
                    if isinstance(message, dict) and message.get("id"):
                        message_ids[str(message["id"])] = str(message.get("threadId", ""))
            next_token = result.get("nextPageToken")
            newest = str(result.get("historyId") or history_id)
        else:
            profile = self.api.get("profile", fields="historyId,emailAddress")
            newest = str(profile.get("historyId", ""))
            params = {
                "maxResults": min(limit, 100),
                "q": (
                    "newer_than:30d -in:spam -in:trash -category:promotions "
                    "-category:social -category:updates"
                ),
            }
            if page_token:
                params["pageToken"] = page_token
            result = self.api.get("messages", **params)
            messages = result.get("messages", [])
            message_ids = (
                {
                    str(item["id"]): str(item.get("threadId", ""))
                    for item in messages
                    if isinstance(item, dict) and item.get("id")
                }
                if isinstance(messages, list)
                else {}
            )
            next_token = result.get("nextPageToken")
            reconcile = False
        records: list[ExternalCrmRecord] = []
        for message_id, thread_id in message_ids.items():
            records.append(
                ExternalCrmRecord(
                    provider="gmail",
                    object_type="message",
                    record_id=message_id,
                    values={"thread_id": thread_id},
                )
            )
        next_cursor = (
            {
                "history_id": newest,
                "page_token": str(next_token),
                "initial": initial or not cursor,
                "reconcile": reconcile,
            }
            if next_token
            else None
        )
        return InboundCrmPage(
            records=records,
            next_cursor=next_cursor,
            checkpoint={"history_id": newest} if newest else None,
        )

    def fetch_record(self, *, object_type: str, record_id: str) -> ExternalCrmRecord:
        del object_type
        data = self.api.get(
            f"messages/{record_id}",
            format="metadata",
            metadataHeaders=["From", "Reply-To", "Subject", "Date"],
            fields="id,threadId,labelIds,internalDate,snippet,payload(headers)",
        )
        headers = {
            str(item.get("name", "")).lower(): str(item.get("value", ""))
            for item in data.get("payload", {}).get("headers", [])
            if isinstance(item, dict)
        }
        labels = set(data.get("labelIds", []))
        if labels.intersection({"SPAM", "TRASH", "DRAFT", "SENT"}):
            return ExternalCrmRecord(
                provider="gmail",
                object_type="message",
                record_id=record_id,
                values={"skip_reason": "system_or_own_message"},
            )
        reason = candidate_reason(headers, str(data.get("snippet", "")), self.mailbox_email)
        if reason:
            return ExternalCrmRecord(
                provider="gmail",
                object_type="message",
                record_id=record_id,
                values={"skip_reason": reason},
            )
        full = self.api.get(
            f"messages/{record_id}",
            format="full",
            fields="id,threadId,internalDate,payload(mimeType,filename,headers,body(data),parts(mimeType,filename,body(data),parts))",
        )
        payload = full.get("payload", {})
        full_headers = {
            str(item.get("name", "")).lower(): str(item.get("value", ""))
            for item in payload.get("headers", [])
            if isinstance(item, dict)
        }
        body = parse_mime_body(payload)
        if not body:
            body = str(data.get("snippet", ""))[:_MAX_BODY_CHARS]
        return ExternalCrmRecord(
            provider="gmail",
            object_type="message",
            record_id=record_id,
            values={
                "headers": full_headers or headers,
                "thread_id": str(full.get("threadId") or data.get("threadId", "")),
                "body": body,
                "snippet": str(data.get("snippet", ""))[:1000],
            },
            modified_at=datetime.fromtimestamp(
                int(full.get("internalDate", data.get("internalDate", "0"))) / 1000, tz=UTC
            ),
        )

    def normalize_record(self, record: ExternalCrmRecord) -> CanonicalInboundLead:
        values = record.values
        if values.get("skip_reason"):
            raise ValueError(str(values["skip_reason"]))
        if not values.get("headers"):
            try:
                record = self.fetch_record(object_type="message", record_id=record.record_id)
            except ProviderOperationError as error:
                # History can include drafts or messages deleted before acquisition.
                if error.code == "gmail_resource_not_found":
                    raise ValueError("message_no_longer_available") from None
                raise
            values = record.values
            if values.get("skip_reason"):
                raise ValueError(str(values["skip_reason"]))
        headers = values.get("headers", {})
        if not isinstance(headers, dict):
            raise ValueError("Gmail message headers are unavailable")
        sender_name, sender_email = parseaddr(str(headers.get("from", "")))
        email = sender_email.strip().lower()
        body = str(values.get("body", ""))[:_MAX_BODY_CHARS]
        if not email or not body:
            raise ValueError("Gmail message does not contain an importable sender and body")
        subject = str(headers.get("subject", "(no subject)"))[:500]
        display = sender_name.strip() or email.split("@", 1)[0]
        local_domain = email.split("@")[-1]
        company = (
            re.sub(r"\.(com|net|org|co|io|ai|biz|uk|us)$", "", local_domain, flags=re.I)
            .replace(".", " ")
            .title()
        )
        content = f"Subject: {subject}\n\n{body}"[:_MAX_BODY_CHARS]
        payload = {
            "source": "email",
            "source_detail": "Gmail",
            "external_id": record.record_id,
            "full_name": display,
            "email": email,
            "company_name": company or "Unknown company",
            "product_or_service": "Inbound inquiry",
            "message": content,
            "received_at": record.modified_at or datetime.now(UTC),
            "gmail_message_id": record.record_id,
            "gmail_thread_id": str(values.get("thread_id", "")),
            "gmail_mailbox": self.mailbox_email,
            "sender_display_name": sender_name[:255],
            "reply_to": parseaddr(str(headers.get("reply-to", "")))[1].strip().lower(),
        }
        fingerprint = hashlib.sha256(f"{record.record_id}\0{content}".encode()).hexdigest()
        return CanonicalInboundLead(
            provider="gmail",
            object_type="message",
            provider_record_id=record.record_id,
            normalized_payload=payload,
            source_fingerprint=fingerprint,
            provider_modified_at=record.modified_at,
            provider_version=str(record.modified_at.timestamp()) if record.modified_at else None,
            provider_source="Gmail",
            provider_metadata={
                "gmail_thread_id": str(values.get("thread_id", "")),
                "gmail_message_id": record.record_id,
                "gmail_mailbox": self.mailbox_email,
                "sender_email": email,
            },
            enrichment_fingerprint=None,
        )
