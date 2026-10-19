from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import time
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from typing import Any, cast
from urllib.parse import unquote
from uuid import UUID

import httpx

from lead_agent.domain.crm_inbound import (
    CanonicalInboundLead,
    ExternalCrmRecord,
    InboundCrmPage,
    InboundCursor,
)
from lead_agent.domain.gmail import GmailEvidence, GmailLeadExtraction, GmailProspectCandidate
from lead_agent.domain.integrations import ConnectionValidation
from lead_agent.domain.provider_errors import ProviderOperationError

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_MAX_BODY_CHARS = 32_000
_MAX_MESSAGE_BYTES = 1_000_000
_AUTO_LOCAL = re.compile(
    r"(?:^|[+._-])(no.?reply|do.?not.?reply|mailer-daemon|postmaster)(?:$|[+._-])", re.I
)
_INQUIRY = re.compile(
    r"\b(?:quote|quotation|pricing|demo|proposal|consultation)\s+(?:request|inquiry|enquiry)\b|"
    r"\b(?:request(?:ing)?|need|want|would like|please (?:send|share|provide)|"
    r"(?:can|could|would) you (?:please )?(?:send|share|provide))\b"
    r".{0,100}\b(?:quote|quotation|pricing|price|demo|proposal|consultation)\b|"
    r"\b(?:can|could) (?:we|i) (?:please )?(?:schedule|book|see|get|request)"
    r".{0,60}\b(?:demo|consultation|quote|proposal)\b",
    re.I | re.S,
)
_BUSINESS = re.compile(
    r"\b(?:services?|solutions?|software|platform|product|automation|implementation|"
    r"consulting|website|vendor|agency|budget|business|project)\b",
    re.I,
)
_BUYER_INTENT = re.compile(
    r"\b(?:(?:i|we)\s+(?:am |are |would |'d )?(?:interested in|looking for|"
    r"need|want|require|like to)|(?:looking|searching) for|"
    r"(?:can|could) you (?:please )?(?:help us|help me|build|develop|implement))\b"
    r"[^.!?\n]{0,180}",
    re.I,
)
_EXCLUDED_SUBJECT = re.compile(
    r"\b(newsletter|receipt|order confirmation|delivery (?:notice|status)|shipping|password reset|"
    r"security alert|verification code|statement|invoice|your subscription|unsubscribe)\b",
    re.I,
)
_EVIDENCE_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
        "\u200b": "",
    }
)


def _normalized_evidence(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).translate(_EVIDENCE_TRANSLATION)
    # Quoting adds transport formatting between wrapped words, not authored text.
    normalized = re.sub(r"(?m)^[ \t]*(?:>[ \t]*)+", "", normalized)
    # Plain-text MIME may repeat a telephone link after its displayed number.
    # Remove only a link whose number is identical to that displayed number.
    normalized = _PHONE_LINK.sub(
        lambda match: match["display"] if _phone_evidence_is_from_source(
            match["display"], match["link"]
        ) else match.group(0),
        normalized,
    )
    return " ".join(normalized.casefold().split())


def _evidence_is_from_source(source: str, excerpt: str) -> bool:
    normalized_excerpt = _normalized_evidence(excerpt)
    return bool(normalized_excerpt) and normalized_excerpt in _normalized_evidence(source)


_PHONE_TOKEN = re.compile(
    r"(?<!\d)(?:\+?1[\s()./%\-]*)?(?:\(\s*\d{3}\s*\)|\d{3})"
    r"[\s()./%\-]*\d{3}[\s()./%\-]*\d{4}(?!\d)"
)
_PHONE_LINK = re.compile(
    rf"(?P<display>{_PHONE_TOKEN.pattern})\s*<(?P<link>[^<>\n]{{1,100}})>"
)


def _phone_tokens(value: str) -> set[str]:
    decoded = unquote(unicodedata.normalize("NFKC", value))
    return {
        re.sub(r"\D", "", match.group(0))
        for match in _PHONE_TOKEN.finditer(decoded)
    }


def _same_phone(left: str, right: str) -> bool:
    first = re.sub(r"\D", "", left)
    second = re.sub(r"\D", "", right)
    if first == second:
        return True
    return (
        len(first) == 11 and first.startswith("1") and first[1:] == second
    ) or (
        len(second) == 11 and second.startswith("1") and second[1:] == first
    )


def _phone_evidence_is_from_source(source: str, excerpt: str) -> bool:
    excerpt_tokens = _phone_tokens(excerpt)
    source_tokens = _phone_tokens(source)
    return bool(excerpt_tokens and any(
        _same_phone(evidenced, sourced)
        for evidenced in excerpt_tokens
        for sourced in source_tokens
    ))


def _field_evidence_matches(
    field: str, source: str, excerpt: str, value: str | None = None,
) -> bool:
    if field == "phone":
        return (
            _phone_evidence_is_from_source(source, excerpt)
            and (value is None or _phone_evidence_is_from_source(excerpt, value))
        )
    return (
        _evidence_is_from_source(source, excerpt)
        and (value is None or _evidence_is_from_source(excerpt, value))
    )


def _verified_optional_fields(candidate: Any, sources: dict[str, str]) -> tuple[Any, list[str]]:
    """Unknown enrichment must not veto a separately verified prospect identity."""
    optional = {"company_name", "job_title", "requested_service", "referral_context"}
    if candidate.email:
        optional.add("phone")
    dropped = []
    for field in optional:
        value = getattr(candidate, field, None)
        if value and not any(
            item.field == field
            and _field_evidence_matches(
                field, sources[item.source], item.excerpt, value
            )
            for item in candidate.evidence
        ):
            dropped.append(field)
    return candidate.model_copy(update={
        **{field: None for field in dropped},
        "evidence": [
            item for item in candidate.evidence
            if item.field not in optional
            or (item.field not in dropped and getattr(candidate, item.field, None)
                and _evidence_is_from_source(sources[item.source], item.excerpt))
        ],
    }), sorted(dropped)


def _ground_candidate_identity(
    candidate: GmailProspectCandidate,
    sources: dict[str, str],
    eligible_emails: set[str],
    outreach_emails: set[str],
) -> GmailProspectCandidate:
    """Use parsed participant headers, not an LLM's choice of evidence label."""
    email = (candidate.email or "").strip().lower()
    evidence = []
    for item in candidate.evidence:
        if item.field == "phone" and not _field_evidence_matches(
            "phone", sources[item.source], item.excerpt
        ) and _field_evidence_matches("phone", sources["body"], item.excerpt):
            item = item.model_copy(update={"source": "body"})
        if (
            item.field == "full_name" and email in eligible_emails
            and not _evidence_is_from_source(sources[item.source], item.excerpt)
            and _evidence_is_from_source(sources["body"], item.excerpt)
        ):
            item = item.model_copy(update={"source": "body"})
        evidence.append(item)
    if email in eligible_emails:
        for line in sources["body"].splitlines():
            match = re.match(
                r"(?i)^\s*(?:>\s*)*(From|Reply-To|To):\s*(.*)$", line
            )
            if match is None or len(line) > 500:
                continue
            if match[1].casefold() == "to" and email not in outreach_emails:
                continue
            pairs = getaddresses([match[2]])
            if not any(address.strip().lower() == email for _, address in pairs):
                continue
            evidence = [item for item in evidence if item.field != "email"]
            evidence.append(GmailEvidence(field="email", source="body", excerpt=line))
            break
        name = (candidate.full_name or "").strip()
        name_excerpt = ""
        if name:
            for block in _forwarded_header_blocks(sources["body"]):
                _, author = parseaddr(block["headers"].get("from", ""))
                if author.strip().lower() != email:
                    continue
                # The correspondent's own signature may expand a shortened or
                # non-personal From display name. Never borrow a nested author's name.
                own_body = re.split(
                    r"(?im)^\s*(?:>\s*)*From:", block["body"], maxsplit=1
                )[0]
                lines = own_body.splitlines()
                for index, line in enumerate(lines):
                    clean = re.sub(r"^\s*(?:>\s*)*", "", line).strip().strip("_* ")
                    if (
                        _normalized_evidence(clean) == _normalized_evidence(name)
                        and len(line) <= 500
                        and email in "\n".join(lines[index:index + 7]).lower()
                    ):
                        name_excerpt = line
                        break
                if name_excerpt:
                    break
        if name_excerpt:
            evidence = [item for item in evidence if item.field != "full_name"]
            evidence.append(GmailEvidence(
                field="full_name", source="body", excerpt=name_excerpt
            ))
    return candidate.model_copy(update={"evidence": evidence})


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
        response: httpx.Response | None = None
        for attempt in range(3):
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
            if response.status_code < 400:
                break
            error = _gmail_http_error(response, path=path)
            if not error.retryable or attempt == 2:
                raise error
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = min(2.0, max(0.0, float(retry_after)))
            except ValueError:
                delay = 0.2 * (2**attempt)
            time.sleep(delay)
        if response is None:
            raise ProviderOperationError(
                "Gmail is temporarily unavailable.",
                provider="gmail",
                code="gmail_transport_error",
                category="temporarily_unavailable",
                retryable=True,
            )
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

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.client.post(
                f"{_API}/{path.lstrip('/')}",
                json=payload,
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
        try:
            result = response.json()
        except ValueError:
            raise ProviderOperationError(
                "Gmail returned an unreadable response.",
                provider="gmail",
                code="gmail_malformed_response",
                category="malformed_response",
                retryable=False,
            ) from None
        if not isinstance(result, dict):
            raise ProviderOperationError(
                "Gmail returned an unreadable response.",
                provider="gmail",
                code="gmail_malformed_response",
                category="malformed_response",
                retryable=False,
            )
        return cast(dict[str, Any], result)


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
        is_send = path.lstrip("/") == "messages/send"
        code, category, reason, retryable = (
            "gmail_insufficient_scope",
            "insufficient_permissions",
            (
                "Gmail authorization is missing permission to send email. "
                "Reconnect Gmail and grant send permission."
                if is_send
                else "Gmail authorization is missing permission to read this mailbox."
            ),
            False,
        )
    elif status_code == 403 and reasons.intersection(
        {"userratelimitexceeded", "ratelimitexceeded", "quotaexceeded"}
    ):
        code, category, reason, retryable = (
            "gmail_rate_limited",
            "temporarily_unavailable",
            "Gmail is temporarily limiting requests. Wait briefly and retry.",
            True,
        )
    elif status_code == 403:
        is_send = path.lstrip("/") == "messages/send"
        code, category, reason, retryable = (
            (
                "gmail_send_forbidden"
                if is_send
                else "gmail_thread_access_denied"
            ),
            "insufficient_permissions",
            (
                (
                    "Gmail denied the email send. Reconnect Gmail and verify "
                    "the account is allowed to send this message."
                )
                if is_send
                else (
                    "Google denied access to this Gmail thread. Check that it is "
                    "available in the connected mailbox; reconnect Gmail if this "
                    "affects every thread."
                )
            ),
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


def _authored_email_text(text: str) -> str:
    """Exclude quoted/forwarded content and common signatures from new-lead intent."""
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if (
            stripped.startswith(">")
            or re.match(r"(?i)^on .{0,200}wrote:$", stripped)
            or re.match(r"(?i)^-{2,}\s*(?:original|forwarded) message", stripped)
            or re.match(r"(?i)^begin forwarded message:", stripped)
            or stripped == "--"
        ):
            break
        lines.append(line)
    return "\n".join(lines)[:8000]


def _forwarded_lead(text: str) -> tuple[str, str, str, str] | None:
    """Return the identity and authored content of an explicitly forwarded email."""
    marker = re.search(
        r"(?im)^(?:-{2,}\s*(?:original|forwarded) message\s*-*|begin forwarded message:)\s*$",
        text,
    )
    if marker is not None:
        forwarded = text[marker.end():]
    else:
        # Some clients omit the usual forwarded-message banner. Require a real
        # inline header block rather than treating arbitrary "From:" prose as a lead.
        header_start = re.search(r"(?im)^From:\s*.+$", text)
        if header_start is None:
            return None
        forwarded = text[header_start.start():]
    header_block, separator, body = forwarded.partition("\n\n")
    headers: dict[str, str] = {}
    for line in header_block.splitlines():
        key, colon, value = line.partition(":")
        if colon and key.strip().lower() in {"from", "subject", "date", "to"}:
            headers[key.strip().lower()] = value.strip()
    if marker is None and not ({"subject", "date", "to"} & headers.keys()):
        return None
    name, address = parseaddr(headers.get("from", ""))
    address = address.strip().lower()
    authored = _authored_email_text(body if separator else "")
    if not address or not authored:
        return None
    return name.strip(), address, headers.get("subject", ""), authored


def _thread_from_participants(
    headers: dict[str, str],
    thread_messages: list[dict[str, Any]],
    internal_domains: set[str],
    internal_emails: set[str],
) -> list[tuple[str, str]]:
    participants: dict[str, str] = {}

    def add(value: str) -> None:
        name, address = parseaddr(value)
        address = address.strip().lower()
        if not address or "@" not in address:
            return
        if _is_internal_address(address, internal_domains, internal_emails):
            return
        participants.setdefault(address, name.strip())

    def add_addresses(value: str) -> None:
        for name, address in getaddresses([value]):
            add(f"{name} <{address}>" if name else address)

    add(headers.get("from", ""))
    add_addresses(headers.get("reply-to", ""))
    for message in thread_messages:
        message_headers = message.get("headers", {})
        if isinstance(message_headers, dict):
            add(str(message_headers.get("from", "")))
            add_addresses(str(message_headers.get("reply-to", "")))
        lines = str(message.get("body", "")).splitlines()
        for block in _forwarded_header_blocks(str(message.get("body", ""))):
            add(str(block["headers"].get("from", "")))
            add_addresses(str(block["headers"].get("reply-to", "")))
        for index, line in enumerate(lines):
            match = re.match(r"(?i)^\s*>*\s*From:\s*(.+?)\s*$", line)
            if match is None:
                continue
            nearby = "\n".join(lines[index + 1:index + 6])
            if not re.search(r"(?im)^\s*>*\s*(?:To|Date|Subject):", nearby):
                continue
            add(match.group(1))
    return [(name, email) for email, name in participants.items()]


def _forwarded_header_blocks(text: str) -> list[dict[str, str]]:
    """Parse complete inline From/To header blocks, including nested quoted prefixes."""
    lines = text.splitlines()
    blocks: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r"(?i)^\s*(?:>\s*)*From:\s*(.+?)\s*$", line)
        if match is None:
            continue
        headers = {"from": match.group(1).strip()}
        end = index + 1
        while end < len(lines) and end <= index + 10:
            candidate = re.sub(r"^\s*(?:>\s*)+", "", lines[end]).strip()
            if not candidate:
                break
            key, colon, value = candidate.partition(":")
            normalized_key = key.strip().casefold()
            if colon and normalized_key in {
                "from", "to", "cc", "reply-to", "subject", "date", "message-id"
            }:
                headers[normalized_key] = value.strip()
                end += 1
                continue
            break
        if {"to", "subject", "date", "reply-to"} & headers.keys():
            blocks.append(
                {
                    "headers": headers,
                    "body": "\n".join(lines[end + 1 :]),
                }
            )
    return blocks


def _signature_contacts(text: str) -> list[dict[str, str]]:
    """Collect signature-only identities as context; callers must not promote them."""
    lines = text.splitlines()
    contacts: dict[str, dict[str, str]] = {}
    for index, line in enumerate(lines):
        if not re.match(
            r"(?i)^\s*(?:--\s*|best(?: regards)?|kind regards|warm regards|"
            r"regards|sincerely|cheers|thanks)[,!]?\s*$",
            line,
        ):
            continue
        signature = lines[index + 1:index + 9]
        emails = sorted({
            address.lower()
            for entry in signature
            for address in re.findall(
                r"(?i)\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}\b",
                entry,
            )
        })
        display_name = next(
            (
                entry.strip()
                for entry in signature[:3]
                if entry.strip()
                and "@" not in entry
                and not re.search(r"\d{3}", entry)
            ),
            "",
        )
        excerpt = "\n".join(signature).strip()
        for address in emails:
            contacts.setdefault(
                address,
                {
                    "name": display_name,
                    "email": address,
                    "role": "signature_only",
                    "excerpt": excerpt[:500],
                },
            )
    return list(contacts.values())


def _is_internal_address(
    address: str, internal_domains: set[str], internal_emails: set[str]
) -> bool:
    normalized = address.strip().lower()
    return bool(
        normalized
        and (
            normalized in internal_emails
            or ("@" in normalized and (
                normalized.rsplit("@", 1)[-1] in internal_domains
                or any(
                    re.sub(r"[^a-z0-9]", "", label) in {
                        "ingenuity", "ingenuitysys", "ingenuitysystem", "ingenuitysystems",
                    }
                    for label in normalized.rsplit("@", 1)[-1].split(".")[:-1]
                )
            ))
        )
    )


def _internal_outreach_recipients(
    headers: dict[str, str],
    messages: list[dict[str, Any]],
    internal_domains: set[str],
    internal_emails: set[str],
) -> list[tuple[str, str]]:
    """Find external To recipients of internal-authored messages, never Cc recipients."""
    recipients: dict[str, str] = {}
    sources = [{"headers": headers, "body": ""}, *messages]
    for message in sources:
        raw_headers = message.get("headers", {})
        if not isinstance(raw_headers, dict):
            continue
        bodies = [str(message.get("body", ""))]
        groups = [(raw_headers, bodies[0])]
        groups.extend(
            (block["headers"], block["body"])
            for block in _forwarded_header_blocks(bodies[0])
        )
        for block_headers, _body in groups:
            block_name, block_sender = parseaddr(str(block_headers.get("from", "")))
            if not _is_internal_address(block_sender, internal_domains, internal_emails):
                continue
            for display_name, address in getaddresses([str(block_headers.get("to", ""))]):
                address = address.strip().lower()
                if (
                    not address
                    or "@" not in address
                    or _is_internal_address(address, internal_domains, internal_emails)
                ):
                    continue
                recipients.setdefault(address, display_name.strip())
    return [(name, address) for address, name in recipients.items()]


def _evidence_message_id(
    source: str,
    excerpt: str,
    messages: list[dict[str, Any]],
    fallback_id: str,
) -> str:
    header_names = {
        "subject": "subject",
        "from_header": "from",
        "reply_to": "reply-to",
    }
    for message in messages:
        message_id = str(message.get("message_id") or fallback_id)
        message_headers = message.get("headers", {})
        if isinstance(message_headers, dict):
            source_text = (
                str(message.get("body", ""))
                if source == "body"
                else str(message_headers.get(header_names.get(source, ""), ""))
            )
            if source_text and _evidence_is_from_source(source_text, excerpt):
                return message_id
        if source == "from_header":
            for line in str(message.get("body", "")).splitlines():
                if re.match(r"(?i)^\s*>*\s*From:", line) and _evidence_is_from_source(
                    line, excerpt
                ):
                    return message_id
    return fallback_id


def _context_message_id(context: str, excerpt: str, fallback_id: str) -> str:
    position = context.find(excerpt)
    if position < 0:
        return fallback_id
    markers = list(re.finditer(r"(?m)^--- Gmail message ([^ ]+) ---$", context[:position]))
    return markers[-1].group(1) if markers else fallback_id


def candidate_reason(headers: dict[str, str], snippet: str, mailbox_email: str) -> str | None:
    sender_name, sender_email = parseaddr(headers.get("from", ""))
    sender_email = sender_email.strip().lower()
    forwarded = _forwarded_lead(snippet)
    if forwarded is not None:
        _forwarded_name, sender_email, forwarded_subject, forwarded_body = forwarded
        subject = forwarded_subject
        authored = forwarded_body
    else:
        subject = headers.get("subject", "")
        authored = _authored_email_text(snippet)
    reply_to = parseaddr(headers.get("reply-to", ""))[1].strip().lower()
    is_reply = bool(headers.get("in-reply-to")) or bool(
        re.match(r"(?i)^\s*(?:re|fw|fwd)\s*:", subject)
    )
    # A reply's old subject is not evidence of a new buying inquiry.
    text = authored if is_reply else f"{subject}\n{authored}"
    if (
        not sender_email
        or (forwarded is None and sender_email == mailbox_email.strip().lower())
        or (forwarded is None and reply_to == mailbox_email.strip().lower())
    ):
        return "own_or_missing_sender"
    local = sender_email.split("@", 1)[0]
    if _AUTO_LOCAL.search(local) or any(
        word in sender_email for word in ("notifications@", "notify@", "updates@", "alerts@")
    ):
        return "automated_sender"
    if headers.get("auto-submitted", "no").strip().lower() not in {"", "no"}:
        return "automated_sender"
    if (
        headers.get("list-id")
        or headers.get("list-unsubscribe")
        or headers.get("precedence", "").strip().lower() in {"bulk", "list", "junk"}
    ):
        return "bulk_or_transactional"
    if _EXCLUDED_SUBJECT.search(subject) or "unsubscribe" in text.lower():
        return "bulk_or_transactional"
    buyer_clauses = _BUYER_INTENT.finditer(text)
    if not (
        _INQUIRY.search(text)
        or any(_BUSINESS.search(match.group()) for match in buyer_clauses)
    ):
        return "no_inquiry_signal"
    return None


def _is_lead_notification_wrapper(headers: dict[str, str], body: str = "") -> bool:
    subject = str(headers.get("subject", ""))
    sender = str(headers.get("from", ""))
    subject_is_notification = bool(
        re.search(
            r"\b(?:lead\s+notifications?|new\s+lead\s+(?:alert|submission|received))\b",
            subject,
            re.I,
        )
    )
    notification_sender = bool(
        re.search(r"\blead\s+notifications?\b", sender, re.I)
    )
    structured_lead_body = bool(
        re.search(
            r"(?im)^\s*(?:lead\s+details|lead\s+information|lead\s+payload)\s*[:#]?",
            body[:4000],
        )
    )
    return subject_is_notification or notification_sender or structured_lead_body


def _email_appears_as_lead_identity(excerpt: str, email: str) -> bool:
    """Allow evidenced embedded identity email, but never a To/Cc-only address."""
    return any(
        email.casefold() in line.casefold()
        and not re.match(r"(?i)^\s*(?:>\s*)*(?:to|cc|bcc)\s*:", line)
        for line in excerpt.splitlines()
    )


def _email_appears_in_to_header(excerpt: str, email: str) -> bool:
    return any(
        email.casefold() in line.casefold()
        and re.match(r"(?i)^\s*(?:>\s*)*To\s*:", line)
        for line in excerpt.splitlines()
    )


def _is_notification_label(value: str) -> bool:
    return bool(re.search(r"\blead\s+notifications?\b", value, re.I))


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
        if not isinstance(body, dict):
            return
        if filename and mime not in {"text/plain", "message/rfc822"}:
            return
        data = _decode_body(body.get("data"))
        if mime in {"text/plain", "message/rfc822"} and data:
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
    if html:
        parser = _TextHTML()
        parser.feed("\n".join(html))
        html_content = re.sub(r"[ \t]+", " ", " ".join(parser.parts)).strip()
        if not content:
            content = html_content
        elif html_content:
            normalized_plain = " ".join(content.casefold().split())
            normalized_html = " ".join(html_content.casefold().split())
            if normalized_html not in normalized_plain:
                content = f"{content}\n\n{html_content}"
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
        lead_extractor: Callable[..., GmailLeadExtraction] | None = None,
        ai_fallback_reason: str = "verified_workspace_openai_unavailable",
        internal_domains: set[str] | None = None,
        internal_emails: set[str] | None = None,
    ) -> None:
        self._connection_id = connection_id
        self.mailbox_email = mailbox_email.strip().lower()
        self.internal_domains = {
            domain.strip().lower()
            for domain in (internal_domains or set())
            if domain.strip()
        }
        self.internal_domains.add(self.mailbox_email.rsplit("@", 1)[-1])
        self.internal_emails = {
            email.strip().lower()
            for email in (internal_emails or set())
            if "@" in email
        }
        self.internal_emails.add(self.mailbox_email)
        self.api = GmailTransport(access_token, timeout_seconds=timeout_seconds, client=client)
        self.lead_extractor = lead_extractor
        self.ai_fallback_reason = ai_fallback_reason

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
        review_recent = cursor.get("review_recent") is True
        if history_id and not reconcile and not initial and not review_recent:
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
        if history_id and not reconcile and not initial and not review_recent:
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
                    "newer_than:30d -in:spam -in:trash"
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
                "review_recent": review_recent,
            }
            if next_token
            else None
        )
        return InboundCrmPage(
            records=records,
            next_cursor=next_cursor,
            checkpoint={"history_id": newest} if newest else None,
        )

    def list_contact_threads(self, email: str, *, max_threads: int = 200) -> list[str]:
        """Find bounded Gmail threads that include one exact lead email address."""
        target = email.strip().lower()
        if not target or "@" not in target:
            return []
        thread_ids: dict[str, None] = {}
        page_token: str | None = None
        while len(thread_ids) < max_threads:
            params: dict[str, object] = {
                "q": f"{{from:{target} to:{target}}} -in:spam -in:trash",
                "maxResults": 100,
            }
            if page_token:
                params["pageToken"] = page_token
            result = self.api.get("messages", **params)
            messages = result.get("messages", [])
            for message in messages if isinstance(messages, list) else []:
                if isinstance(message, dict) and message.get("threadId"):
                    thread_ids[str(message["threadId"])] = None
                    if len(thread_ids) >= max_threads:
                        break
            page_token = result.get("nextPageToken")
            if not page_token or not messages:
                break
        return list(thread_ids)

    def fetch_thread(self, thread_id: str, *, lead_email: str) -> list[dict[str, str]]:
        """Read messages between this mailbox and a lead from a known Gmail thread."""
        target = lead_email.strip().lower()
        result = self.api.get(
            f"threads/{thread_id}",
            format="full",
            fields="id,messages(id,threadId,internalDate,snippet,payload(mimeType,headers,body(data),parts(mimeType,filename,body(data),parts)))",
        )
        rows: list[dict[str, str]] = []
        messages = result.get("messages", [])
        for message in messages if isinstance(messages, list) else []:
            if not isinstance(message, dict):
                continue
            payload = message.get("payload", {})
            headers = {
                str(item.get("name", "")).lower(): str(item.get("value", ""))
                for item in payload.get("headers", [])
                if isinstance(item, dict)
            }
            sender_name, sender = parseaddr(headers.get("from", ""))
            to_recipients = [
                address.strip().lower()
                for _name, address in getaddresses([headers.get("to", "")])
                if address
            ]
            recipients = [
                address.strip().lower()
                for _name, address in getaddresses(
                    [headers.get("to", ""), headers.get("cc", "")]
                )
                if address
            ]
            sender = sender.strip().lower()
            if sender != target and target not in to_recipients:
                continue
            try:
                timestamp = datetime.fromtimestamp(
                    int(message.get("internalDate", "0")) / 1000, tz=UTC
                ).isoformat()
            except (TypeError, ValueError, OSError):
                timestamp = ""
            body = parse_mime_body(payload if isinstance(payload, dict) else {})
            rows.append(
                {
                    "message_id": str(message.get("id", "")),
                    "thread_id": str(message.get("threadId", thread_id)),
                    "sender": sender,
                    "sender_name": sender_name[:255],
                    "recipients": ", ".join(recipients),
                    "subject": headers.get("subject", "(no subject)")[:500],
                    "sent_at": timestamp,
                    "rfc_message_id": headers.get("message-id", ""),
                    "direction": "outbound" if sender == self.mailbox_email else "inbound",
                    "body": (body or str(message.get("snippet", "")))[:8000],
                }
            )
        rows.sort(key=lambda item: item["sent_at"])
        return rows

    def fetch_record(self, *, object_type: str, record_id: str) -> ExternalCrmRecord:
        del object_type
        data = self.api.get(
            f"messages/{record_id}",
            format="metadata",
            metadataHeaders=[
                "From", "Reply-To", "To", "Cc", "Bcc", "Subject", "Date",
                "Auto-Submitted", "Precedence", "List-Id", "List-Unsubscribe",
                "In-Reply-To", "Message-ID",
            ],
            fields="id,threadId,labelIds,internalDate,snippet,payload(headers)",
        )
        metadata_headers = {
            str(item.get("name", "")).lower(): str(item.get("value", ""))
            for item in data.get("payload", {}).get("headers", [])
            if isinstance(item, dict)
        }
        thread_id = str(data.get("threadId", ""))
        if set(data.get("labelIds", [])).intersection({"SPAM", "TRASH", "DRAFT"}):
            return ExternalCrmRecord(
                provider="gmail",
                object_type="message",
                record_id=record_id,
                values={"skip_reason": "system_or_own_message", "thread_id": thread_id},
            )
        thread = self.api.get(
            f"threads/{thread_id}",
            format="full",
        ) if thread_id else {}
        messages = thread.get("messages", [])
        thread_messages: list[dict[str, Any]] = []
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, dict):
                    continue
                payload = message.get("payload", {})
                if not isinstance(payload, dict):
                    continue
                headers = {
                    str(item.get("name", "")).lower(): str(item.get("value", ""))
                    for item in payload.get("headers", [])
                    if isinstance(item, dict)
                }
                body = parse_mime_body(payload) or str(message.get("snippet", ""))
                thread_messages.append(
                    {
                        "message_id": str(message.get("id", "")),
                        "headers": headers,
                        "body": body[:_MAX_BODY_CHARS],
                        "internal_date": str(message.get("internalDate", "")),
                    }
                )
        anchor = next(
            (message for message in thread_messages if message["message_id"] == record_id),
            None,
        )
        if anchor is None:
            full = self.api.get(
                f"messages/{record_id}",
                format="full",
            )
            payload = full.get("payload", {})
            full_headers = {
                str(item.get("name", "")).lower(): str(item.get("value", ""))
                for item in payload.get("headers", [])
                if isinstance(item, dict)
            } if isinstance(payload, dict) else {}
            anchor = {
                "message_id": record_id,
                "headers": full_headers or metadata_headers,
                "body": (
                    parse_mime_body(payload)
                    if isinstance(payload, dict)
                    else ""
                ) or str(data.get("snippet", "")),
                "internal_date": str(full.get("internalDate", data.get("internalDate", ""))),
            }
            thread_messages.append(anchor)
        headers = {**metadata_headers, **anchor["headers"]}
        body = str(anchor["body"])
        outreach = _internal_outreach_recipients(
            headers, thread_messages, self.internal_domains, self.internal_emails
        )
        if "SENT" in data.get("labelIds", []) and not (
            outreach and _forwarded_header_blocks(body)
        ):
            return ExternalCrmRecord(
                provider="gmail", object_type="message", record_id=record_id,
                values={"skip_reason": "system_or_own_message", "thread_id": thread_id},
            )
        anchor_reason = candidate_reason(headers, body, self.mailbox_email)
        if (
            anchor_reason
            and anchor_reason != "no_inquiry_signal"
            and not _is_lead_notification_wrapper(headers, body)
            and not outreach
        ):
            return ExternalCrmRecord(
                provider="gmail",
                object_type="message",
                record_id=record_id,
                values={"skip_reason": anchor_reason, "thread_id": thread_id},
            )
        context_parts: list[str] = []
        def message_order(message: dict[str, Any]) -> int:
            try:
                return int(message["internal_date"] or "0")
            except (TypeError, ValueError):
                return 0

        for message in sorted(thread_messages, key=message_order):
            message_headers = message["headers"]
            header_lines = [
                f"{name.title()}: {value}"
                for name, value in message_headers.items()
                if name.lower() in {
                    "from",
                    "to",
                    "cc",
                    "bcc",
                    "date",
                    "subject",
                    "reply-to",
                    "message-id",
                    "in-reply-to",
                    "references",
                }
                and value
            ]
            context_parts.append(
                f"--- Gmail message {message['message_id']} ---\n"
                + "\n".join(header_lines)
                + "\n\n"
                + str(message["body"])
            )
        thread_context = "\n\n".join(context_parts)[: _MAX_BODY_CHARS * 3]
        return ExternalCrmRecord(
            provider="gmail",
            object_type="message",
            record_id=record_id,
            values={
                "headers": headers,
                "thread_id": thread_id,
                "body": body,
                "thread_context": thread_context,
                "thread_messages": thread_messages,
                "snippet": str(data.get("snippet", ""))[:1000],
                "deterministic_candidate_reason": anchor_reason,
            },
            modified_at=datetime.fromtimestamp(
                int(anchor["internal_date"] or data.get("internalDate", "0")) / 1000, tz=UTC
            ),
        )

    def normalize_record(self, record: ExternalCrmRecord) -> CanonicalInboundLead:
        values = record.values
        if values.get("skip_reason"):
            raise ValueError(str(values["skip_reason"]))
        if not values.get("headers"):
            try:
                fetched = self.fetch_record(object_type="message", record_id=record.record_id)
                values.update(fetched.values)
                record = fetched.model_copy(update={"values": values})
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
        wrapper_sender_name = sender_name.strip()
        original_sender_email = sender_email.strip().lower()
        email = original_sender_email
        body = str(values.get("body", ""))[:_MAX_BODY_CHARS]
        if not email or not body:
            raise ValueError("Gmail message does not contain an importable sender and body")
        subject = str(headers.get("subject", "(no subject)"))[:500]
        thread_context = str(values.get("thread_context") or body)[: _MAX_BODY_CHARS * 3]
        notification_wrapper = _is_lead_notification_wrapper(headers, thread_context)
        thread_messages = values.get("thread_messages", [])
        forwarded_options: list[tuple[str, str, str, str]] = []
        if isinstance(thread_messages, list):
            for message in thread_messages:
                if isinstance(message, dict):
                    parsed_forward = _forwarded_lead(str(message.get("body", "")))
                    if parsed_forward is not None:
                        forwarded_options.append(parsed_forward)
        participant_messages = (
            [message for message in thread_messages if isinstance(message, dict)]
            if isinstance(thread_messages, list)
            else []
        )
        if not any(item.get("message_id") == record.record_id for item in participant_messages):
            participant_messages.append({"headers": headers, "body": body})
        eligible_participants = _thread_from_participants(
            headers,
            participant_messages,
            self.internal_domains,
            self.internal_emails,
        )
        outreach_recipients = _internal_outreach_recipients(
            headers,
            participant_messages,
            self.internal_domains,
            self.internal_emails,
        )
        known_participant_emails = {address for _, address in eligible_participants}
        eligible_participants.extend(
            (name, address)
            for name, address in outreach_recipients
            if address not in known_participant_emails
        )
        internal_participant_names: set[str] = {"ingenuity", "ingenuity systems"}
        signature_contacts: list[dict[str, str]] = []
        for message in participant_messages:
            message_headers = message.get("headers", {})
            if not isinstance(message_headers, dict):
                continue
            author_headers = [message_headers] + [
                block["headers"]
                for block in _forwarded_header_blocks(str(message.get("body", "")))
            ]
            for author_header in author_headers:
                participant_name, participant_email = parseaddr(
                    str(author_header.get("from", ""))
                )
                if _is_internal_address(
                    participant_email, self.internal_domains, self.internal_emails
                ) and participant_name.strip():
                    internal_participant_names.add(participant_name.strip().casefold())
            for contact in _signature_contacts(str(message.get("body", ""))):
                signature_contacts.append(
                    {
                        **contact,
                        "message_id": str(message.get("message_id") or record.record_id),
                    }
                )
        header_name, header_email = parseaddr(str(headers.get("from", "")))
        header_email = header_email.strip().lower()
        if _is_internal_address(header_email, self.internal_domains, self.internal_emails):
            if header_name.strip():
                internal_participant_names.add(header_name.strip().casefold())
        if not eligible_participants and self.lead_extractor is None:
            raise ValueError("no_external_thread_participant")
        eligible_by_email = {address: name for name, address in eligible_participants}
        thread_reply_to = "\n".join(
            [
                str(message.get("headers", {}).get("reply-to", ""))
                for message in participant_messages
                if isinstance(message.get("headers"), dict)
                and message.get("headers", {}).get("reply-to")
            ]
            + [
                str(block["headers"].get("reply-to", ""))
                for message in participant_messages
                for block in _forwarded_header_blocks(str(message.get("body", "")))
                if block["headers"].get("reply-to")
            ]
        )
        forwarded = _forwarded_lead(body)
        if forwarded is None:
            unique_forwarded = {item[1].strip().lower(): item for item in forwarded_options}
            if len(unique_forwarded) == 1:
                forwarded = next(iter(unique_forwarded.values()))
        if forwarded is not None and forwarded[1].strip().lower() not in eligible_by_email:
            forwarded = None
        selected_identity = (
            next(
                (item for item in eligible_participants if item[1] == forwarded[1].lower()),
                None,
            )
            if forwarded
            else next(
                (item for item in eligible_participants if item[1] == original_sender_email),
                eligible_participants[0] if len(eligible_participants) == 1 else None,
            )
        )
        if selected_identity is not None:
            sender_name, email = selected_identity
            if email != original_sender_email and forwarded is None:
                forwarded = (
                    sender_name,
                    email,
                    subject,
                    thread_context,
                )
        display = sender_name.strip() or email.split("@", 1)[0]
        local_domain = email.split("@")[-1]
        company = (
            re.sub(r"\.(com|net|org|co|io|ai|biz|uk|us)$", "", local_domain, flags=re.I)
            .replace(".", " ")
            .title()
        )
        if forwarded is not None:
            sender_name, email, forwarded_subject, _forwarded_body = forwarded
            subject = forwarded_subject or subject
            email = email.strip().lower()
            display = sender_name.strip() or email.split("@", 1)[0]
            local_domain = email.split("@")[-1]
            company = (
                re.sub(r"\.(com|net|org|co|io|ai|biz|uk|us)$", "", local_domain, flags=re.I)
                .replace(".", " ")
                .title()
            )
        authored_body = thread_context
        if self.lead_extractor is None and selected_identity is None:
            raise ValueError("external_thread_participant_ambiguous")
        extracted: GmailLeadExtraction | None = None
        evidence_refs: list[dict[str, str]] = []
        selection_reason = ""
        fallback_reason = self.ai_fallback_reason
        ai_reason = ""
        if self.lead_extractor is not None:
            try:
                extraction_headers = dict(headers)
                extraction_headers["internal-domains"] = ",".join(sorted(self.internal_domains))
                extraction_headers["internal-emails"] = ",".join(sorted(self.internal_emails))
                extraction_headers["internal-company-names"] = "Ingenuity,Ingenuity Systems"
                if values.get("gmail_validation_feedback"):
                    extraction_headers["validation-feedback"] = str(
                        values["gmail_validation_feedback"]
                    )[:2000]
                candidate = self.lead_extractor(
                    headers=extraction_headers, subject=subject, body=thread_context
                )
                if not candidate.is_genuine_inquiry:
                    record.values.update(
                        gmail_ai_method="openai",
                        gmail_ai_fallback_reason="",
                        gmail_ai_reason="not_a_genuine_inquiry",
                    )
                    raise ValueError("ai_classified_not_a_genuine_inquiry")
                from_evidence = []
                reply_to_evidence = []
                for thread_message in participant_messages:
                    message_headers = thread_message.get("headers", {})
                    if isinstance(message_headers, dict):
                        if message_headers.get("from"):
                            from_evidence.append(str(message_headers["from"]))
                        if message_headers.get("reply-to"):
                            reply_to_evidence.append(str(message_headers["reply-to"]))
                    from_evidence.extend(
                        line.strip()
                        for line in str(thread_message.get("body", "")).splitlines()
                        if re.match(r"(?i)^\s*>*\s*From:", line)
                    )
                evidence_sources = {
                    "subject": "\n".join([subject] + [
                        line for line in thread_context.splitlines()
                        if re.match(r"(?i)^\s*(?:>\s*)*Subject:", line)
                    ]),
                    "body": authored_body,
                    "from_header": "\n".join(from_evidence),
                    "reply_to": "\n".join(reply_to_evidence),
                }
                if candidate.candidates:
                    # Each child has its own evidence and inquiry. Redundant legacy
                    # fields must not veto independently valid candidates.
                    candidate = candidate.model_copy(update={
                        "full_name": None, "email": None, "company_name": None,
                        "job_title": None, "requested_service": None, "phone": None,
                        "evidence": [],
                    })
                else:
                    candidate, dropped_fields = _verified_optional_fields(
                        candidate, evidence_sources
                    )
                    record.values["gmail_field_review_notes"] = [
                        f"Unverified {field.replace('_', ' ')} was left unknown."
                        for field in dropped_fields
                    ]
                if any(
                    not _evidence_is_from_source(
                        evidence_sources[item.source], item.excerpt
                    )
                    for item in candidate.evidence
                ):
                    raise ValueError("ai_evidence_not_in_source")
                inquiry = candidate.inquiry_evidence.strip()
                if not candidate.candidates and (
                    not inquiry
                    or not _evidence_is_from_source(authored_body, inquiry)
                ):
                    raise ValueError("ai_inquiry_signal_missing")
                for field_name, field_value in {
                    "full_name": candidate.full_name,
                    "email": candidate.email,
                    "company_name": candidate.company_name,
                    "job_title": candidate.job_title,
                    "requested_service": candidate.requested_service,
                    "phone": candidate.phone,
                }.items():
                    if field_value:
                        field_evidence = [
                            item for item in candidate.evidence if item.field == field_name
                        ]
                        if not field_evidence or not any(
                            _evidence_is_from_source(item.excerpt, field_value)
                            for item in field_evidence
                        ):
                            raise ValueError(f"ai_evidence_missing_or_mismatched:{field_name}")
                candidate_name = (candidate.full_name or "").strip()
                candidate_email = (candidate.email or "").strip().lower()
                if (
                    _is_notification_label(candidate_name)
                    or candidate_name.casefold() in internal_participant_names
                    or _is_internal_address(
                        candidate_email, self.internal_domains, self.internal_emails
                    )
                ) and not candidate.candidates:
                    raise ValueError("internal_notification_identity_only")
                if (
                    candidate.email
                    and candidate.email.strip().lower() not in eligible_by_email
                    and not candidate.candidates
                ):
                    raise ValueError("ai_email_not_a_thread_candidate")
                if candidate.email:
                    email = candidate.email.strip().lower()
                elif selected_identity is None and not candidate.candidates:
                    raise ValueError(
                        "internal_notification_identity_only"
                        if notification_wrapper
                        else "external_thread_participant_ambiguous"
                    )
                selected_identity = next(
                    (item for item in eligible_participants if item[1] == email),
                    selected_identity,
                )
                if selected_identity is not None:
                    sender_name = selected_identity[0]
                    display = sender_name.strip() or email.split("@", 1)[0]
                    if email != original_sender_email:
                        forwarded = next(
                            (item for item in forwarded_options if item[1].lower() == email),
                            (sender_name, email, subject, thread_context),
                        )
                        subject = forwarded[2] or subject
                        local_domain = email.split("@")[-1]
                        company = (
                            re.sub(
                                r"\.(com|net|org|co|io|ai|biz|uk|us)$",
                                "",
                                local_domain,
                                flags=re.I,
                            )
                            .replace(".", " ")
                            .title()
                        )
                evidence_refs = [
                    {
                        "field": item.field,
                        "source": item.source,
                        "excerpt": item.excerpt,
                        "message_id": _evidence_message_id(
                            item.source,
                            item.excerpt,
                            participant_messages,
                            record.record_id,
                        ),
                    }
                    for item in candidate.evidence
                ]
                extracted = candidate
                fallback_reason = ""
                ai_reason = "genuine_inquiry"
                selection_reason = candidate.reason
            except ValueError as error:
                if str(error) == "ai_classified_not_a_genuine_inquiry":
                    raise ValueError(str(error)) from None
                if str(error) == "ai_email_not_a_thread_candidate":
                    record.values.update(
                        gmail_ai_method="openai",
                        gmail_ai_fallback_reason="",
                        gmail_ai_reason="thread_candidate_not_supported",
                    )
                    raise ValueError("thread_candidate_not_a_prospect") from None
                safe_reason = str(error)
                if safe_reason not in {
                    "ai_evidence_not_in_source",
                    "ai_inquiry_signal_missing",
                    "ai_email_does_not_match_contact",
                    "ai_email_not_a_thread_candidate",
                } and not re.fullmatch(
                    r"ai_evidence_missing_or_mismatched:[a-z_]{1,64}", safe_reason
                ):
                    safe_reason = f"ai_provider_error:{error.__class__.__name__}"
                fallback_reason = safe_reason[:128]
                record.values.update(
                    gmail_ai_method="openai",
                    gmail_ai_fallback_reason=fallback_reason,
                    gmail_ai_reason="classification_failed",
                )
                raise ValueError(
                    f"ai_classification_failed:{fallback_reason[:96]}"
                ) from None
            except Exception as error:
                detail = str(error).casefold()
                if "configuration is invalid" in detail or "credentials are invalid" in detail:
                    fallback_reason = "ai_credentials_missing_or_invalid"
                elif "rate limit" in detail:
                    fallback_reason = "ai_rate_limited"
                elif "quota" in detail:
                    fallback_reason = "ai_project_quota_exhausted"
                elif (
                    "timed out" in detail
                    or error.__class__.__name__ == "ReasoningProviderTimeout"
                ):
                    fallback_reason = "ai_timeout"
                elif "temporarily unavailable" in detail:
                    fallback_reason = "ai_provider_unavailable"
                elif error.__class__.__name__ == "InvalidReasoningOutput":
                    fallback_reason = "ai_invalid_output"
                else:
                    fallback_reason = f"ai_provider_error:{error.__class__.__name__}"[:128]
                record.values.update(
                    gmail_ai_method="openai",
                    gmail_ai_fallback_reason=fallback_reason,
                    gmail_ai_reason="classification_failed",
                )
                raise ValueError(
                    f"ai_classification_failed:{error.__class__.__name__}"
                ) from None
        if (
            extracted is None
            and candidate_reason(headers, thread_context, self.mailbox_email)
            == "no_inquiry_signal"
        ):
            record.values.update(
                gmail_ai_method="deterministic",
                gmail_ai_fallback_reason=fallback_reason,
            )
            raise ValueError("no_inquiry_signal")
        record.values.update(
            gmail_ai_method="openai" if extracted else "deterministic",
            gmail_ai_fallback_reason=fallback_reason,
            gmail_ai_reason=ai_reason,
        )
        email = email.strip().lower()
        candidate_decision = (
            "separate_forwarded_lead"
            if forwarded is not None and email != original_sender_email
            else "sender_lead"
        )
        identity_name_is_email_local_part = bool(
            email in {address for _, address in outreach_recipients}
            and not sender_name.strip()
            and not (extracted and extracted.full_name)
        )
        payload = {
            "source": "email",
            "source_detail": "Gmail",
            "external_id": record.record_id,
            "full_name": (extracted.full_name if extracted else None) or display,
            "email": email,
            "job_title": extracted.job_title if extracted else None,
            "phone": extracted.phone if extracted else None,
            "company_name": (
                (extracted.company_name if extracted else None) or company or "Unknown company"
            ),
            "product_or_service": (
                (extracted.requested_service if extracted else None) or "Inbound inquiry"
            ),
            "message": (
                "Thread role: direct prospect selected from parsed external participant "
                f"headers.\n\n{thread_context}"
            ),
            "received_at": record.modified_at or datetime.now(UTC),
            "gmail_message_id": record.record_id,
            "gmail_thread_id": str(values.get("thread_id", "")),
            "gmail_mailbox": self.mailbox_email,
            "sender_display_name": sender_name[:255],
            "original_sender_email": original_sender_email,
            "forwarded_lead": candidate_decision == "separate_forwarded_lead",
            "gmail_thread_decision": candidate_decision,
            "gmail_candidate_identity_email": email,
            "gmail_candidate_role": "direct_prospect",
            "gmail_identity_name_is_email_local_part": identity_name_is_email_local_part,
            "gmail_selection_reason": selection_reason,
            "gmail_evidence": evidence_refs,
                "subject": subject,
            "gmail_source_message_ids": [
                str(item.get("message_id"))
                for item in participant_messages
                if item.get("message_id")
            ],
            "gmail_internal_participant_names": sorted(internal_participant_names),
            "gmail_signature_contacts": signature_contacts,
            "gmail_candidate_review_notes": values.get("gmail_field_review_notes", []),
            "gmail_notification_wrapper": notification_wrapper,
            "gmail_wrapper_sender_name": wrapper_sender_name[:255],
            "gmail_embedded_message_ids": sorted(
                set(
                    re.findall(
                        r"(?im)^\s*>*\s*Message-ID:\s*<?([^>\s]+)",
                        thread_context,
                    )
                )
            ),
            "gmail_eligible_participant_emails": list(eligible_by_email),
            "gmail_outreach_recipient_emails": [
                address for _, address in outreach_recipients
            ],
            "gmail_prospect_candidates": (
                [item.model_dump(mode="json") for item in extracted.candidates]
                if extracted
                else []
            ),
            "gmail_participant_roles": (
                [
                    {
                        "name": item.full_name,
                        "email": item.email,
                        "role": item.role,
                        "reason": item.reason,
                    }
                    for item in extracted.candidates
                ]
                if extracted
                else []
            ),
            "reply_to": thread_reply_to
            or parseaddr(str(headers.get("reply-to", "")))[1].strip().lower(),
        }
        fingerprint = hashlib.sha256(
            f"{record.record_id}\0{thread_context}\0{email}".encode()
        ).hexdigest()
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
                "forwarded_lead": candidate_decision == "separate_forwarded_lead",
                "original_sender_email": original_sender_email,
                "gmail_thread_decision": candidate_decision,
                "gmail_candidate_identity_email": email,
                "gmail_candidate_role": "direct_prospect",
                "gmail_identity_name_is_email_local_part": identity_name_is_email_local_part,
                "gmail_selection_reason": selection_reason[:500],
                "gmail_ai_method": "openai" if extracted else "deterministic",
                "gmail_ai_fallback_reason": fallback_reason,
                "gmail_ai_reason": ai_reason,
                "gmail_notification_wrapper": notification_wrapper,
                "gmail_wrapper_sender_name": wrapper_sender_name[:255],
                "gmail_source_subject": subject[:500],
                # Kept only on the in-memory canonical record for timeline import.
                # The provider-link metadata allowlist never persists message bodies.
                "gmail_thread_messages": values.get("thread_messages", []),
                "gmail_embedded_message_ids": sorted(
                    set(
                        re.findall(
                            r"(?im)^\s*>*\s*Message-ID:\s*<?([^>\s]+)",
                            thread_context,
                        )
                    )
                ),
            },
            enrichment_fingerprint=None,
        )

    def normalize_records(
        self, record: ExternalCrmRecord, *, _evidence_retry: bool = False,
    ) -> list[CanonicalInboundLead]:
        base = self.normalize_record(record)
        payload = base.normalized_payload
        candidates = payload.get("gmail_prospect_candidates")
        if not isinstance(candidates, list) or not candidates:
            outreach = payload.get("gmail_outreach_recipient_emails", [])
            if len(set(outreach)) > 1:
                raise ValueError("multiple_forwarded_outreach_candidates_require_manual_review")
            if outreach and (
                not payload.get("full_name")
                or payload.get("gmail_identity_name_is_email_local_part")
                or _is_internal_address(
                    str(payload.get("email") or ""),
                    self.internal_domains,
                    self.internal_emails,
                )
            ):
                raise ValueError("forwarded_outreach_identity_requires_manual_review")
            return [base]
        thread_id = str(payload.get("gmail_thread_id") or record.record_id)
        thread_text = str(payload.get("message") or "")
        eligible_emails = {
            str(value).strip().lower()
            for value in payload.get("gmail_eligible_participant_emails", [])
        }
        outreach_emails = {
            str(value).strip().lower()
            for value in payload.get("gmail_outreach_recipient_emails", [])
        }
        internal_names = {
            str(value).strip().casefold()
            for value in payload.get("gmail_internal_participant_names", [])
            if str(value).strip()
        }
        evidence_sources = {
            "body": thread_text,
            "subject": "\n".join([str(payload.get("subject") or "")] + [
                line for line in thread_text.splitlines()
                if re.match(r"(?i)^\s*(?:>\s*)*Subject:", line)
            ]),
            "from_header": "\n".join(
                line for line in thread_text.splitlines()
                if re.match(r"(?i)^\s*(?:>\s*)*From:", line)
            ),
            "reply_to": str(payload.get("reply_to") or ""),
        }
        normalized: list[CanonicalInboundLead] = []
        review_notes: list[str] = []
        rejection_notes: list[str] = []
        repairable_rejections = False
        internal_candidate_seen = False
        # Validate referred prospects before considering whether an outreach
        # recipient marked as a referrer also represents a direct opportunity.
        ordered_candidates = sorted(
            candidates,
            key=lambda item: not (
                isinstance(item, dict)
                and item.get("role") in {"direct_prospect", "referred_prospect"}
            ),
        )
        for raw in ordered_candidates:
            if (
                not isinstance(raw, dict)
                or not raw.get("is_genuine_inquiry")
            ):
                continue
            candidate = GmailProspectCandidate.model_validate(raw)
            candidate = _ground_candidate_identity(
                candidate, evidence_sources, eligible_emails, outreach_emails,
            )
            candidate, dropped_fields = _verified_optional_fields(candidate, evidence_sources)
            review_notes.extend(
                f"{candidate.full_name or 'Prospect'}: unverified "
                f"{field.replace('_', ' ')} was left unknown."
                for field in dropped_fields
            )
            name = (candidate.full_name or "").strip()
            email = (candidate.email or "").strip().lower()
            if candidate.role == "external_referrer" and any(
                _normalized_evidence(str(item.normalized_payload.get("gmail_referred_by") or ""))
                == _normalized_evidence(name)
                for item in normalized
            ):
                continue
            if (
                candidate.role not in {"direct_prospect", "referred_prospect"}
                and email not in outreach_emails
            ):
                continue
            if email in outreach_emails and candidate.role != "referred_prospect":
                candidate = candidate.model_copy(update={"role": "direct_prospect"})
            if (
                _is_notification_label(name)
                or name.casefold() in internal_names
                or _is_internal_address(email, self.internal_domains, self.internal_emails)
            ):
                internal_candidate_seen = True
                continue
            if candidate.confidence < 0.6:
                review_notes.append(
                    f"{candidate.full_name or 'Unknown person'} ({candidate.role}) "
                    "requires manual review because identity confidence is low."
                )
                rejection_notes.append(
                    f"{candidate.full_name or 'Unknown person'} ({candidate.role}) "
                    "identity_confidence_low"
                )
                continue
            if (
                not candidate.inquiry_evidence
                or not _evidence_is_from_source(thread_text, candidate.inquiry_evidence)
            ):
                rejection_notes.append(f"{name or 'Prospect'}: inquiry_evidence_not_in_source")
                repairable_rejections = True
                continue
            evidence = [
                {
                    **item.model_dump(mode="json"),
                    "message_id": str(
                        _context_message_id(
                            thread_text,
                            item.excerpt,
                            str(base.provider_metadata.get("gmail_message_id") or record.record_id),
                        )
                    ),
                    "thread_id": thread_id,
                }
                for item in candidate.evidence
            ]
            invalid_sources = sorted({
                item.field for item in candidate.evidence
                if not _field_evidence_matches(
                    item.field, evidence_sources[item.source], item.excerpt
                )
            })
            if invalid_sources:
                rejection_notes.append(
                    f"{name or 'Prospect'}: evidence_not_in_source:{','.join(invalid_sources)}"
                )
                repairable_rejections = True
                continue
            fields = {
                "full_name": candidate.full_name,
                "email": candidate.email,
                "company_name": candidate.company_name,
                "job_title": candidate.job_title,
                "phone": candidate.phone,
                "requested_service": candidate.requested_service,
                "referred_by": candidate.referred_by,
                "referral_context": candidate.referral_context,
            }
            unsupported_fields = [
                field for field, value in fields.items() if value
                and not any(
                    item["field"] == field
                    and _field_evidence_matches(
                        field, evidence_sources[item["source"]], item["excerpt"], value
                    )
                    for item in evidence
                )
            ]
            if unsupported_fields:
                rejection_notes.append(
                    f"{name or 'Prospect'}: field_evidence_missing_or_mismatched:"
                    f"{','.join(unsupported_fields)}"
                )
                repairable_rejections = True
                continue
            if email:
                email_evidence = [
                    item for item in candidate.evidence if item.field == "email"
                ]
                body_identity_supported = any(
                    item.source == "body"
                    and _email_appears_as_lead_identity(item.excerpt, email)
                    and (
                        email in eligible_emails
                        or bool(payload.get("gmail_notification_wrapper"))
                        or (
                            candidate.role == "referred_prospect"
                            and bool(candidate.referred_by)
                        )
                    )
                    or (
                        item.source == "body"
                        and email in outreach_emails
                        and _email_appears_in_to_header(item.excerpt, email)
                    )
                    for item in email_evidence
                )
                if not email_evidence or not any(
                    item.source in {"from_header", "reply_to"}
                    or (item.source == "body" and body_identity_supported)
                    for item in email_evidence
                ):
                    rejection_notes.append(f"{name or 'Prospect'}: email_identity_not_supported")
                    continue
                if email not in eligible_emails and not body_identity_supported:
                    rejection_notes.append(f"{name or 'Prospect'}: email_not_a_thread_participant")
                    continue
            has_referral_support = bool(
                candidate.referred_by or candidate.referral_context or candidate.company_name
            )
            if not email and (not name or not candidate.phone or not has_referral_support):
                rejection_notes.append(
                    f"{name or 'Prospect'}: name_or_supported_contact_missing"
                )
                repairable_rejections = True
                continue
            if not name:
                review_notes.append(
                    "Name not provided; retained the verified external email."
                )
            identity = email or "|".join(
                (
                    name.casefold(),
                    (candidate.phone or "").strip(),
                    (candidate.company_name or "").casefold(),
                )
            )
            candidate_key = hashlib.sha256(f"{thread_id}\0{identity}".encode()).hexdigest()[:24]
            provider_record_id = f"{thread_id}:{candidate_key}"
            role_label = candidate.role.replace("_", " ")
            referral = f" Referred by {candidate.referred_by}." if candidate.referred_by else ""
            context = (
                f" Referral context: {candidate.referral_context}."
                if candidate.referral_context
                else ""
            )
            summary = f"Thread role: {role_label}.{referral}{context}"
            candidate_payload = {
                **payload,
                "external_id": provider_record_id,
                "full_name": name or "Name not provided",
                "email": email,
                "phone": candidate.phone or "",
                "company_name": candidate.company_name or "Unknown",
                "job_title": candidate.job_title,
                "product_or_service": candidate.requested_service or "Unknown",
                "message": f"{summary}\n\nOriginal thread:\n{thread_text}",
                "gmail_candidate_role": candidate.role,
                "gmail_referred_by": candidate.referred_by or "",
                "gmail_referral_context": candidate.referral_context or "",
                "gmail_unknown_identity_fields": [
                    field_name
                    for field_name, value in {
                        "full_name": name,
                        "email": email,
                        "company_name": candidate.company_name,
                        "job_title": candidate.job_title,
                        "phone": candidate.phone,
                    }.items()
                    if not value
                ],
                "gmail_selection_reason": candidate.reason,
                "gmail_evidence": evidence,
                "gmail_prospect_candidates": [],
                "gmail_participant_roles": payload.get("gmail_participant_roles", []),
                "gmail_candidate_review_notes": review_notes,
            }
            fingerprint = hashlib.sha256(
                (
                    f"{provider_record_id}\0{thread_text}\0"
                    f"{json.dumps(candidate_payload, sort_keys=True, default=str)}"
                ).encode()
            ).hexdigest()
            metadata = {
                **base.provider_metadata,
                "gmail_candidate_role": candidate.role,
                "gmail_referred_by": candidate.referred_by or "",
                "gmail_selection_reason": candidate.reason[:500],
                "gmail_candidate_identity_email": email,
                "gmail_thread_messages": base.provider_metadata.get(
                    "gmail_thread_messages", []
                ),
            }
            normalized.append(
                base.model_copy(
                    update={
                        "provider_record_id": provider_record_id,
                        "normalized_payload": candidate_payload,
                        "source_fingerprint": fingerprint,
                        "provider_metadata": metadata,
                    }
                )
            )
        if not normalized:
            if repairable_rejections and self.lead_extractor and not _evidence_retry:
                # Retry only the structured extraction once, reusing acquired
                # source text. No extra Gmail reads and no relaxed evidence checks.
                retry_values = {
                    **record.values,
                    "gmail_validation_feedback": "; ".join(rejection_notes)[:2000],
                }
                return self.normalize_records(
                    record.model_copy(update={"values": retry_values}), _evidence_retry=True
                )
            if internal_candidate_seen:
                raise ValueError("internal_notification_identity_only")
            if payload.get("gmail_notification_wrapper") and not any(
                isinstance(item, dict)
                and item.get("role") in {"direct_prospect", "referred_prospect"}
                for item in candidates
            ):
                raise ValueError("internal_notification_identity_only")
            details = "; ".join(rejection_notes)[:1200]
            raise ValueError(
                "no_supported_prospect_candidates" + (f":{details}" if details else "")
            )
        return normalized


class GmailOutboundProvider:
    """Gmail send boundary for confirmations, follow-ups, and meeting options."""

    provider_name = "gmail"

    def __init__(
        self,
        connection_id: UUID,
        access_token: str,
        mailbox_email: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.connection_id = connection_id
        self.mailbox_email = mailbox_email.strip().lower()
        self.api = GmailTransport(access_token, client=client)

    def send_confirmation(
        self,
        *,
        recipient: str,
        lead_name: str,
        company_name: str,
        product_or_service: str,
    ) -> str | None:
        message = EmailMessage()
        message["From"] = self.mailbox_email
        message["To"] = recipient
        message["Subject"] = "We received your inquiry"
        first_name = (lead_name.strip().split() or ["there"])[0]
        message.set_content(
            f"Hi {first_name},\n\n"
            f"Thanks for contacting us about {product_or_service or 'your inquiry'}. "
            f"We have received your request for {company_name or 'your team'} and "
            "our team will review it shortly.\n\n"
            "We will follow up with the next step soon.\n\n"
            "Best,\nThe team"
        )
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
        payload = self.api.post("messages/send", {"raw": raw})
        message_id = payload.get("id")
        return str(message_id) if message_id else None

    def send_email(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
    ) -> str | None:
        message = EmailMessage()
        message["From"] = self.mailbox_email
        message["To"] = recipient
        message["Subject"] = subject
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
        request: dict[str, str] = {"raw": raw}
        if thread_id:
            request["threadId"] = thread_id
        payload = self.api.post("messages/send", request)
        message_id = payload.get("id")
        return str(message_id) if message_id else None
