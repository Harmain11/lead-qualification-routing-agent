from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import lead_agent.application.services as application_services
from lead_agent.api.dependencies import get_workspace_context
from lead_agent.api.main import app
from lead_agent.application.crm_inbound import configure_inbound_sync, run_inbound_sync
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.crm_inbound import (
    CanonicalInboundLead,
    ExternalCrmRecord,
    InboundCrmPage,
    InboundCursor,
    InboundSyncMode,
)
from lead_agent.domain.gmail import (
    GmailEvidence,
    GmailLeadExtraction,
    GmailProspectCandidate,
)
from lead_agent.domain.normalization import normalize_lead
from lead_agent.domain.operations import WorkspaceBusinessProfile
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.domain.routing import NotificationDelivery, NotificationMessage
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    CrmInboundSyncRunORM,
    CrmProviderRecordLinkORM,
    LeadORM,
    ProviderConnectionORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.gmail import (
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    GmailConnectionValidator,
    GmailInboundProvider,
    GmailOutboundProvider,
    _gmail_http_error,
    candidate_reason,
    parse_mime_body,
)
from lead_agent.infrastructure.observability import current_request_id
from lead_agent.infrastructure.repositories import ensure_workspace


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_gmail_validator_requires_readonly_scope_and_real_profile_response() -> None:
    validator = GmailConnectionValidator()
    missing = validator.validate("gmail", {"access_token": "secret"}, [])
    assert not missing.verified
    assert missing.status == "insufficient_permissions"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/profile")
        assert request.headers["Authorization"] == "Bearer test-access-token"
        return httpx.Response(200, json={"emailAddress": "reader@example.test", "historyId": "901"})

    result = GmailConnectionValidator(client=_client(handler)).validate(
        "gmail",
        {"access_token": "test-access-token"},
        [GMAIL_READONLY_SCOPE],
    )
    assert result.verified
    assert result.metadata == {"gmail_email": "reader@example.test", "history_id": "901"}


def test_gmail_validator_classifies_api_disabled_scope_and_transport() -> None:
    def disabled(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "message": "Gmail API is disabled",
                    "errors": [{"reason": "accessNotConfigured"}],
                }
            },
        )

    disabled_result = GmailConnectionValidator(client=_client(disabled)).validate(
        "gmail", {"access_token": "token"}, [GMAIL_READONLY_SCOPE]
    )
    assert disabled_result.status == "configuration_error"
    assert disabled_result.error_code == "gmail_api_disabled"

    def insufficient(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "message": "insufficient authentication scopes",
                    "errors": [{"reason": "insufficientPermissions"}],
                }
            },
        )

    scope_result = GmailConnectionValidator(client=_client(insufficient)).validate(
        "gmail", {"access_token": "token"}, [GMAIL_READONLY_SCOPE]
    )
    assert scope_result.status == "insufficient_permissions"


def test_gmail_403_errors_distinguish_mailbox_read_from_send_permission() -> None:
    insufficient_scope = httpx.Response(
        403,
        json={
            "error": {
                "message": "insufficient authentication scopes",
                "errors": [{"reason": "insufficientPermissions"}],
            }
        },
    )

    send_error = _gmail_http_error(insufficient_scope, path="messages/send")
    read_error = _gmail_http_error(insufficient_scope, path="threads/thread-1")

    assert GMAIL_SEND_SCOPE.endswith("/gmail.send")
    assert send_error.code == "gmail_insufficient_scope"
    assert "send email" in str(send_error).lower()
    assert read_error.code == "gmail_insufficient_scope"
    assert "read this mailbox" in str(read_error).lower()


def test_unknown_gmail_thread_403_is_a_skippable_thread_error() -> None:
    forbidden = httpx.Response(
        403,
        json={
            "error": {
                "message": "User cannot access this thread",
                "errors": [{"reason": "forbidden"}],
            }
        },
    )

    error = _gmail_http_error(forbidden, path="threads/thread-1")

    assert error.code == "gmail_thread_access_denied"
    assert error.retryable is False


def test_mime_parser_prefers_nested_plain_text_and_html_fallback() -> None:
    plain = parse_mime_body(
        {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/html", "body": {"data": _encoded("<p>HTML offer</p>")}},
                        {
                            "mimeType": "text/plain",
                            "body": {"data": _encoded("Please send pricing for our team")},
                        },
                    ],
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "proposal.pdf",
                    "body": {"attachmentId": "not-downloaded"},
                },
                {
                    "mimeType": "text/plain",
                    "filename": "project-details.txt",
                    "body": {"data": _encoded("Attachment: we need a project proposal")},
                },
            ],
        }
    )
    assert plain.startswith("Please send pricing for our team")
    assert "Attachment: we need a project proposal" in plain

    html = parse_mime_body(
        {
            "mimeType": "text/html",
            "body": {
                "data": _encoded(
                    "<style>secret()</style><p>Contact our team &amp; discuss a demo</p>"
                )
            },
        }
    )
    assert "Contact our team & discuss a demo" in html
    assert "secret" not in html

    combined = parse_mime_body(
        {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _encoded("Short note")}},
                {
                    "mimeType": "text/html",
                    "body": {
                        "data": _encoded("<p>Short note</p><p>Full pricing context</p>")
                    },
                },
            ],
        }
    )
    assert "Short note" in combined
    assert "Full pricing context" in combined


def test_mime_parser_bounds_body_size() -> None:
    body = parse_mime_body({"mimeType": "text/plain", "body": {"data": _encoded("x" * 100_000)}})
    assert len(body) <= 32_000


def test_candidate_filter_is_deterministic_and_excludes_own_and_automated_mail() -> None:
    assert (
        candidate_reason(
            {"from": "Owner <owner@client.test>", "subject": "Pricing for our project"},
            "Could we schedule a demo?",
            "intake@company.test",
        )
        is None
    )
    assert (
        candidate_reason(
            {"from": "Intake <intake@company.test>", "subject": "Pricing"},
            "Please call",
            "intake@company.test",
        )
        == "own_or_missing_sender"
    )
    assert (
        candidate_reason(
            {"from": "Updates <no-reply@provider.test>", "subject": "Pricing"},
            "Please call",
            "intake@company.test",
        )
        == "automated_sender"
    )
    assert (
        candidate_reason(
            {"from": "Buyer <buyer@client.test>", "subject": "Weekly newsletter"},
            "Pricing and demo",
            "intake@company.test",
        )
        == "bulk_or_transactional"
    )
    assert (
        candidate_reason(
            {"from": "Buyer <buyer@client.test>", "subject": "Hello"},
            "Just saying hi",
            "intake@company.test",
        )
        == "no_inquiry_signal"
    )


def test_gmail_initial_sync_is_bounded_to_recent_query_and_has_checkpoint() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path.endswith("/profile"):
            return httpx.Response(
                200, json={"historyId": "500", "emailAddress": "inbox@example.test"}
            )
        if request.url.params.get("q") == "older_than:30d -in:spam -in:trash":
            return httpx.Response(
                200,
                json={
                    "messages": [{"id": "old-m1", "threadId": "old-t1"}],
                    "nextPageToken": "older-page-2",
                },
            )
        return httpx.Response(200, json={"messages": [{"id": "m1", "threadId": "t1"}]})

    provider = GmailInboundProvider(
        uuid4(), "access", "inbox@example.test", client=_client(handler)
    )
    page = provider.list_records(object_type="message", cursor=None, limit=25)
    assert [item.record_id for item in page.records] == ["m1"]
    assert page.checkpoint == {"history_id": "500"}
    assert "newer_than%3A30d" in requests[-1]
    assert "maxResults=25" in requests[-1]
    review_page = provider.list_records(
        object_type="message",
        cursor={"history_id": "100", "initial": False, "review_recent": True},
        limit=25,
    )
    assert [item.record_id for item in review_page.records] == ["m1"]
    assert requests[-1].split("?", 1)[0].endswith("/messages")
    assert "newer_than%3A30d" in requests[-1]
    previous_page = provider.list_records(
        object_type="message",
        cursor={"initial": True, "review_previous": True},
        limit=25,
    )
    assert [item.record_id for item in previous_page.records] == ["old-m1"]
    assert "older_than%3A30d" in requests[-1]
    assert previous_page.next_cursor is not None
    assert previous_page.next_cursor["page_token"] == "older-page-2"
    assert previous_page.next_cursor["review_previous"] is True


def test_gmail_contact_thread_search_and_sync_includes_both_directions() -> None:
    def message(message_id: str, sender: str, recipient: str, sent_at: str, body: str) -> dict:
        return {
            "id": message_id,
            "threadId": "thread-1",
            "internalDate": sent_at,
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": sender},
                    {"name": "To", "value": recipient},
                    {"name": "Subject", "value": "Project follow-up"},
                    {"name": "Message-ID", "value": f"<{message_id}@example.test>"},
                ],
                "body": {"data": _encoded(body)},
            },
        }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                json={"messages": [{"id": "inbound-1", "threadId": "thread-1"}]},
            )
        if request.url.path.endswith("/threads/thread-1"):
            return httpx.Response(
                200,
                json={
                    "messages": [
                        message(
                            "outbound-1",
                            "rep@example.test",
                            "buyer@example.test",
                            "1700000000000",
                            "Sent the proposal.",
                        ),
                        message(
                            "inbound-1",
                            "buyer@example.test",
                            "rep@example.test",
                            "1700000001000",
                            "Please include pricing.",
                        ),
                        message(
                            "other-1",
                            "other@example.test",
                            "rep@example.test",
                            "1700000002000",
                            "Not part of this lead thread.",
                        ),
                    ]
                },
            )
        raise AssertionError(f"Unexpected Gmail request: {request.url}")

    provider = GmailInboundProvider(
        uuid4(), "access", "rep@example.test", client=_client(handler)
    )
    assert provider.list_contact_threads("buyer@example.test") == ["thread-1"]
    conversation = provider.fetch_thread("thread-1", lead_email="buyer@example.test")
    assert [item["message_id"] for item in conversation] == ["outbound-1", "inbound-1"]
    assert [item["direction"] for item in conversation] == ["outbound", "inbound"]
    assert conversation[0]["body"] == "Sent the proposal."
    assert conversation[1]["body"] == "Please include pricing."
    assert conversation[1]["rfc_message_id"] == "<inbound-1@example.test>"


def test_gmail_outbound_provider_replies_inside_existing_thread() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "outbound-2"})

    provider = GmailOutboundProvider(
        uuid4(), "access", "rep@example.test", client=_client(handler)
    )
    result = provider.send_email(
        recipient="buyer@example.test",
        subject="Re: Project follow-up",
        body="Reply with option 1.",
        thread_id="thread-1",
        in_reply_to="<inbound-1@example.test>",
    )

    assert result == "outbound-2"
    assert captured["threadId"] == "thread-1"
    raw = str(captured["raw"])
    decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
    assert "In-Reply-To: <inbound-1@example.test>" in decoded
    assert "References: <inbound-1@example.test>" in decoded


def test_gmail_shared_inbox_lists_messages_and_reads_full_thread() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                json={
                    "messages": [{"id": "message-1", "threadId": "thread-1"}],
                    "nextPageToken": "next",
                },
            )
        if request.url.path.endswith("/messages/message-1"):
            return httpx.Response(
                200,
                json={
                    "id": "message-1",
                    "threadId": "thread-1",
                    "labelIds": ["INBOX", "UNREAD"],
                    "internalDate": "1792494000000",
                    "snippet": "Could you send pricing?",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Pat Example <pat@example.test>"},
                            {"name": "To", "value": "team@example.test"},
                            {"name": "Subject", "value": "Pricing request"},
                        ]
                    },
                },
            )
        if request.url.path.endswith("/threads/thread-1"):
            return httpx.Response(
                200,
                json={
                    "messages": [{
                        "id": "message-1",
                        "threadId": "thread-1",
                        "internalDate": "1792494000000",
                        "payload": {
                            "mimeType": "text/plain",
                            "headers": [
                                {"name": "From", "value": "Pat Example <pat@example.test>"},
                                {"name": "To", "value": "team@example.test"},
                                {"name": "Subject", "value": "Pricing request"},
                                {"name": "Message-ID", "value": "<message-1@example.test>"},
                            ],
                            "body": {"data": _encoded("Could you send pricing?")},
                        },
                    }]
                },
            )
        raise AssertionError(f"Unexpected Gmail request: {request.url}")

    provider = GmailInboundProvider(
        uuid4(), "access", "team@example.test", client=_client(handler)
    )
    page = provider.list_mailbox_messages(
        folder="inbox", query="from:pat@example.test", page_token="previous", limit=75
    )
    assert page["next_page_token"] == "next"
    assert page["messages"][0]["sender"] == "pat@example.test"
    assert page["messages"][0]["unread"] is True
    assert page["messages"][0]["subject"] == "Pricing request"
    assert calls[0].url.params["q"] == "in:inbox from:pat@example.test"
    assert calls[0].url.params["maxResults"] == "50"
    assert calls[0].url.params["pageToken"] == "previous"

    thread = provider.fetch_mailbox_thread("thread-1")
    assert thread[0]["thread_id"] == "thread-1"
    assert thread[0]["direction"] == "inbound"
    assert thread[0]["body"] == "Could you send pricing?"
    assert thread[0]["rfc_message_id"] == "<message-1@example.test>"


def test_gmail_outbound_provider_returns_message_and_thread_ids() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "sent-1", "threadId": "thread-1"})

    provider = GmailOutboundProvider(
        uuid4(), "access", "team@example.test", client=_client(handler)
    )
    assert provider.send_email_with_metadata(
        recipient="pat@example.test",
        subject="Pricing",
        body="Here is the information.",
    ) == {"message_id": "sent-1", "thread_id": "thread-1"}


def test_gmail_special_folders_include_spam_and_trash_when_requested() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"messages": []})

    provider = GmailInboundProvider(
        uuid4(), "access", "team@example.test", client=_client(handler)
    )
    for folder in ("spam", "trash", "archive", "all"):
        provider.list_mailbox_messages(folder=folder)

    assert requests[0].url.params["q"] == "in:spam"
    assert requests[0].url.params["includeSpamTrash"].lower() == "true"
    assert requests[1].url.params["q"] == "in:trash"
    assert requests[1].url.params["includeSpamTrash"].lower() == "true"
    assert requests[2].url.params["q"] == "-in:inbox -in:sent -in:spam -in:trash -in:drafts"
    assert requests[3].url.params["q"] == "in:anywhere -in:spam -in:trash"


def test_gmail_outbound_provider_sends_cc_bcc_rich_text_and_attachments() -> None:
    import base64
    import json
    from email import message_from_bytes
    from email.policy import default

    sent_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        raw = body["raw"]
        raw += "=" * (-len(raw) % 4)
        sent_messages.append(
            message_from_bytes(base64.urlsafe_b64decode(raw), policy=default)
        )
        return httpx.Response(200, json={"id": "sent-rich", "threadId": "thread-rich"})

    provider = GmailOutboundProvider(
        uuid4(), "access", "team@example.test", client=_client(handler)
    )
    result = provider.send_email_with_metadata(
        recipient="pat@example.test",
        cc="team-copy@example.test",
        bcc="private-copy@example.test",
        subject="Formatted update",
        body="Hello",
        html_body="<p><strong>Hello</strong></p>",
        attachments=[("notes.txt", "text/plain", b"Call notes")],
    )

    assert result == {"message_id": "sent-rich", "thread_id": "thread-rich"}
    message = sent_messages[0]
    assert message["To"] == "pat@example.test"
    assert message["Cc"] == "team-copy@example.test"
    assert message["Bcc"] == "private-copy@example.test"
    assert message.get_content_type() == "multipart/mixed"
    html_part = next(part for part in message.walk() if part.get_content_type() == "text/html")
    assert "<strong>Hello</strong>" in html_part.get_content()
    attachment = next(message.iter_attachments())
    assert attachment.get_filename() == "notes.txt"
    assert attachment.get_payload(decode=True) == b"Call notes"


def test_gmail_incremental_history_and_expired_history_reconciliation() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/history"):
            if request.url.params.get("startHistoryId") == "expired":
                return httpx.Response(404, json={"error": {"message": "history not found"}})
            return httpx.Response(
                200,
                json={
                    "historyId": "700",
                    "history": [
                        {"messagesAdded": [{"message": {"id": "new", "threadId": "thread"}}]}
                    ],
                },
            )
        if request.url.path.endswith("/profile"):
            return httpx.Response(
                200, json={"historyId": "701", "emailAddress": "inbox@example.test"}
            )
        return httpx.Response(
            200, json={"messages": [{"id": "recent", "threadId": "recent-thread"}]}
        )

    provider = GmailInboundProvider(
        uuid4(), "access", "inbox@example.test", client=_client(handler)
    )
    incremental = provider.list_records(
        object_type="message", cursor={"history_id": "600"}, limit=25
    )
    assert [item.record_id for item in incremental.records] == ["new"]
    assert incremental.checkpoint == {"history_id": "700"}

    reconciled = provider.list_records(
        object_type="message", cursor={"history_id": "expired"}, limit=25
    )
    assert [item.record_id for item in reconciled.records] == ["recent"]
    assert reconciled.checkpoint == {"history_id": "701"}
    assert calls.count("/gmail/v1/users/me/history") == 2
    assert calls[-1].endswith("messages")


@pytest.mark.parametrize("missing_format", ["metadata", "full"])
def test_gmail_deleted_history_message_is_skipped(missing_format: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("format") == missing_format:
            return httpx.Response(404, json={"error": {"message": "not found"}})
        return httpx.Response(
            200,
            json={
                "payload": {"headers": [
                    {"name": "From", "value": "buyer@example.test"},
                    {"name": "Subject", "value": "Pricing for our project"},
                ]},
                "snippet": "Please call our team",
            },
        )

    provider = GmailInboundProvider(
        uuid4(), "access", "inbox@example.test", client=_client(handler)
    )
    record = ExternalCrmRecord(
        provider="gmail", object_type="message", record_id="deleted", values={}
    )
    with pytest.raises(ValueError, match="message_no_longer_available"):
        provider.normalize_record(record)


@pytest.mark.parametrize("status", [429, 503])
def test_gmail_transient_message_failure_is_not_skipped(status: int) -> None:
    provider = GmailInboundProvider(
        uuid4(), "access", "inbox@example.test",
        client=_client(lambda _request: httpx.Response(status, text="private provider body")),
    )
    record = ExternalCrmRecord(
        provider="gmail", object_type="message", record_id="retry", values={}
    )
    with pytest.raises(ProviderOperationError) as caught:
        provider.normalize_record(record)
    assert caught.value.code == "gmail_provider_unavailable"
    assert caught.value.retryable
    assert "private provider body" not in str(caught.value)


def test_gmail_retries_user_rate_limit_before_returning_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("lead_agent.infrastructure.gmail.time.sleep", lambda _delay: None)
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(
                403,
                headers={"Retry-After": "0"},
                json={
                    "error": {
                        "errors": [{"reason": "userRateLimitExceeded"}],
                        "message": "Rate Limit Exceeded",
                    }
                },
            )
        return httpx.Response(200, json={"messages": []})

    provider = GmailInboundProvider(
        uuid4(), "access", "inbox@example.test", client=_client(handler)
    )
    assert provider.api.get("messages") == {"messages": []}
    assert attempts == 3


def test_gmail_normalization_preserves_provider_and_message_thread_provenance() -> None:
    provider = GmailInboundProvider(uuid4(), "access", "inbox@example.test")
    record = ExternalCrmRecord(
        provider="gmail",
        object_type="message",
        record_id="message-1",
        values={
            "thread_id": "thread-9",
            "headers": {
                "from": "Mira Example <Mira@Client.example>",
                "reply-to": "sales@client.example",
                "subject": "Pricing request",
            },
            "body": "Please send pricing for our team.",
        },
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    canonical = provider.normalize_record(record)
    assert canonical.provider == "gmail"
    assert canonical.provider_record_id == "message-1"
    assert canonical.provider_metadata["gmail_thread_id"] == "thread-9"
    assert canonical.normalized_payload["source"] == "email"
    assert canonical.normalized_payload["gmail_message_id"] == "message-1"
    assert canonical.normalized_payload["reply_to"] == "sales@client.example"


def test_gmail_forwarded_lead_keeps_forwarded_contact_identity_and_provenance() -> None:
    provider = GmailInboundProvider(uuid4(), "access", "inbox@example.test")
    record = ExternalCrmRecord(
        provider="gmail",
        object_type="message",
        record_id="forward-1",
        values={
            "thread_id": "thread-shared",
            "headers": {
                "from": "Sales Rep <rep@agency.example>",
                "subject": "Fwd: lead",
            },
            "body": (
                "Please follow up.\n\n"
                "---------- Forwarded message ---------\n"
                "From: Mira Example <mira@client.example>\n"
                "Subject: Pricing request\n\n"
                "Could you send pricing for our team?"
            ),
        },
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    canonical = provider.normalize_record(record)
    assert canonical.normalized_payload["email"] == "mira@client.example"
    assert canonical.normalized_payload["full_name"] == "Mira Example"
    assert canonical.normalized_payload["company_name"] == "Client Example"
    assert canonical.provider_metadata["forwarded_lead"] is True
    assert canonical.provider_metadata["original_sender_email"] == "rep@agency.example"
    assert "From: Mira Example <mira@client.example>" in canonical.normalized_payload["message"]


def test_gmail_ai_recovers_candidate_without_deterministic_inquiry_signal() -> None:
    body = (
        "We are looking for event support for Acme Group. "
        "Call me at 1-202-555-0161."
    )
    headers = {
        "from": "Mira Example <mira@client.example>",
        "subject": "Question",
    }
    assert candidate_reason(headers, body, "inbox@example.test") == "no_inquiry_signal"
    extraction = GmailLeadExtraction(
        is_genuine_inquiry=True,
        reason="The sender is actively seeking a service.",
        inquiry_evidence="We are looking for event support",
        full_name="Mira Example",
        email="mira@client.example",
        company_name="Acme Group",
        phone="1-202-555-0161",
        requested_service="event support",
        evidence=[
            GmailEvidence(field="full_name", source="from_header", excerpt="Mira Example"),
            GmailEvidence(field="email", source="from_header", excerpt="mira@client.example"),
            GmailEvidence(field="company_name", source="body", excerpt="Acme Group"),
            GmailEvidence(field="phone", source="body", excerpt="1-202-555-0161"),
            GmailEvidence(field="requested_service", source="body", excerpt="event support"),
        ],
    )
    provider = GmailInboundProvider(
        uuid4(), "access", "inbox@example.test",
        lead_extractor=lambda **_kwargs: extraction,
    )
    canonical = provider.normalize_record(ExternalCrmRecord(
        provider="gmail",
        object_type="message",
        record_id="ai-positive",
        values={"thread_id": "thread-ai", "headers": headers, "body": body},
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
    ))
    assert canonical.normalized_payload["full_name"] == "Mira Example"
    assert canonical.normalized_payload["company_name"] == "Acme Group"
    assert canonical.normalized_payload["phone"] == "1-202-555-0161"
    assert canonical.normalized_payload["product_or_service"] == "event support"
    assert canonical.provider_metadata["gmail_ai_method"] == "openai"


def test_gmail_ai_can_accept_plausible_intent_without_keyword_gate() -> None:
    body = (
        "We are comparing firms for our rebrand. "
        "Could we discuss what working together might look like?"
    )
    headers = {"from": "Mira Example <mira@client.example>", "subject": "A question"}
    extraction = GmailLeadExtraction(
        is_genuine_inquiry=True,
        reason="The sender is exploring a possible business fit.",
        inquiry_evidence="Could we discuss what working together might look like?",
    )
    provider = GmailInboundProvider(
        uuid4(),
        "access",
        "inbox@example.test",
        lead_extractor=lambda **_kwargs: extraction,
    )
    canonical = provider.normalize_record(
        ExternalCrmRecord(
            provider="gmail",
            object_type="message",
            record_id="ai-plausible-intent",
            values={"thread_id": "thread-ai", "headers": headers, "body": body},
            modified_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    assert canonical.normalized_payload["email"] == "mira@client.example"
    assert canonical.provider_metadata["gmail_ai_method"] == "openai"


def test_gmail_ai_evidence_accepts_normalized_whitespace_and_typography() -> None:
    body = "We’re exploring event\noptions. Could we discuss this next week?"
    extraction = GmailLeadExtraction(
        is_genuine_inquiry=True,
        reason="The sender is exploring event services.",
        inquiry_evidence="Could we discuss this next week?",
        email="mira@client.example",
        requested_service="event options",
        evidence=[
            GmailEvidence(
                field="requested_service",
                source="body",
                excerpt="We're exploring event options",
            ),
            GmailEvidence(
                field="email",
                source="from_header",
                excerpt="mira@client.example",
            ),
        ],
    )
    provider = GmailInboundProvider(
        uuid4(),
        "access",
        "inbox@example.test",
        lead_extractor=lambda **_kwargs: extraction,
    )
    canonical = provider.normalize_record(
        ExternalCrmRecord(
            provider="gmail",
            object_type="message",
            record_id="ai-normalized-evidence",
            values={
                "thread_id": "thread-ai",
                "headers": {
                    "from": "Mira Example <mira@client.example>",
                    "subject": "Event services",
                },
                "body": body,
            },
            modified_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    assert canonical.normalized_payload["email"] == "mira@client.example"
    assert canonical.provider_metadata["gmail_ai_method"] == "openai"


def test_gmail_ai_negative_excludes_message_even_if_heuristic_candidate() -> None:
    provider = GmailInboundProvider(
        uuid4(), "access", "inbox@example.test",
        lead_extractor=lambda **_kwargs: GmailLeadExtraction(
            is_genuine_inquiry=False,
            reason="Routine vendor marketing email sent to our workspace.",
        ),
    )
    record = ExternalCrmRecord(
        provider="gmail",
        object_type="message",
        record_id="ai-negative",
        values={
            "headers": {"from": "Mira <mira@client.example>", "subject": "Pricing request"},
            "body": "Please send pricing for our project.",
        },
    )
    with pytest.raises(ValueError, match="ai_classified_not_a_genuine_inquiry"):
        provider.normalize_record(record)
    assert record.values["gmail_ai_method"] == "openai"


def test_gmail_business_profile_reaches_extractor_and_marketing_is_not_created_as_lead() -> None:
    profile = WorkspaceBusinessProfile(
        business_name="Corral Systems",
        alternate_names=["Corral"],
        website="https://corral.example",
        email_domains=["corral.example"],
        description="CRM and sales operations software.",
    )
    observed: dict[str, object] = {}

    def classify(**kwargs: object) -> GmailLeadExtraction:
        observed.update(kwargs)
        return GmailLeadExtraction(
            is_genuine_inquiry=False,
            reason="Routine vendor marketing email sent to our workspace.",
        )

    provider = GmailInboundProvider(
        uuid4(),
        "access",
        "inbox@corral.example",
        lead_extractor=classify,
        business_profile=profile,
    )
    record = ExternalCrmRecord(
        provider="gmail",
        object_type="message",
        record_id="routine-marketing",
        values={
            "headers": {
                "from": "Vendor Sales <sales@vendor.example>",
                "subject": "Pricing request",
            },
            "body": "We sell a marketing platform and would like to present a demo.",
        },
    )

    with pytest.raises(ValueError, match="ai_classified_not_a_genuine_inquiry"):
        provider.normalize_record(record)

    extractor_headers = observed["headers"]
    assert isinstance(extractor_headers, dict)
    assert json.loads(extractor_headers["workspace-business-profile"]) == (
        profile.model_dump(mode="json")
    )
    assert "corral.example" in extractor_headers["internal-domains"].split(",")
    assert {"Corral Systems", "Corral"}.issubset(
        set(extractor_headers["internal-company-names"].split(","))
    )
    assert record.values["gmail_ai_reason"] == "not_a_genuine_inquiry"


def test_gmail_openai_failure_skips_message_with_auditable_reason() -> None:
    def fail(**_kwargs):
        raise RuntimeError("provider unavailable")

    provider = GmailInboundProvider(
        uuid4(), "access", "inbox@example.test", lead_extractor=fail
    )
    record = ExternalCrmRecord(
        provider="gmail",
        object_type="message",
        record_id="ai-fallback",
        values={
            "headers": {
                "from": "Mira <mira@client.example>",
                "subject": "Pricing request",
            },
            "body": "Could you send pricing for our project?",
        },
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="ai_classification_failed:RuntimeError"):
        provider.normalize_record(record)
    assert record.values["gmail_ai_method"] == "openai"
    assert record.values["gmail_ai_reason"] == "classification_failed"
    assert record.values["gmail_ai_fallback_reason"] == "ai_provider_error:RuntimeError"


def test_gmail_ai_uses_forwarded_message_identity_and_content() -> None:
    body = (
        "Please follow up.\n\n---------- Forwarded message ---------\n"
        "From: Mira Example <mira@client.example>\n"
        "Subject: Question\n\nWe are looking for event support for Acme Group."
    )
    observed: list[str] = []

    def extract(*, headers, subject, body):
        observed.append(body)
        return GmailLeadExtraction(
            is_genuine_inquiry=True,
            reason="The forwarded sender requested a service.",
            inquiry_evidence="We are looking for event support",
            full_name="Mira Example",
            email="mira@client.example",
            company_name="Acme Group",
            requested_service="event support",
            evidence=[
                GmailEvidence(field="full_name", source="from_header", excerpt="Mira Example"),
                GmailEvidence(field="email", source="from_header", excerpt="mira@client.example"),
                GmailEvidence(field="company_name", source="body", excerpt="Acme Group"),
                GmailEvidence(field="requested_service", source="body", excerpt="event support"),
            ],
        )

    provider = GmailInboundProvider(
        uuid4(), "access", "inbox@example.test", lead_extractor=extract
    )
    canonical = provider.normalize_record(ExternalCrmRecord(
        provider="gmail",
        object_type="message",
        record_id="forwarded-ai",
        values={
            "thread_id": "thread-forwarded",
            "headers": {"from": "Rep <rep@agency.example>", "subject": "Fwd: lead"},
            "body": body,
        },
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
    ))
    assert len(observed) == 1
    assert "From: Mira Example <mira@client.example>" in observed[0]
    assert "We are looking for event support for Acme Group." in observed[0]
    assert canonical.normalized_payload["email"] == "mira@client.example"
    assert canonical.provider_metadata["forwarded_lead"] is True


def test_gmail_fetch_record_supplies_full_multi_message_thread_to_ai() -> None:
    body = (
        "Could we discuss a project this week?\n\n"
        "> We are comparing providers for our project.\n"
        "> Unique detail: our pilot starts in March."
    )
    thread_messages = [
        {
            "id": "older",
            "threadId": "thread-full",
            "internalDate": "1760000000000",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Mira <mira@client.example>"},
                    {"name": "To", "value": "inbox@example.test"},
                    {"name": "Date", "value": "Mon, 1 Oct 2026 10:00:00 +0000"},
                    {"name": "Subject", "value": "Project question"},
                ],
                "body": {"data": _encoded("We are comparing providers for our project.")},
            },
        },
        {
            "id": "newer",
            "threadId": "thread-full",
            "internalDate": "1760000100000",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Mira <mira@client.example>"},
                    {"name": "To", "value": "inbox@example.test"},
                    {"name": "Cc", "value": "ops@example.test"},
                    {"name": "Date", "value": "Mon, 1 Oct 2026 10:01:00 +0000"},
                    {"name": "Subject", "value": "Re: Project question"},
                ],
                "body": {"data": _encoded(body)},
            },
        },
    ]

    class FakeApi:
        def get(self, path: str, **_params):
            if path == "messages/newer":
                return {
                    "id": "newer",
                    "threadId": "thread-full",
                    "labelIds": ["INBOX"],
                    "internalDate": "1760000100000",
                    "snippet": body,
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Mira <mira@client.example>"},
                            {"name": "Subject", "value": "Re: Project question"},
                        ]
                    },
                }
            if path == "threads/thread-full":
                return {"id": "thread-full", "messages": thread_messages}
            raise AssertionError(f"Unexpected Gmail API path: {path}")

    received: list[str] = []

    def extract(*, body: str, **_kwargs):
        received.append(body)
        return GmailLeadExtraction(
            is_genuine_inquiry=True,
            reason="The thread describes an active project discussion.",
            inquiry_evidence="Could we discuss a project this week?",
        )

    provider = GmailInboundProvider(
        uuid4(), "access", "inbox@example.test", lead_extractor=extract
    )
    provider.api = FakeApi()
    fetched = provider.fetch_record(object_type="message", record_id="newer")
    canonical = provider.normalize_record(fetched)

    assert len(received) == 1
    assert received[0].count("We are comparing providers for our project.") == 1
    assert "Could we discuss a project this week?" in received[0]
    assert "Unique detail: our pilot starts in March." in received[0]
    assert "Cc: ops@example.test" in received[0]
    assert (
        "> We are comparing providers for our project."
        in fetched.values["thread_messages"][1]["body"]
    )
    assert canonical.normalized_payload["email"] == "mira@client.example"
    assert canonical.normalized_payload["gmail_thread_id"] == "thread-full"


def test_gmail_inline_forwarded_headers_select_distinct_prospect() -> None:
    provider = GmailInboundProvider(uuid4(), "access", "inbox@example.test")
    body = (
        "Can you follow up?\n\n"
        "From: Mira Example <mira@client.example>\n"
        "To: sales@agency.example\n"
        "Date: Mon, 19 Oct 2026 09:00:00 +0000\n"
        "Subject: Pricing request\n\n"
        "Could you send pricing for our team?"
    )
    canonical = provider.normalize_record(
        ExternalCrmRecord(
            provider="gmail",
            object_type="message",
            record_id="forward-inline",
            values={
                "thread_id": "thread-forward-inline",
                "headers": {
                    "from": "Sales Rep <rep@agency.example>",
                    "subject": "Fwd: pricing",
                },
                "body": body,
            },
            modified_at=datetime(2026, 10, 19, tzinfo=UTC),
        )
    )
    assert canonical.normalized_payload["email"] == "mira@client.example"
    assert canonical.provider_metadata["original_sender_email"] == "rep@agency.example"
    assert canonical.provider_metadata["gmail_thread_decision"] == "separate_forwarded_lead"
    assert "From: Mira Example <mira@client.example>" in canonical.normalized_payload["message"]


def test_gmail_quoted_header_and_internal_cc_do_not_become_candidate() -> None:
    extraction = GmailLeadExtraction(
        is_genuine_inquiry=True,
        reason="The sender is exploring a project.",
        inquiry_evidence="Could we discuss the project?",
    )
    provider = GmailInboundProvider(
        uuid4(), "access", "inbox@example.test", lead_extractor=lambda **_kwargs: extraction
    )
    quoted = provider.normalize_record(
        ExternalCrmRecord(
            provider="gmail",
            object_type="message",
            record_id="quoted-thread",
            values={
                "thread_id": "thread-quoted",
                "headers": {"from": "Mira <mira@client.example>", "subject": "Project"},
                "body": (
                    "Could we discuss the project?\n"
                    "> From: old.contact@example.test\n"
                    "> Subject: previous inquiry"
                ),
            },
            modified_at=datetime(2026, 10, 19, tzinfo=UTC),
        )
    )
    assert quoted.normalized_payload["email"] == "mira@client.example"
    assert quoted.provider_metadata["gmail_thread_decision"] == "sender_lead"

    cc_provider = GmailInboundProvider(
        uuid4(),
        "access",
        "inbox@example.test",
        lead_extractor=lambda **_kwargs: GmailLeadExtraction(
            is_genuine_inquiry=True,
            reason="An internal colleague appears in the thread.",
            inquiry_evidence="Could we discuss the project?",
            email="staff@agency.example",
            evidence=[
                GmailEvidence(
                    field="email",
                    source="body",
                    excerpt="Cc: staff@agency.example",
                )
            ],
        ),
    )
    with pytest.raises(
        ValueError, match="thread_candidate_not_a_prospect"
    ):
        cc_provider.normalize_record(
            ExternalCrmRecord(
                provider="gmail",
                object_type="message",
                record_id="internal-cc",
                values={
                    "thread_id": "thread-cc",
                    "headers": {"from": "Mira <mira@client.example>", "subject": "Project"},
                    "body": "Could we discuss the project?",
                    "thread_context": (
                        "From: Mira <mira@client.example>\n"
                        "Cc: staff@agency.example\n\nCould we discuss the project?"
                    ),
                },
                modified_at=datetime(2026, 10, 19, tzinfo=UTC),
            )
        )


def test_gmail_internal_dan_thread_selects_external_les_not_quoted_reese() -> None:
    body = (
        "Hi Dan, please see the customer conversation below.\n\n"
        "---------- Forwarded message ---------\n"
        "From: Reese Maddox <reese@ingenuitysys.com>\n"
        "Date: Mon, 19 Oct 2026 09:10:00 +0000\n"
        "To: Dan Asif <dan@ingenuitysys.com>\n"
        "Subject: Fwd: Website pricing\n\n"
        "Forwarding the customer's note.\n\n"
        "From: Les Lehman <les@leslehman.com>\n"
        "Date: Mon, 19 Oct 2026 08:55:00 +0000\n"
        "To: Reese Maddox <reese@ingenuitysys.com>\n"
        "Subject: Website pricing\n\n"
        "I'm the founder at Les Lehman Studio. Could you send pricing for a website refresh?"
    )
    extraction = GmailLeadExtraction(
        is_genuine_inquiry=True,
        reason="Les is the external customer requesting commercial services.",
        inquiry_evidence="Could you send pricing for a website refresh?",
        full_name="Les Lehman",
        email="les@leslehman.com",
        company_name="Les Lehman Studio",
        job_title="Founder",
        requested_service="website refresh",
        evidence=[
            GmailEvidence(field="full_name", source="body", excerpt="Les Lehman"),
            GmailEvidence(field="email", source="body", excerpt="les@leslehman.com"),
            GmailEvidence(field="company_name", source="body", excerpt="Les Lehman Studio"),
            GmailEvidence(field="job_title", source="body", excerpt="founder"),
            GmailEvidence(field="requested_service", source="body", excerpt="website refresh"),
        ],
    )
    observed_headers: list[dict[str, str]] = []
    provider = GmailInboundProvider(
        uuid4(),
        "access",
        "inbox@ingenuitysys.com",
        lead_extractor=lambda **kwargs: (
            observed_headers.append(kwargs["headers"]) or extraction
        ),
        internal_domains={"ingenuitysys.com"},
        internal_emails={
            "dan@ingenuitysys.com",
            "reese@ingenuitysys.com",
        },
    )
    canonical = provider.normalize_record(
        ExternalCrmRecord(
            provider="gmail",
            object_type="message",
            record_id="dan-thread",
            values={
                "thread_id": "dan-les-thread",
                "headers": {
                    "from": "Dan Asif <dan@ingenuitysys.com>",
                    "subject": "Fwd: Website pricing",
                },
                "body": body,
                "thread_context": body,
                "thread_messages": [
                    {
                        "message_id": "dan-thread",
                        "headers": {"from": "Dan Asif <dan@ingenuitysys.com>"},
                        "body": body,
                    }
                ],
            },
            modified_at=datetime(2026, 10, 19, tzinfo=UTC),
        )
    )
    assert canonical.normalized_payload["full_name"] == "Les Lehman"
    assert canonical.normalized_payload["email"] == "les@leslehman.com"
    assert canonical.normalized_payload["company_name"] == "Les Lehman Studio"
    assert canonical.normalized_payload["job_title"] == "Founder"
    assert canonical.provider_metadata["original_sender_email"] == "dan@ingenuitysys.com"
    assert canonical.provider_metadata["gmail_thread_decision"] == "separate_forwarded_lead"
    assert observed_headers[0]["from"] == "Dan Asif <dan@ingenuitysys.com>"


@pytest.mark.parametrize(
    ("rep_name", "rep_email", "person_name", "person_email", "company", "subject", "quoted"),
    [
        (
            "Dan Asif",
            "dan@ingenuitysys.com",
            "Les Lehman",
            "les@leslehman.com",
            "Les Lehman Studio",
            "Re: Les Can I call you on Thursday?",
            False,
        ),
        (
            "Dan A.",
            "dan@ingenuitysys.com",
            "Kathi Moore",
            "kathi@moore.example",
            "Moore Design",
            "Fwd: Website planning",
            False,
        ),
        (
            "Reese Maddox",
            "reese@ingenuitysys.com",
            "Pei Chen",
            "pei@chen.example",
            "Chen Studio",
            "Re: Follow-up",
            True,
        ),
    ],
)
def test_gmail_internal_forwarded_outreach_selects_external_to_recipient(
    rep_name: str,
    rep_email: str,
    person_name: str,
    person_email: str,
    company: str,
    subject: str,
    quoted: bool,
) -> None:
    inquiry = "Could we discuss a website refresh this Thursday?"
    quote_prefix = ">" if quoted else ""
    body = (
        "---------- Forwarded message ---------\n"
        f"{quote_prefix}From: {rep_name} <{rep_email}>\n"
        f"{quote_prefix}To: {person_name} <{person_email}>\n"
        f"{quote_prefix}Subject: {subject}\n"
        f"{quote_prefix}Date: Mon, 19 Oct 2026 09:10:00 +0000\n\n"
        f"Hi {person_name.split()[0]}, {inquiry}\n"
        f"Name: {person_name}\nLead email: {person_email}\n"
        f"Company: {company}\nRole: Founder\nPhone: 1-202-555-0161\n"
        f"Inquiry: {inquiry}"
    )
    extracted = _embedded_lead_candidate(
        name=person_name,
        email=person_email,
        company=company,
        role="Founder",
        phone="1-202-555-0161",
        inquiry=inquiry,
    )
    observed_headers: list[dict[str, str]] = []
    provider = GmailInboundProvider(
        uuid4(),
        "access",
        "sales@ingenuitysys.com",
        internal_domains={"ingenuitysys.com"},
        internal_emails={rep_email},
        lead_extractor=lambda **kwargs: (
            observed_headers.append(kwargs["headers"])
            or GmailLeadExtraction(
                is_genuine_inquiry=True,
                reason="Internal commercial outreach is addressed to this external person.",
                inquiry_evidence=inquiry,
                candidates=[extracted],
            )
        ),
    )
    record = ExternalCrmRecord(
        provider="gmail",
        object_type="message",
        record_id=f"forward-{person_email}",
        values={
            "thread_id": f"thread-{person_email}",
            "headers": {
                "from": f"{rep_name} <{rep_email}>",
                "to": "sales@ingenuitysys.com",
                "subject": f"Fwd: {subject}",
            },
            "body": body,
            "thread_context": (
                f"From: {rep_name} <{rep_email}>\n"
                f"To: sales@ingenuitysys.com\nSubject: Fwd: {subject}\n\n{body}"
            ),
            "thread_messages": [
                {
                    "message_id": f"forward-{person_email}",
                    "headers": {
                        "from": f"{rep_name} <{rep_email}>",
                        "to": "sales@ingenuitysys.com",
                        "subject": f"Fwd: {subject}",
                    },
                    "body": body,
                }
            ],
        },
        modified_at=datetime(2026, 10, 19, tzinfo=UTC),
    )

    [lead] = provider.normalize_records(record)
    assert lead.normalized_payload["full_name"] == person_name
    assert lead.normalized_payload["email"] == person_email
    assert lead.normalized_payload["company_name"] == company
    assert lead.normalized_payload["gmail_candidate_role"] == "direct_prospect"
    assert lead.provider_metadata["original_sender_email"] == rep_email
    assert lead.provider_metadata["gmail_candidate_identity_email"] == person_email
    assert observed_headers[0]["from"] == f"{rep_name} <{rep_email}>"


def test_gmail_thread_with_multiple_internal_forwarded_emails_extracts_each_recipient() -> None:
    first_inquiry = "Could we discuss a website refresh this week?"
    second_inquiry = "Can you help plan a new website refresh?"
    body = (
        "---------- Forwarded message ---------\n"
        "From: Dan Asif <dan@ingenuitysys.com>\n"
        "To: Les Lehman <les@leslehman.com>\n"
        "Subject: Re: Les Can I call you on Thursday?\n"
        "Date: Mon, 19 Oct 2026 09:10:00 +0000\n\n"
        f"Hi Les, {first_inquiry}\n"
        "Name: Les Lehman\nLead email: les@leslehman.com\n"
        "Company: Les Lehman Studio\nRole: Founder\nPhone: 1-202-555-0161\n"
        f"Inquiry: {first_inquiry}\n\n"
        ">---------- Forwarded message ---------\n"
        ">From: Dan Asif <dan@ingenuitysys.com>\n"
        ">To: Kathi Moore <kathi@moore.example>\n"
        ">Subject: Fwd: Website planning\n"
        ">Date: Mon, 19 Oct 2026 09:20:00 +0000\n\n"
        f"Hi Kathi, {second_inquiry}\n"
        "Name: Kathi Moore\nLead email: kathi@moore.example\n"
        "Company: Moore Design\nRole: Director\nPhone: 1-202-555-0162\n"
        f"Inquiry: {second_inquiry}"
    )
    first = _embedded_lead_candidate(
        name="Les Lehman",
        email="les@leslehman.com",
        company="Les Lehman Studio",
        role="Founder",
        phone="1-202-555-0161",
        inquiry=first_inquiry,
    )
    second = _embedded_lead_candidate(
        name="Kathi Moore",
        email="kathi@moore.example",
        company="Moore Design",
        role="Director",
        phone="1-202-555-0162",
        inquiry=second_inquiry,
        candidate_role="external_referrer",
    )
    provider = GmailInboundProvider(
        uuid4(),
        "access",
        "sales@ingenuitysys.com",
        internal_domains={"ingenuitysys.com"},
        internal_emails={"dan@ingenuitysys.com"},
        lead_extractor=lambda **_kwargs: GmailLeadExtraction(
            is_genuine_inquiry=True,
            reason="Two independent outbound commercial conversations are present.",
            inquiry_evidence=first_inquiry,
            candidates=[first, second],
        ),
    )
    record = ExternalCrmRecord(
        provider="gmail",
        object_type="message",
        record_id="dan-forward-batch",
        values={
            "thread_id": "dan-forward-batch-thread",
            "headers": {
                "from": "Dan Asif <dan@ingenuitysys.com>",
                "to": "sales@ingenuitysys.com",
                "subject": "Fwd: customer outreach",
            },
            "body": body,
            "thread_context": body,
            "thread_messages": [
                {
                    "message_id": "dan-forward-batch",
                    "headers": {
                        "from": "Dan Asif <dan@ingenuitysys.com>",
                        "to": "sales@ingenuitysys.com",
                        "subject": "Fwd: customer outreach",
                    },
                    "body": body,
                }
            ],
        },
        modified_at=datetime(2026, 10, 19, tzinfo=UTC),
    )

    leads = provider.normalize_records(record)
    assert [lead.normalized_payload["full_name"] for lead in leads] == [
        "Les Lehman",
        "Kathi Moore",
    ]
    assert [lead.normalized_payload["email"] for lead in leads] == [
        "les@leslehman.com",
        "kathi@moore.example",
    ]
    assert leads[1].normalized_payload["gmail_candidate_role"] == "direct_prospect"
    assert leads[0].provider_record_id != leads[1].provider_record_id
    assert [
        lead.provider_record_id for lead in provider.normalize_records(record)
    ] == [lead.provider_record_id for lead in leads]


@pytest.mark.parametrize("fabricated_identity", [False, True])
@pytest.mark.parametrize("via_fetch", [False, True])
def test_forwarded_outreach_uses_child_evidence_not_legacy_or_optional_fields(
    fabricated_identity: bool, via_fetch: bool,
) -> None:
    inquiry = "Could we discuss a website refresh on Thursday?"
    to_line = "> > To: Avery Lane <avery@lane.example>"
    body = (
        "Please review the forwarded outreach.\n\n"
        "> > From: Morgan West <morgan@agency.example>\n"
        f"{to_line}\n> > Subject: Thursday discussion\n\n"
        f"> > Hi Avery, {inquiry}"
    )
    child = GmailProspectCandidate(
        role="direct_prospect", is_genuine_inquiry=True, confidence=0.96,
        reason="External recipient of internal commercial outreach.",
        inquiry_evidence=inquiry,
        full_name="Invented Person" if fabricated_identity else "Avery Lane",
        email="avery@lane.example",
        company_name="Unsupported Company", requested_service="Website redesign package",
        evidence=[
            GmailEvidence(field="full_name", source="body", excerpt=to_line),
            GmailEvidence(field="email", source="body", excerpt=to_line),
            GmailEvidence(field="company_name", source="body", excerpt="not in the email"),
            GmailEvidence(field="requested_service", source="body", excerpt=inquiry),
        ],
    )
    outer_headers = {
        "from": "Sales <sales@agency.example>" if via_fetch else "Relay <relay@delivery.example>",
        "subject": "Discussion",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        message = {
            "id": "nested-outreach", "threadId": "outreach-thread",
            "labelIds": ["SENT"], "internalDate": "1792368000000",
            "payload": {
                "mimeType": "text/plain",
                "headers": [{"name": key, "value": value} for key, value in outer_headers.items()],
                "body": {"data": _encoded(body)},
            },
        }
        return httpx.Response(
            200, json={"messages": [message]} if "/threads/" in request.url.path else message
        )

    provider = GmailInboundProvider(
        uuid4(), "access", "sales@agency.example",
        client=_client(handler),
        internal_domains={"agency.example"},
        lead_extractor=lambda **_: GmailLeadExtraction(
            is_genuine_inquiry=True, reason="Commercial outreach.",
            inquiry_evidence="This is a paraphrase, not source evidence.",
            full_name="Morgan West", email="morgan@agency.example",
            evidence=[
                GmailEvidence(field="email", source="body", excerpt="invalid legacy evidence")
            ],
            candidates=[child],
        ),
    )
    record = ExternalCrmRecord(
        provider="gmail", object_type="message", record_id="nested-outreach",
        values={} if via_fetch else {
            "headers": outer_headers,
            "body": body, "thread_context": body, "thread_id": "outreach-thread",
        },
        modified_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    if fabricated_identity:
        with pytest.raises(ValueError):
            provider.normalize_records(record)
        return
    [lead] = provider.normalize_records(record)
    assert lead.normalized_payload["full_name"] == "Avery Lane"
    assert lead.normalized_payload["email"] == "avery@lane.example"
    assert lead.normalized_payload["company_name"] == "Unknown"
    assert "Unsupported Company" not in str(lead.normalized_payload)
    assert lead.normalized_payload["gmail_candidate_review_notes"]


def test_gmail_signature_only_contact_is_context_not_a_lead_identity() -> None:
    body = (
        "Could you send pricing for our event project?\n\n"
        "Best regards,\nDina Shah\nFounder\nDina Studio\n"
        "dina@dinastudio.example"
    )
    extraction = GmailLeadExtraction(
        is_genuine_inquiry=True,
        reason="A contact appears only in the representative's signature.",
        inquiry_evidence="Could you send pricing for our event project?",
        candidates=[
            GmailProspectCandidate(
                role="direct_prospect",
                is_genuine_inquiry=True,
                confidence=0.94,
                reason="The model incorrectly selected the signature contact.",
                inquiry_evidence="Could you send pricing for our event project?",
                full_name="Dina Shah",
                email="dina@dinastudio.example",
                company_name="Dina Studio",
                evidence=[
                    GmailEvidence(field="full_name", source="body", excerpt="Dina Shah"),
                    GmailEvidence(
                        field="email",
                        source="body",
                        excerpt="dina@dinastudio.example",
                    ),
                    GmailEvidence(
                        field="company_name", source="body", excerpt="Dina Studio"
                    ),
                ],
            )
        ],
    )
    provider = GmailInboundProvider(
        uuid4(),
        "access",
        "sales@ingenuitysys.com",
        internal_domains={"ingenuitysys.com"},
        internal_emails={"dan@ingenuitysys.com"},
        lead_extractor=lambda **_kwargs: extraction,
    )
    record = ExternalCrmRecord(
        provider="gmail",
        object_type="message",
        record_id="signature-contact",
        values={
            "thread_id": "signature-thread",
            "headers": {
                "from": "Dan Asif <dan@ingenuitysys.com>",
                "subject": "Event project",
            },
            "body": body,
            "thread_context": body,
            "thread_messages": [
                {
                    "message_id": "signature-contact",
                    "headers": {"from": "Dan Asif <dan@ingenuitysys.com>"},
                    "body": body,
                }
            ],
        },
        modified_at=datetime(2026, 10, 19, tzinfo=UTC),
    )

    canonical = provider.normalize_record(record)
    assert canonical.normalized_payload["gmail_signature_contacts"][0]["role"] == (
        "signature_only"
    )
    with pytest.raises(ValueError, match="no_supported_prospect_candidates"):
        provider.normalize_records(record)


def test_gmail_ai_can_select_one_of_multiple_external_thread_participants() -> None:
    body = (
        "From: Les Lehman <les@leslehman.com>\n"
        "To: Dan Asif <dan@ingenuitysys.com>\n"
        "Subject: Pricing request\n\n"
        "Could you send pricing for the website refresh?\n\n"
        "From: Pat Lee <pat@agency.example>\n"
        "Date: Mon, 19 Oct 2026 09:10:00 +0000\n"
        "Subject: Re: Pricing request\n\nPat is copied for implementation."
    )
    provider = GmailInboundProvider(
        uuid4(),
        "access",
        "inbox@ingenuitysys.com",
        lead_extractor=lambda **_kwargs: GmailLeadExtraction(
            is_genuine_inquiry=True,
            reason="Les made the commercial request; Pat is mentioned as a collaborator.",
            inquiry_evidence="Could you send pricing for the website refresh?",
            full_name="Les Lehman",
            email="les@leslehman.com",
            evidence=[
                GmailEvidence(field="full_name", source="body", excerpt="Les Lehman"),
                GmailEvidence(field="email", source="body", excerpt="les@leslehman.com"),
            ],
        ),
        internal_domains={"ingenuitysys.com"},
    )
    canonical = provider.normalize_record(
        ExternalCrmRecord(
            provider="gmail",
            object_type="message",
            record_id="multi-external",
            values={
                "thread_id": "multi-external-thread",
                "headers": {
                    "from": "Dan Asif <dan@ingenuitysys.com>",
                    "subject": "Fwd: Pricing request",
                },
                "body": body,
                "thread_context": body,
            },
            modified_at=datetime(2026, 10, 19, tzinfo=UTC),
        )
    )
    assert canonical.normalized_payload["email"] == "les@leslehman.com"


def test_gmail_referral_thread_creates_company_and_phone_only_referral_candidates() -> None:
    body = (
        "Maya Jones at Swift Water Law wants to discuss a collaboration.\n"
        "Rick Kosakowski replied: Please contact Mr. Patel at 1-202-324-3000. "
        "For the second matter, contact Ms. Singh at 1-202-555-0148."
    )
    inquiry = "wants to discuss a collaboration"
    extraction = GmailLeadExtraction(
        is_genuine_inquiry=True,
        reason="The thread contains a company inquiry and two explicit referrals.",
        inquiry_evidence=inquiry,
        candidates=[
            GmailProspectCandidate(
                role="direct_prospect",
                is_genuine_inquiry=True,
                confidence=0.96,
                reason="Maya requested a collaboration for Swift Water Law.",
                inquiry_evidence=inquiry,
                full_name="Maya Jones",
                email="maya@swiftwaterlaw.com",
                company_name="Swift Water Law",
                evidence=[
                    GmailEvidence(field="full_name", source="body", excerpt="Maya Jones"),
                    GmailEvidence(
                        field="email",
                        source="from_header",
                        excerpt="maya@swiftwaterlaw.com",
                    ),
                    GmailEvidence(field="company_name", source="body", excerpt="Swift Water Law"),
                ],
            ),
            GmailProspectCandidate(
                role="referred_prospect",
                is_genuine_inquiry=True,
                confidence=0.93,
                reason="Rick explicitly referred the team to Mr. Patel.",
                inquiry_evidence="Please contact Mr. Patel at 1-202-324-3000",
                full_name="Mr. Patel",
                phone="1-202-324-3000",
                referred_by="Rick Kosakowski",
                referral_context="Swift Water Law wants to discuss a collaboration",
                evidence=[
                    GmailEvidence(field="full_name", source="body", excerpt="Mr. Patel"),
                    GmailEvidence(field="phone", source="body", excerpt="1-202-324-3000"),
                    GmailEvidence(
                        field="referred_by",
                        source="body",
                        excerpt="Rick Kosakowski",
                    ),
                    GmailEvidence(
                        field="referral_context",
                        source="body",
                        excerpt="Swift Water Law wants to discuss a collaboration",
                    ),
                ],
            ),
            GmailProspectCandidate(
                role="referred_prospect",
                is_genuine_inquiry=True,
                confidence=0.91,
                reason="Rick explicitly referred the team to Ms. Singh.",
                inquiry_evidence="contact Ms. Singh at 1-202-555-0148",
                full_name="Ms. Singh",
                phone="1-202-555-0148",
                referred_by="Rick Kosakowski",
                evidence=[
                    GmailEvidence(field="full_name", source="body", excerpt="Ms. Singh"),
                    GmailEvidence(field="phone", source="body", excerpt="1-202-555-0148"),
                    GmailEvidence(
                        field="referred_by",
                        source="body",
                        excerpt="Rick Kosakowski",
                    ),
                ],
            ),
            GmailProspectCandidate(
                role="external_referrer",
                is_genuine_inquiry=True,
                confidence=0.99,
                reason="Rick is the connector, not a referred prospect.",
                inquiry_evidence="Please contact Mr. Patel",
                full_name="Rick Kosakowski",
            ),
            GmailProspectCandidate(
                role="referred_prospect",
                is_genuine_inquiry=True,
                confidence=0.35,
                reason="The thread may mention another referred person.",
                inquiry_evidence="contact Ms. Singh",
                full_name="Unclear prospect",
            ),
        ],
    )
    provider = GmailInboundProvider(
        uuid4(),
        "access",
        "sales@ingenuitysys.com",
        internal_domains={"ingenuitysys.com"},
        lead_extractor=lambda **_kwargs: extraction,
    )
    record = ExternalCrmRecord(
        provider="gmail",
        object_type="message",
        record_id="rick-reply",
        values={
            "thread_id": "rick-referral-thread",
            "headers": {
                "from": "Rick Kosakowski <rick@swiftwaterlaw.com>",
                "subject": "Re: Swift Water Law collaboration",
            },
            "body": body,
            "thread_context": (
                "From: Maya Jones <maya@swiftwaterlaw.com>\n\n"
                f"{body}\n\n"
                "From: Rick Kosakowski <rick@swiftwaterlaw.com>"
            ),
            "thread_messages": [
                {
                    "message_id": "maya-message",
                    "headers": {"from": "Maya Jones <maya@swiftwaterlaw.com>"},
                    "body": body,
                },
                {
                    "message_id": "rick-reply",
                    "headers": {"from": "Rick Kosakowski <rick@swiftwaterlaw.com>"},
                    "body": body,
                },
            ],
        },
        modified_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    candidates = provider.normalize_records(record)
    repeated = provider.normalize_records(record)
    assert [item.normalized_payload["full_name"] for item in candidates] == [
        "Maya Jones",
        "Mr. Patel",
        "Ms. Singh",
    ]
    assert [item.normalized_payload["email"] for item in candidates] == [
        "maya@swiftwaterlaw.com",
        "",
        "",
    ]
    assert candidates[1].normalized_payload["phone"] == "1-202-324-3000"
    assert candidates[1].normalized_payload["gmail_referred_by"] == "Rick Kosakowski"
    assert candidates[1].normalized_payload["gmail_referral_context"] == (
        "Swift Water Law wants to discuss a collaboration"
    )
    assert candidates[1].normalized_payload["gmail_candidate_role"] == "referred_prospect"
    phone_only_lead = normalize_lead(candidates[1].normalized_payload, uuid4())
    assert phone_only_lead.email == ""
    assert phone_only_lead.phone == "1-202-324-3000"
    assert "email" in candidates[1].normalized_payload["gmail_unknown_identity_fields"]
    assert candidates[0].normalized_payload["company_name"] == "Swift Water Law"
    assert candidates[1].normalized_payload["gmail_source_message_ids"] == [
        "maya-message",
        "rick-reply",
    ]
    assert any(
        participant["role"] == "external_referrer"
        for participant in candidates[1].normalized_payload["gmail_participant_roles"]
    )
    assert "requires manual review" in candidates[0].normalized_payload[
        "gmail_candidate_review_notes"
    ][0]
    assert len({item.provider_record_id for item in candidates}) == 3
    assert [item.provider_record_id for item in candidates] == [
        item.provider_record_id for item in repeated
    ]
    assert all(
        item.normalized_payload["gmail_message_id"] == "rick-reply"
        and item.normalized_payload["gmail_thread_id"] == "rick-referral-thread"
        for item in candidates
    )


def _embedded_lead_candidate(
    *,
    name: str,
    email: str,
    company: str,
    role: str,
    phone: str,
    inquiry: str,
    candidate_role: str = "direct_prospect",
    referred_by: str | None = None,
) -> GmailProspectCandidate:
    evidence = [
        GmailEvidence(field="full_name", source="body", excerpt=f"Name: {name}"),
        GmailEvidence(field="email", source="body", excerpt=f"Lead email: {email}"),
        GmailEvidence(field="company_name", source="body", excerpt=f"Company: {company}"),
        GmailEvidence(field="job_title", source="body", excerpt=f"Role: {role}"),
        GmailEvidence(field="phone", source="body", excerpt=f"Phone: {phone}"),
        GmailEvidence(
            field="requested_service", source="body", excerpt=f"Inquiry: {inquiry}"
        ),
    ]
    if referred_by:
        evidence.append(
            GmailEvidence(
                field="referred_by", source="body", excerpt=f"Referred by: {referred_by}"
            )
        )
    return GmailProspectCandidate(
        role=candidate_role,
        is_genuine_inquiry=True,
        confidence=0.97,
        reason=f"{name} is the external person identified in the lead payload.",
        inquiry_evidence=inquiry,
        full_name=name,
        email=email,
        company_name=company,
        job_title=role,
        phone=phone,
        requested_service="website refresh",
        referred_by=referred_by,
        evidence=evidence,
    )


def _notification_record(
    *,
    subject: str,
    body: str,
    candidates: list[GmailProspectCandidate],
    record_id: str = "notification-message",
) -> tuple[GmailInboundProvider, ExternalCrmRecord]:
    extraction = GmailLeadExtraction(
        is_genuine_inquiry=True,
        reason="The embedded payload contains an external commercial inquiry.",
        inquiry_evidence=candidates[0].inquiry_evidence,
        candidates=candidates,
    )
    provider = GmailInboundProvider(
        uuid4(),
        "access",
        "sales@ingenuitysys.com",
        internal_domains={"ingenuitysys.com"},
        internal_emails={"reese@ingenuitysys.com"},
        lead_extractor=lambda **_kwargs: extraction,
    )
    headers = {
        "from": "Reese Maddox <reese@ingenuitysys.com>",
        "subject": subject,
    }
    record = ExternalCrmRecord(
        provider="gmail",
        object_type="message",
        record_id=record_id,
        values={
            "thread_id": f"thread-{record_id}",
            "headers": headers,
            "body": body,
            "thread_context": (
                f"--- Gmail message {record_id} ---\n"
                f"From: {headers['from']}\nSubject: {subject}\n\n{body}"
            ),
            "thread_messages": [
                {
                    "message_id": record_id,
                    "headers": headers,
                    "body": body,
                }
            ],
        },
        modified_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    return provider, record


def test_gmail_ingenuity_notification_reese_wrapper_uses_embedded_lead_identity() -> None:
    inquiry = "Could you send pricing for a website refresh?"
    body = (
        "Lead Details\nName: Jordan Bell\nLead email: jordan@bellstudio.example\n"
        "Company: Bell Studio\nRole: Founder\nPhone: 1-202-555-0177\n"
        f"Inquiry: {inquiry}\nMessage-ID: <original-jordan-77@bellstudio.example>"
    )
    external = _embedded_lead_candidate(
        name="Jordan Bell",
        email="jordan@bellstudio.example",
        company="Bell Studio",
        role="Founder",
        phone="1-202-555-0177",
        inquiry=inquiry,
    )
    internal_decoy = GmailProspectCandidate(
        role="direct_prospect",
        is_genuine_inquiry=True,
        confidence=0.99,
        reason="Wrapper sender incorrectly selected as a lead.",
        inquiry_evidence=inquiry,
        full_name="Reese Maddox",
        email="reese@ingenuitysys.com",
    )
    provider, record = _notification_record(
        subject="Ingenuity Lead Notifications — Reese Maddox",
        body=body,
        candidates=[internal_decoy, external],
        record_id="ingenuity-notification-reese",
    )

    [lead] = provider.normalize_records(record)
    assert lead.normalized_payload["full_name"] == "Jordan Bell"
    assert lead.normalized_payload["email"] == "jordan@bellstudio.example"
    assert lead.normalized_payload["company_name"] == "Bell Studio"
    assert lead.normalized_payload["job_title"] == "Founder"
    assert lead.normalized_payload["phone"] == "1-202-555-0177"
    assert lead.normalized_payload["gmail_notification_wrapper"] is True
    assert lead.normalized_payload["gmail_wrapper_sender_name"] == "Reese Maddox"
    assert lead.normalized_payload["gmail_embedded_message_ids"] == [
        "original-jordan-77@bellstudio.example"
    ]
    assert lead.provider_metadata["gmail_notification_wrapper"] is True
    assert lead.provider_metadata["gmail_wrapper_sender_name"] == "Reese Maddox"
    assert lead.provider_metadata["original_sender_email"] == "reese@ingenuitysys.com"
    assert lead.normalized_payload["gmail_message_id"] == "ingenuity-notification-reese"
    assert lead.normalized_payload["gmail_thread_id"] == "thread-ingenuity-notification-reese"
    internal_provider, internal_record = _notification_record(
        subject="Ingenuity Lead Notifications — Reese Maddox",
        body=body,
        candidates=[internal_decoy],
        record_id="internal-only-notification",
    )
    with pytest.raises(ValueError, match="internal_notification_identity_only"):
        internal_provider.normalize_records(internal_record)


def test_gmail_notification_with_one_embedded_lead_uses_original_from_and_thread_evidence() -> None:
    inquiry = "We need a website refresh. Could you send pricing?"
    body = (
        "Lead Information:\nName: Avery Stone\nLead email: avery@stoneworks.example\n"
        "Company: Stoneworks\nRole: Operations Director\nPhone: 1-202-555-0130\n"
        f"Inquiry: {inquiry}\n\n"
        "Original message headers:\n"
        "From: Avery Stone <avery@stoneworks.example>\n"
        "Reply-To: avery@stoneworks.example\n"
        "To: Reese Maddox <reese@ingenuitysys.com>\n"
        "Cc: team@stoneworks.example\nSubject: Website refresh pricing\n"
        "Message-ID: <avery-original@stoneworks.example>\n\n"
        f"{inquiry}"
    )
    candidate = _embedded_lead_candidate(
        name="Avery Stone",
        email="avery@stoneworks.example",
        company="Stoneworks",
        role="Operations Director",
        phone="1-202-555-0130",
        inquiry=inquiry,
    )
    provider, record = _notification_record(
        subject="New lead received",
        body=body,
        candidates=[candidate],
        record_id="one-embedded-lead",
    )

    [lead] = provider.normalize_records(record)
    assert lead.normalized_payload["full_name"] == "Avery Stone"
    assert lead.normalized_payload["email"] == "avery@stoneworks.example"
    assert "Original message headers:" in lead.normalized_payload["message"]
    assert "Cc: team@stoneworks.example" in lead.normalized_payload["message"]
    assert lead.normalized_payload["gmail_notification_wrapper"] is True


def test_gmail_notification_with_multiple_embedded_leads_keeps_candidates_separate() -> None:
    inquiry_a = "Could you send pricing for a website refresh?"
    inquiry_b = "We need help planning our new website refresh."
    body = (
        "Lead Details A\nName: Morgan Reed\nLead email: morgan@reed.example\n"
        "Company: Reed Group\nRole: CEO\nPhone: 1-202-555-0151\n"
        f"Inquiry: {inquiry_a}\n\n"
        "Lead Details B\nName: Taylor Kim\nLead email: taylor@kim.example\n"
        "Company: Kim Design\nRole: Marketing Lead\nPhone: 1-202-555-0152\n"
        f"Inquiry: {inquiry_b}\nReferred by: Morgan Reed"
    )
    first = _embedded_lead_candidate(
        name="Morgan Reed",
        email="morgan@reed.example",
        company="Reed Group",
        role="CEO",
        phone="1-202-555-0151",
        inquiry=inquiry_a,
    )
    second = _embedded_lead_candidate(
        name="Taylor Kim",
        email="taylor@kim.example",
        company="Kim Design",
        role="Marketing Lead",
        phone="1-202-555-0152",
        inquiry=inquiry_b,
        candidate_role="referred_prospect",
        referred_by="Morgan Reed",
    )
    provider, record = _notification_record(
        subject="Ingenuity Lead Notifications",
        body=body,
        candidates=[first, second],
        record_id="multi-embedded-leads",
    )

    leads = provider.normalize_records(record)
    assert [lead.normalized_payload["full_name"] for lead in leads] == [
        "Morgan Reed",
        "Taylor Kim",
    ]
    assert [lead.normalized_payload["email"] for lead in leads] == [
        "morgan@reed.example",
        "taylor@kim.example",
    ]
    assert leads[1].normalized_payload["gmail_referred_by"] == "Morgan Reed"
    assert leads[0].provider_record_id != leads[1].provider_record_id


def test_gmail_direct_external_inbound_thread_remains_a_single_external_lead() -> None:
    inquiry = "Could you send pricing for our website refresh?"
    candidate = GmailProspectCandidate(
        role="direct_prospect",
        is_genuine_inquiry=True,
        confidence=0.96,
        reason="The external sender directly requests commercial services.",
        inquiry_evidence=inquiry,
        full_name="Casey Lane",
        email="casey@lane.example",
        company_name="Lane Studio",
        requested_service="website refresh",
        evidence=[
            GmailEvidence(field="full_name", source="from_header", excerpt="Casey Lane"),
            GmailEvidence(field="email", source="from_header", excerpt="casey@lane.example"),
            GmailEvidence(field="company_name", source="body", excerpt="Lane Studio"),
            GmailEvidence(
                field="requested_service", source="body", excerpt="website refresh"
            ),
        ],
    )
    provider = GmailInboundProvider(
        uuid4(),
        "access",
        "sales@ingenuitysys.com",
        internal_domains={"ingenuitysys.com"},
        lead_extractor=lambda **_kwargs: GmailLeadExtraction(
            is_genuine_inquiry=True,
            reason="The external sender requests pricing.",
            inquiry_evidence=inquiry,
            candidates=[candidate],
        ),
    )
    record = ExternalCrmRecord(
        provider="gmail",
        object_type="message",
        record_id="direct-casey",
        values={
            "thread_id": "direct-casey-thread",
            "headers": {
                "from": "Casey Lane <casey@lane.example>",
                "subject": "Website refresh pricing",
            },
            "body": f"We are Lane Studio. {inquiry}",
            "thread_context": (
                "From: Casey Lane <casey@lane.example>\n"
                "Subject: Website refresh pricing\n\n"
                f"We are Lane Studio. {inquiry}"
            ),
        },
        modified_at=datetime(2026, 10, 19, tzinfo=UTC),
    )

    [lead] = provider.normalize_records(record)
    assert lead.normalized_payload["full_name"] == "Casey Lane"
    assert lead.normalized_payload["email"] == "casey@lane.example"
    assert lead.normalized_payload["gmail_notification_wrapper"] is False


def test_gmail_fetch_does_not_drop_automated_lead_notification_wrapper() -> None:
    inquiry = "Could you send pricing for our website refresh?"
    body = (
        "Lead Details\nName: Jordan Bell\nLead email: jordan@bellstudio.example\n"
        "Company: Bell Studio\nRole: Founder\nPhone: 1-202-555-0177\n"
        f"Inquiry: {inquiry}"
    )

    class FakeApi:
        def get(self, path: str, **_params):
            if path == "messages/wrapper-auto":
                return {
                    "id": "wrapper-auto",
                    "threadId": "wrapper-auto-thread",
                    "labelIds": ["INBOX"],
                    "internalDate": "1792400000000",
                    "snippet": body,
                    "payload": {
                        "headers": [
                            {
                                "name": "From",
                                "value": (
                                    "Ingenuity Lead Notifications "
                                    "<notifications@ingenuitysys.com>"
                                ),
                            },
                            {
                                "name": "Subject",
                                "value": "Ingenuity Lead Notifications — Reese Maddox",
                            },
                        ]
                    },
                }
            if path == "threads/wrapper-auto-thread":
                return {
                    "messages": [
                        {
                            "id": "wrapper-auto",
                            "threadId": "wrapper-auto-thread",
                            "internalDate": "1792400000000",
                            "payload": {
                                "mimeType": "text/plain",
                                "headers": [
                                    {
                                        "name": "From",
                                        "value": (
                                            "Ingenuity Lead Notifications "
                                            "<notifications@ingenuitysys.com>"
                                        ),
                                    },
                                    {
                                        "name": "Subject",
                                        "value": (
                                            "Ingenuity Lead Notifications — Reese Maddox"
                                        ),
                                    },
                                ],
                                "body": {"data": _encoded(body)},
                            },
                        }
                    ]
                }
            raise AssertionError(f"Unexpected Gmail API path: {path}")

    provider = GmailInboundProvider(
        uuid4(),
        "access",
        "sales@ingenuitysys.com",
        internal_domains={"ingenuitysys.com"},
    )
    provider.api = FakeApi()
    fetched = provider.fetch_record(object_type="message", record_id="wrapper-auto")
    assert fetched.values.get("skip_reason") is None
    assert fetched.values["body"] == body
    assert "Ingenuity Lead Notifications" in fetched.values["thread_context"]


def test_gmail_forwarded_identity_is_stable_across_repeated_normalization() -> None:
    provider = GmailInboundProvider(uuid4(), "access", "inbox@example.test")
    record = ExternalCrmRecord(
        provider="gmail",
        object_type="message",
        record_id="forward-repeat",
        values={
            "thread_id": "thread-repeat",
            "headers": {"from": "Rep <rep@agency.example>", "subject": "Fwd: lead"},
            "body": (
                "---------- Forwarded message ---------\n"
                "From: Mira <mira@client.example>\n"
                "Subject: Pricing request\n\nCould you send pricing?"
            ),
        },
        modified_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    first = provider.normalize_record(record)
    second = provider.normalize_record(record)
    assert first.normalized_payload["email"] == second.normalized_payload["email"]
    assert first.normalized_payload["email"] == "mira@client.example"
    assert first.source_fingerprint == second.source_fingerprint


class _CanonicalGmailProvider:
    provider_name = "gmail"

    def __init__(
        self,
        connection_id: UUID,
        messages: list[ExternalCrmRecord],
        *,
        email: str = "mira@client.example",
        name: str = "Mira Example",
        forwarded_lead: bool = False,
        original_sender_email: str | None = None,
    ) -> None:
        self.connection_id = connection_id
        self.messages = messages
        self.normalize_count = 0
        self.email = email
        self.name = name
        self.forwarded_lead = forwarded_lead
        self.original_sender_email = original_sender_email

    def list_records(
        self, *, object_type: str, cursor: InboundCursor | None, limit: int
    ) -> InboundCrmPage:
        del object_type, cursor
        return InboundCrmPage(
            records=self.messages[:limit],
            checkpoint={"history_id": str(len(self.messages) + 100)},
        )

    def fetch_record(self, *, object_type: str, record_id: str) -> ExternalCrmRecord:
        del object_type
        return next(message for message in self.messages if message.record_id == record_id)

    def normalize_record(self, record: ExternalCrmRecord) -> CanonicalInboundLead:
        self.normalize_count += 1
        values = record.values
        content = str(values["body"])
        payload = {
            "source": "email",
            "source_detail": "Gmail",
            "external_id": record.record_id,
            "full_name": self.name,
            "email": self.email,
            "company_name": "Client Example",
            "product_or_service": "Inbound inquiry",
            "message": content,
            "received_at": record.modified_at,
            "gmail_message_id": record.record_id,
            "gmail_thread_id": str(values["thread_id"]),
            "gmail_mailbox": "inbox@example.test",
        }
        return CanonicalInboundLead(
            provider="gmail",
            object_type="message",
            provider_record_id=record.record_id,
            normalized_payload=payload,
            source_fingerprint=hashlib.sha256(content.encode()).hexdigest(),
            provider_modified_at=record.modified_at,
            provider_source="Gmail",
            provider_metadata={
                "gmail_message_id": record.record_id,
                "gmail_thread_id": str(values["thread_id"]),
                "gmail_mailbox": "inbox@example.test",
                "sender_email": self.email,
                "forwarded_lead": self.forwarded_lead,
                "original_sender_email": self.original_sender_email or self.email,
                "gmail_thread_decision": (
                    "separate_forwarded_lead" if self.forwarded_lead else "sender_lead"
                ),
                "gmail_candidate_identity_email": self.email,
            },
        )


def test_gmail_message_ids_deduplicate_and_new_thread_message_updates_same_contact(
    tmp_path: Path, icp: IcpConfig, routing: RoutingConfig, monkeypatch
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gmail.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        confirmation_calls: list[bool] = []
        monkeypatch.setattr(
            application_services,
            "_send_lead_confirmation_email",
            lambda *_args, **_kwargs: confirmation_calls.append(True),
        )
        workspace_id = uuid4()
        ensure_workspace(session, workspace_id, name="Gmail test workspace")
        now = datetime.now(UTC)
        connection = ProviderConnectionORM(
            id=uuid4(),
            workspace_id=workspace_id,
            provider_type="gmail",
            external_account_id="inbox@example.test",
            account_name="inbox@example.test",
            auth_type="oauth2",
            status="verified",
            scopes=[GMAIL_READONLY_SCOPE],
            secret_reference="encrypted-reference",
            credential_state="configured",
            connection_metadata={"gmail_email": "inbox@example.test"},
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(connection)
        session.commit()

        slack_connection = ProviderConnectionORM(
            id=uuid4(),
            workspace_id=workspace_id,
            provider_type="slack",
            account_name="Synthetic Slack workspace",
            auth_type="oauth2",
            status="verified",
            credential_state="configured",
            connection_metadata={"slack_channel_id": "C-SYNTHETIC"},
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(slack_connection)
        session.commit()
        delivery_correlations: list[str] = []

        class SlackProvider:
            provider_name = "slack"
            connection_id = slack_connection.id

            def send(self, message: NotificationMessage) -> NotificationDelivery:
                delivery_correlations.append(current_request_id())
                return NotificationDelivery(state="sent", external_id="synthetic-slack-id")

        def message(message_id: str, body: str) -> ExternalCrmRecord:
            return ExternalCrmRecord(
                provider="gmail",
                object_type="message",
                record_id=message_id,
                values={"thread_id": "thread-1", "body": body},
                modified_at=now,
            )

        def run(provider: _CanonicalGmailProvider, mode: InboundSyncMode) -> CrmInboundSyncRunORM:
            configure_inbound_sync(
                session, connection, enabled=True, object_type="message", actor_id="admin"
            )
            return run_inbound_sync(
                session,
                connection,
                provider,
                mode=mode,
                object_type="message",
                limit=10,
                page_size=10,
                actor_id="scheduler",
                icp=icp,
                routing=routing,
                notification_provider=SlackProvider(),
                send_confirmation_email=False,
            )

        first = run(
            _CanonicalGmailProvider(connection.id, [message("m1", "We need a demo for our team.")]),
            "initial",
        )
        lead = session.scalar(select(LeadORM))
        assert lead is not None and lead.source == "email" and lead.external_id == "m1"
        assert lead.lead_number == 1
        first_decisions = session.scalar(
            select(func.count()).select_from(models.RoutingDecisionORM)
        )
        assert first_decisions is not None
        second = run(
            _CanonicalGmailProvider(
                connection.id, [message("m2", "Please schedule a demo next week for our team.")]
            ),
            "incremental",
        )
        links = list(
            session.scalars(
                select(CrmProviderRecordLinkORM).where(
                    CrmProviderRecordLinkORM.connection_id == connection.id
                )
            )
        )
        assert first.records_created == 1
        assert second.records_updated == 1
        assert lead.lead_number == 1
        assert confirmation_calls == []
        assert delivery_correlations == [first.correlation_id]
        assert all(value != "-" for value in delivery_correlations)
        assert current_request_id() == "-"
        notification_audits = list(
            session.scalars(
                select(models.ProviderLifecycleAuditORM)
                .where(models.ProviderLifecycleAuditORM.action == "lead_notification")
                .order_by(models.ProviderLifecycleAuditORM.created_at)
            )
        )
        assert [audit.correlation_id for audit in notification_audits] == delivery_correlations
        assert all(audit.provider == "slack" for audit in notification_audits)
        assert len(links) == 2
        assert {link.provider_record_id for link in links} == {"m1", "m2"}
        assert all(link.provider == "gmail" and link.object_type == "message" for link in links)
        assert all(link.provider_metadata["gmail_thread_id"] == "thread-1" for link in links)

        repeated = run(
            _CanonicalGmailProvider(
                connection.id, [message("m2", "Please schedule a demo next week for our team.")]
            ),
            "manual",
        )
        assert repeated.records_unchanged == 1
        assert confirmation_calls == []
        assert len(delivery_correlations) == 1
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 1
        forwarder_run = run(
            _CanonicalGmailProvider(
                connection.id,
                [message("rep1", "Forwarding a possible lead.")],
                email="rep@agency.example",
                name="Sales Rep",
            ),
            "incremental",
        )
        forwarded_run = run(
            _CanonicalGmailProvider(
                connection.id,
                [message("forward1", "Forwarded project request.")],
                email="prospect@client.example",
                name="Mira Prospect",
                forwarded_lead=True,
                original_sender_email="rep@agency.example",
            ),
            "incremental",
        )
        linked_run = run(
            _CanonicalGmailProvider(
                connection.id,
                [message("forward2", "The same prospect forwarded again.")],
                email="prospect@client.example",
                name="Mira Prospect",
                forwarded_lead=True,
                original_sender_email="rep@agency.example",
            ),
            "incremental",
        )
        repeated_forward = run(
            _CanonicalGmailProvider(
                connection.id,
                [message("forward2", "The same prospect forwarded again.")],
                email="prospect@client.example",
                name="Mira Prospect",
                forwarded_lead=True,
                original_sender_email="rep@agency.example",
            ),
            "manual",
        )
        rep_lead = session.scalar(
            select(LeadORM).where(LeadORM.email == "rep@agency.example")
        )
        prospect_lead = session.scalar(
            select(LeadORM).where(LeadORM.email == "prospect@client.example")
        )
        assert forwarder_run.records_created == 1
        assert forwarded_run.records_created == 1
        assert linked_run.records_updated == 1
        assert repeated_forward.records_unchanged == 1
        assert "Mira Prospect" in (forwarded_run.last_error or "")
        assert "updated." in (linked_run.last_error or "")
        assert rep_lead is not None and prospect_lead is not None
        assert rep_lead.id != prospect_lead.id
        assert session.scalar(select(func.count()).select_from(LeadORM)) == 3
        assert session.scalar(
            select(func.count())
            .select_from(models.ProviderLifecycleAuditORM)
            .where(models.ProviderLifecycleAuditORM.action == "gmail_candidate_linked_existing")
        ) >= 1
        decisions_before_review = session.scalar(
            select(func.count()).select_from(models.RoutingDecisionORM)
        )
        review_provider = _CanonicalGmailProvider(
            connection.id,
            [
                message("m1", "We need a demo for our team."),
                message("m2", "Please schedule a demo next week for our team."),
            ],
        )
        review = run(review_provider, "review_recent")
        assert review.records_fetched == 2
        assert review.records_unchanged == 2
        assert review_provider.normalize_count == 0
        assert (
            session.scalar(select(func.count()).select_from(models.RoutingDecisionORM))
            == decisions_before_review
        )


def test_gmail_admin_settings_require_manage_and_sync_requires_sync_permission(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gmail-rbac.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    workspace_id = uuid4()
    ensure_workspace(session, workspace_id, name="Gmail RBAC workspace")
    now = datetime.now(UTC)
    connection = ProviderConnectionORM(
        id=uuid4(),
        workspace_id=workspace_id,
        provider_type="gmail",
        external_account_id="inbox@example.test",
        account_name="inbox@example.test",
        auth_type="oauth2",
        status="verified",
        scopes=[GMAIL_READONLY_SCOPE],
        secret_reference="encrypted-reference",
        credential_state="configured",
        connection_metadata={"gmail_email": "inbox@example.test"},
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(connection)
    session.commit()

    def override_db() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        workspace_id=workspace_id,
        actor_id="integration-admin",
        role="custom",
        permissions=frozenset({"integrations.manage", "integrations.view"}),
    )
    try:
        with TestClient(app) as client:
            configured = client.put(
                f"/v1/integrations/connections/{connection.id}/inbound-sync/configuration",
                json={"enabled": True, "object_type": "message"},
            )
            assert configured.status_code == 200
            app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
                workspace_id=workspace_id,
                actor_id="limited-agent",
                role="custom",
                permissions=frozenset({"agents.execute"}),
            )
            configure_denied = client.put(
                f"/v1/integrations/connections/{connection.id}/inbound-sync/configuration",
                json={"enabled": False, "object_type": "message"},
            )
            sync_denied = client.post(
                f"/v1/integrations/connections/{connection.id}/inbound-sync",
                json={"mode": "manual", "object_type": "message", "limit": 1},
            )
        assert configure_denied.status_code == 403
        assert sync_denied.status_code == 403
    finally:
        app.dependency_overrides.clear()
        session.close()
