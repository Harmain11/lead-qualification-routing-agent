from __future__ import annotations

import base64
import binascii
import html as html_module
import re
from datetime import UTC, datetime
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission
from lead_agent.application.gmail_history import append_gmail_thread_messages
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import LeadORM, ProviderConnectionORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.gmail import (
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    GmailInboundProvider,
    GmailOutboundProvider,
)
from lead_agent.infrastructure.provider_lifecycle import get_workspace_connection_resolver

router = APIRouter(prefix="/v1/email", tags=["email"])
_EMAIL_ADDRESS = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
_GMAIL_ID = re.compile(r"^[A-Za-z0-9_-]{1,255}$")
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
_ALLOWED_EMAIL_TAGS = {
    "a", "b", "blockquote", "br", "div", "em", "i", "li", "ol", "p", "span",
    "strong", "u", "ul",
}
_VOID_EMAIL_TAGS = {"br"}


class EmailAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream", max_length=127)
    content_base64: str = Field(min_length=1, max_length=7_000_000)

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("Attachment filenames cannot contain line breaks")
        filename = re.split(r"[\\/]", value.strip())[-1]
        if filename in {"", ".", ".."}:
            raise ValueError("Choose a valid attachment filename")
        return filename

    @field_validator("content_type")
    @classmethod
    def valid_content_type(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+", value):
            raise ValueError("Attachment content type is invalid")
        return value.lower()


class _EmailHTMLSanitizer(HTMLParser):
    """Allow only basic formatting and safe links in user-authored email HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _ALLOWED_EMAIL_TAGS:
            return
        if tag == "a":
            href = next((value for name, value in attrs if name.lower() == "href"), None)
            if href and "\r" not in href and "\n" not in href:
                parsed = urlsplit(href.strip())
                if parsed.scheme in {"http", "https"} and parsed.netloc:
                    self.parts.append(
                        f'<a href="{html_module.escape(href.strip(), quote=True)}" '
                        'rel="noopener noreferrer">'
                    )
                    return
                if parsed.scheme == "mailto" and parsed.path:
                    self.parts.append(
                        f'<a href="{html_module.escape(href.strip(), quote=True)}">'
                    )
                    return
        self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in _ALLOWED_EMAIL_TAGS and tag not in _VOID_EMAIL_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(html_module.escape(data))


def _sanitize_email_html(value: str | None) -> str | None:
    if not value:
        return None
    parser = _EmailHTMLSanitizer()
    parser.feed(value[:40_000])
    parser.close()
    return "".join(parser.parts)


def _validate_address_list(value: str, *, required: bool) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError("Email recipients cannot contain line breaks")
    addresses = [address.strip() for _name, address in getaddresses([value]) if address]
    if ((required or value.strip()) and not addresses) or len(addresses) > 10 or any(
        not _EMAIL_ADDRESS.fullmatch(address) for address in addresses
    ):
        raise ValueError("Enter up to 10 valid email addresses, separated by commas")
    return value


class MailboxMessageResponse(BaseModel):
    message_id: str
    thread_id: str
    sender: str
    sender_name: str
    recipients: str
    subject: str
    sent_at: str
    snippet: str = ""
    direction: str
    unread: bool = False


class MailboxMessagePageResponse(BaseModel):
    messages: list[MailboxMessageResponse]
    next_page_token: str | None


class MailboxThreadResponse(BaseModel):
    messages: list[dict[str, str]]


class SendEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    connection_id: UUID
    recipient: str = Field(min_length=3, max_length=1024)
    cc: str = Field(default="", max_length=1024)
    bcc: str = Field(default="", max_length=1024)
    subject: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=1, max_length=8000)
    html_body: str | None = Field(default=None, max_length=40_000)
    attachments: list[EmailAttachment] = Field(default_factory=list, max_length=3)
    lead_id: UUID | None = None
    thread_id: str | None = Field(default=None, max_length=255)
    in_reply_to: str | None = Field(default=None, max_length=998)

    @field_validator("recipient")
    @classmethod
    def validate_recipient(cls, value: str) -> str:
        return _validate_address_list(value, required=True)

    @field_validator("cc", "bcc")
    @classmethod
    def validate_optional_recipients(cls, value: str) -> str:
        return _validate_address_list(value, required=False)

    @field_validator("subject", "in_reply_to")
    @classmethod
    def reject_header_breaks(cls, value: str | None) -> str | None:
        if value is not None and ("\r" in value or "\n" in value):
            raise ValueError("Email headers cannot contain line breaks")
        return value

    @field_validator("thread_id")
    @classmethod
    def validate_thread_id(cls, value: str | None) -> str | None:
        if value is not None and not _GMAIL_ID.fullmatch(value):
            raise ValueError("Invalid Gmail thread ID")
        return value


class SendEmailResponse(BaseModel):
    message_id: str
    thread_id: str | None
    sender: str
    recipient: str
    sent_at: datetime


class GmailSendAccountResponse(BaseModel):
    id: UUID
    account_name: str
    sender_email: str


@router.get("/send-accounts", response_model=list[GmailSendAccountResponse])
def list_gmail_send_accounts(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> list[GmailSendAccountResponse]:
    connections = session.scalars(
        select(ProviderConnectionORM)
        .where(
            ProviderConnectionORM.workspace_id == workspace.workspace_id,
            ProviderConnectionORM.provider_type == "gmail",
            ProviderConnectionORM.is_active.is_(True),
            ProviderConnectionORM.status == "verified",
        )
        .order_by(ProviderConnectionORM.account_name.asc())
    )
    return [
        GmailSendAccountResponse(
            id=connection.id,
            account_name=connection.account_name,
            sender_email=sender_email,
        )
        for connection in connections
        if GMAIL_SEND_SCOPE in set(connection.scopes or [])
        and _EMAIL_ADDRESS.fullmatch(
            sender_email := str(
                connection.connection_metadata.get("gmail_email")
                or connection.account_name
            ).strip().lower()
        )
    ]


def _gmail_connection(
    session: Session,
    workspace: WorkspaceContext,
    connection_id: UUID,
    *,
    required_scope: str,
) -> tuple[ProviderConnectionORM, str]:
    connection = session.scalar(
        select(ProviderConnectionORM).where(
            ProviderConnectionORM.id == connection_id,
            ProviderConnectionORM.workspace_id == workspace.workspace_id,
            ProviderConnectionORM.provider_type == "gmail",
            ProviderConnectionORM.is_active.is_(True),
            ProviderConnectionORM.status == "verified",
        )
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="Verified Gmail account not found")
    if required_scope not in connection.scopes:
        raise HTTPException(
            status_code=409,
            detail="This Gmail account needs to be reconnected with the required email permission.",
        )
    try:
        resolved = get_workspace_connection_resolver().resolve_connection(
            session,
            workspace.workspace_id,
            connection.id,
            actor_id=workspace.actor_id,
        )
    except ProviderOperationError as error:
        raise HTTPException(
            status_code=503 if error.retryable else 409,
            detail=str(error),
        ) from error
    if resolved is None or not resolved.credentials.get("access_token"):
        raise HTTPException(status_code=409, detail="Gmail account credentials are unavailable")
    metadata = connection.connection_metadata
    mailbox = str(metadata.get("gmail_email") or connection.account_name).strip().lower()
    if "@" not in mailbox:
        raise HTTPException(status_code=409, detail="Gmail account identity is unavailable")
    return connection, resolved.credentials["access_token"]


@router.get("/messages", response_model=MailboxMessagePageResponse)
def list_mailbox_messages(
    connection_id: UUID,
    folder: Literal["inbox", "sent", "all", "spam", "trash", "archive"] = "inbox",
    query: str = Query(default="", max_length=200),
    page_token: str | None = Query(default=None, max_length=1024),
    limit: int = Query(default=25, ge=1, le=50),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.view")),
) -> MailboxMessagePageResponse:
    connection, access_token = _gmail_connection(
        session, workspace, connection_id, required_scope=GMAIL_READONLY_SCOPE
    )
    mailbox = str(
        connection.connection_metadata.get("gmail_email") or connection.account_name
    ).strip().lower()
    provider = GmailInboundProvider(connection.id, access_token, mailbox)
    try:
        page = provider.list_mailbox_messages(
            folder=folder,
            query=query,
            page_token=page_token,
            limit=limit,
        )
    except ProviderOperationError as error:
        raise HTTPException(
            status_code=503 if error.retryable else 502,
            detail=str(error),
        ) from error
    return MailboxMessagePageResponse.model_validate(page)


@router.get("/thread", response_model=MailboxThreadResponse)
def get_mailbox_thread(
    connection_id: UUID,
    thread_id: str = Query(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_-]+$"),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.view")),
) -> MailboxThreadResponse:
    connection, access_token = _gmail_connection(
        session, workspace, connection_id, required_scope=GMAIL_READONLY_SCOPE
    )
    mailbox = str(
        connection.connection_metadata.get("gmail_email") or connection.account_name
    ).strip().lower()
    provider = GmailInboundProvider(connection.id, access_token, mailbox)
    try:
        messages = provider.fetch_mailbox_thread(thread_id)
    except ProviderOperationError as error:
        raise HTTPException(
            status_code=503 if error.retryable else 502,
            detail=str(error),
        ) from error
    return MailboxThreadResponse(messages=messages)


@router.post("/send", response_model=SendEmailResponse)
def send_email(
    request: SendEmailRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> SendEmailResponse:
    connection, access_token = _gmail_connection(
        session, workspace, request.connection_id, required_scope=GMAIL_SEND_SCOPE
    )
    mailbox = str(
        connection.connection_metadata.get("gmail_email") or connection.account_name
    ).strip().lower()
    lead: LeadORM | None = None
    if request.lead_id is not None:
        lead = session.scalar(
            select(LeadORM).where(
                LeadORM.id == request.lead_id,
                LeadORM.workspace_id == workspace.workspace_id,
            )
        )
        if lead is None:
            raise HTTPException(status_code=404, detail="Lead not found")

    provider = GmailOutboundProvider(connection.id, access_token, mailbox)
    decoded_attachments: list[tuple[str, str, bytes]] = []
    total_attachment_bytes = 0
    for attachment in request.attachments:
        try:
            content = base64.b64decode(attachment.content_base64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=422, detail="An attachment could not be decoded.") from None
        total_attachment_bytes += len(content)
        if total_attachment_bytes > _MAX_ATTACHMENT_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Attachments must total 10 MB or less.",
            )
        decoded_attachments.append((attachment.filename, attachment.content_type, content))
    try:
        result = provider.send_email_with_metadata(
            recipient=request.recipient,
            cc=request.cc,
            bcc=request.bcc,
            subject=request.subject,
            body=request.body,
            html_body=_sanitize_email_html(request.html_body),
            attachments=decoded_attachments,
            thread_id=request.thread_id,
            in_reply_to=request.in_reply_to,
        )
    except ProviderOperationError as error:
        raise HTTPException(
            status_code=503 if error.retryable else 502,
            detail=str(error),
        ) from error
    message_id = result.get("message_id")
    if not message_id:
        raise HTTPException(status_code=502, detail="Gmail did not confirm the sent message")
    thread_id = result.get("thread_id") or request.thread_id
    sent_at = datetime.now(UTC)
    if lead is not None:
        event = {
            "message_id": message_id,
            "thread_id": thread_id or "",
            "sender": mailbox,
            "sender_name": "",
            "recipients": ", ".join(
                value for value in (request.recipient, request.cc) if value
            ),
            "subject": request.subject,
            "sent_at": sent_at.isoformat(),
            "rfc_message_id": "",
            "direction": "outbound",
            "body": request.body,
        }
        append_gmail_thread_messages(
            session,
            workspace_id=workspace.workspace_id,
            lead_id=lead.id,
            connection_id=connection.id,
            messages=[event],
        )
        try:
            session.commit()
        except SQLAlchemyError as error:
            session.rollback()
            raise HTTPException(
                status_code=500,
                detail=(
                    "Gmail accepted the email, but Corral could not save its lead activity. "
                    "Check the Sent folder before trying again."
                ),
            ) from error
    return SendEmailResponse(
        message_id=message_id,
        thread_id=thread_id,
        sender=mailbox,
        recipient=request.recipient,
        sent_at=sent_at,
    )
