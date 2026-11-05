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
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission
from lead_agent.application.claims import guard_outgoing_email
from lead_agent.application.email_templates import apply_workspace_email_signature
from lead_agent.application.gmail_history import append_gmail_thread_messages
from lead_agent.application.gmail_threading import matches_lead_thread
from lead_agent.application.mailbox_access import can_use_mailbox
from lead_agent.application.governance import operations_policy
from lead_agent.domain.follow_up import FollowUpEmailContent
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import (
    LeadORM,
    PipelineEventORM,
    ProviderConnectionORM,
    WorkspaceEmailDraftORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.gmail import (
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    GmailInboundProvider,
    GmailOutboundProvider,
)
from lead_agent.infrastructure.provider_lifecycle import get_workspace_connection_resolver
from lead_agent.infrastructure.reasoning.factory import resolve_workspace_reasoning_provider
from lead_agent.domain.meeting_status import VERIFIED_MEETING_STATUSES

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


class MailboxAttachmentResponse(BaseModel):
    attachment_id: str
    filename: str
    mime_type: str
    size: int = 0


class MailboxThreadMessageResponse(BaseModel):
    message_id: str
    thread_id: str
    sender: str
    sender_name: str
    reply_to: str = ""
    recipients: str
    subject: str
    sent_at: str
    rfc_message_id: str = ""
    direction: str
    body: str = ""
    attachments: list[MailboxAttachmentResponse] = Field(default_factory=list)


class MailboxThreadResponse(BaseModel):
    messages: list[MailboxThreadMessageResponse]


class ComposeWithAIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    connection_id: UUID | None = None
    lead_id: UUID | None = None
    thread_id: str | None = Field(default=None, max_length=255)
    in_reply_to: str | None = Field(default=None, max_length=998)
    recipient: str = Field(default="", max_length=1024)
    subject: str = Field(default="", max_length=180)
    current_body: str = Field(default="", max_length=8000)
    instruction: str = Field(default="", max_length=1000)

    @field_validator("thread_id")
    @classmethod
    def validate_thread_id(cls, value: str | None) -> str | None:
        if value is not None and not _GMAIL_ID.fullmatch(value):
            raise ValueError("Invalid Gmail thread ID")
        return value

    @field_validator("in_reply_to")
    @classmethod
    def validate_reply_reference(cls, value: str | None) -> str | None:
        if value is not None and ("\r" in value or "\n" in value):
            raise ValueError("Invalid Gmail message reference")
        return value


class ComposeWithAIResponse(BaseModel):
    subject: str
    body: str


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
    draft_id: UUID | None = None

    @model_validator(mode="after")
    def require_reply_reference(self) -> "SendEmailRequest":
        if self.thread_id and not self.in_reply_to:
            raise ValueError("Refresh the Gmail conversation before replying so the message stays in its thread")
        return self

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


class SaveEmailDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    connection_id: UUID | None = None
    recipient: str = Field(default="", max_length=1024)
    cc: str = Field(default="", max_length=1024)
    bcc: str = Field(default="", max_length=1024)
    subject: str = Field(default="", max_length=180)
    body: str = Field(default="", max_length=8000)
    html_body: str | None = Field(default=None, max_length=40_000)
    attachments: list[EmailAttachment] = Field(default_factory=list, max_length=3)
    thread_id: str | None = Field(default=None, max_length=255)
    in_reply_to: str | None = Field(default=None, max_length=998)
    lead_id: UUID | None = None
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("recipient", "cc", "bcc")
    @classmethod
    def validate_addresses(cls, value: str) -> str:
        return _validate_address_list(value, required=False)

    @field_validator("subject", "in_reply_to")
    @classmethod
    def reject_draft_header_breaks(cls, value: str | None) -> str | None:
        if value is not None and ("\r" in value or "\n" in value):
            raise ValueError("Email headers cannot contain line breaks")
        return value

    @field_validator("thread_id")
    @classmethod
    def validate_draft_thread_id(cls, value: str | None) -> str | None:
        if value is not None and not _GMAIL_ID.fullmatch(value):
            raise ValueError("Invalid Gmail thread ID")
        return value


class EmailDraftResponse(BaseModel):
    id: UUID
    version: int
    data: dict
    created_at: datetime
    updated_at: datetime


class EmailDraftSummary(BaseModel):
    id: UUID
    version: int
    subject: str
    recipient: str
    updated_at: datetime


def _email_draft_response(row: WorkspaceEmailDraftORM) -> EmailDraftResponse:
    return EmailDraftResponse(
        id=row.id, version=row.version, data=row.data,
        created_at=row.created_at, updated_at=row.updated_at,
    )


def _owned_email_draft(session: Session, workspace: WorkspaceContext, draft_id: UUID) -> WorkspaceEmailDraftORM:
    row = session.scalar(select(WorkspaceEmailDraftORM).where(
        WorkspaceEmailDraftORM.id == draft_id,
        WorkspaceEmailDraftORM.workspace_id == workspace.workspace_id,
        WorkspaceEmailDraftORM.created_by == str(workspace.actor_id),
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    return row


def _draft_payload(request: SaveEmailDraftRequest) -> dict:
    total_bytes = 0
    for attachment in request.attachments:
        try:
            total_bytes += len(base64.b64decode(attachment.content_base64, validate=True))
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=422, detail="An attachment could not be decoded.") from None
    if total_bytes > _MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="Attachments must total 10 MB or less.")
    return request.model_dump(mode="json", exclude={"expected_version"}) | {
        "html_body": _sanitize_email_html(request.html_body),
    }


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
        if can_use_mailbox(workspace, connection)
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
    if required_scope == GMAIL_READONLY_SCOPE and "integrations.view" not in workspace.permissions:
        raise HTTPException(403, "permission required: integrations.view")
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
    if not can_use_mailbox(workspace, connection):
        raise HTTPException(403, "This mailbox belongs to another workspace member")
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
    folder: Literal["inbox", "sent", "all", "drafts", "spam", "trash", "archive"] = "inbox",
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


@router.get("/attachment")
def get_mailbox_attachment(
    connection_id: UUID,
    message_id: str = Query(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_-]+$"),
    attachment_id: str = Query(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_-]+$"),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("integrations.view")),
) -> Response:
    connection, access_token = _gmail_connection(
        session, workspace, connection_id, required_scope=GMAIL_READONLY_SCOPE
    )
    mailbox = str(
        connection.connection_metadata.get("gmail_email") or connection.account_name
    ).strip().lower()
    try:
        content, filename, mime_type = GmailInboundProvider(
            connection.id, access_token, mailbox
        ).fetch_mailbox_attachment(message_id, attachment_id)
    except ProviderOperationError as error:
        raise HTTPException(
            status_code=503 if error.retryable else 502,
            detail=str(error),
        ) from error
    safe_type = mime_type.lower() if re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", mime_type.lower()) else "application/octet-stream"
    safe_name = re.sub(r"[\\\r\n\"]+", "", filename) or "attachment"
    disposition = "inline" if safe_type.startswith("image/") else "attachment"
    return Response(
        content=content,
        media_type=safe_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{safe_name}"',
            "Cache-Control": "private, max-age=300",
        },
    )


@router.post("/compose-ai", response_model=ComposeWithAIResponse)
def compose_email_with_ai(
    request: ComposeWithAIRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> ComposeWithAIResponse:
    lead = None
    if request.lead_id:
        lead = session.scalar(
            select(LeadORM).where(
                LeadORM.id == request.lead_id,
                LeadORM.workspace_id == workspace.workspace_id,
            )
        )
        if lead is None:
            raise HTTPException(status_code=404, detail="Lead not found")
    if not any(
        (request.thread_id, lead, request.subject, request.current_body, request.instruction)
    ):
        raise HTTPException(
            status_code=422, detail="Add a subject or describe what the email should say."
        )

    conversation: list[dict[str, str]] = []
    if request.thread_id:
        if not request.connection_id:
            raise HTTPException(status_code=409, detail="Select the Gmail account for this thread.")
        connection, access_token = _gmail_connection(
            session,
            workspace,
            request.connection_id,
            required_scope=GMAIL_READONLY_SCOPE,
        )
        mailbox = (
            str(connection.connection_metadata.get("gmail_email") or connection.account_name)
            .strip()
            .lower()
        )
        try:
            thread = GmailInboundProvider(
                connection.id, access_token, mailbox
            ).fetch_mailbox_thread(request.thread_id)
        except ProviderOperationError as error:
            raise HTTPException(
                status_code=503 if error.retryable else 502, detail=str(error)
            ) from error
        recipient_addresses = {
            address.lower() for _name, address in getaddresses([request.recipient]) if address
        }
        if lead:
            recipient_addresses.add(lead.email.lower())
        if not recipient_addresses:
            raise HTTPException(
                status_code=422, detail="Choose a recipient before composing a reply."
            )
        conversation = [
            item
            for item in thread
            if item.get("sender", "").lower() in recipient_addresses
            or any(
                address.lower() in recipient_addresses
                for _name, address in getaddresses([item.get("recipients", "")])
            )
        ]
        if not conversation:
            raise HTTPException(
                status_code=409,
                detail="This Gmail thread does not match the recipient. Refresh before drafting.",
            )
        if request.in_reply_to and not any(
            item.get("rfc_message_id") == request.in_reply_to for item in conversation
        ):
            raise HTTPException(
                status_code=409,
                detail="The selected message is no longer in this Gmail thread. Refresh before drafting.",
            )
    elif lead:
        events = session.scalars(
            select(PipelineEventORM)
            .where(
                PipelineEventORM.workspace_id == workspace.workspace_id,
                PipelineEventORM.lead_id == lead.id,
                PipelineEventORM.stage == "gmail_message",
            )
            .order_by(PipelineEventORM.created_at.desc())
            .limit(12)
        ).all()
        conversation = [
            {
                "direction": str(event.details.get("direction") or ""),
                "subject": str(event.details.get("subject") or "")[:180],
                "body": str(event.details.get("body") or "")[:2000],
                "sent_at": event.created_at.isoformat(),
            }
            for event in reversed(events)
            if isinstance(event.details, dict)
        ]

    profile = operations_policy(session, workspace.workspace_id).business_profile
    facts = {
        "email_purpose": "compose",
        "lead_name": lead.full_name if lead else "",
        "company": lead.company_name if lead else "",
        "industry": lead.industry if lead else "",
        "requested_product_or_service": lead.product_or_service if lead else "",
        "original_message": lead.message[:1600] if lead else "",
        "recipient": request.recipient,
        "subject": request.subject,
        "current_datetime_utc": datetime.now(UTC).isoformat(),
        "existing_draft": request.current_body,
        "draft_instruction": request.instruction,
        "recent_activity": conversation[-12:],
        "reply_target_message": next(
            (item for item in conversation if item.get("rfc_message_id") == request.in_reply_to),
            {},
        ) if request.in_reply_to else {},
        "workspace_business_profile": profile.model_dump(mode="json"),
    }
    provider = resolve_workspace_reasoning_provider(session, workspace.workspace_id)
    draft_method = getattr(provider, "draft_follow_up", None)
    if not callable(draft_method):
        raise HTTPException(status_code=409, detail="Connect and verify OpenAI to compose with AI.")
    try:
        generated = FollowUpEmailContent.model_validate(draft_method(facts))
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="AI drafting did not complete. Check the OpenAI connection and retry.",
        ) from error
    if request.current_body:
        protected_lines = [
            line.strip()
            for line in request.current_body.splitlines()
            if re.match(r"^[1-5][.)]\s", line.strip())
        ]
        if protected_lines and any(line not in generated.body for line in protected_lines):
            raise HTTPException(
                status_code=409,
                detail="AI could not preserve the meeting options. The current draft was kept.",
            )
    meeting_verified = lead is not None and (
        session.scalar(
            select(PipelineEventORM.id).where(
                PipelineEventORM.workspace_id == workspace.workspace_id,
                PipelineEventORM.lead_id == lead.id,
                PipelineEventORM.stage == "meeting_scheduled",
                PipelineEventORM.status.in_(VERIFIED_MEETING_STATUSES),
            )
        )
        is not None
    )
    guarded = guard_outgoing_email(
        generated.subject, generated.body, meeting_verified=meeting_verified
    )
    subject = request.subject if request.thread_id else guarded.subject
    return ComposeWithAIResponse(
        subject=subject or "",
        body=apply_workspace_email_signature(guarded.body or generated.body, profile),
    )


@router.get("/drafts", response_model=list[EmailDraftSummary])
def list_email_drafts(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> list[EmailDraftSummary]:
    rows = session.scalars(
        select(WorkspaceEmailDraftORM).where(
            WorkspaceEmailDraftORM.workspace_id == workspace.workspace_id,
            WorkspaceEmailDraftORM.created_by == str(workspace.actor_id),
        ).order_by(WorkspaceEmailDraftORM.updated_at.desc()).limit(100)
    ).all()
    return [EmailDraftSummary(
        id=row.id, version=row.version,
        subject=str(row.data.get("subject") or ""),
        recipient=str(row.data.get("recipient") or ""),
        updated_at=row.updated_at,
    ) for row in rows]


@router.get("/drafts/{draft_id}", response_model=EmailDraftResponse)
def get_email_draft(
    draft_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> EmailDraftResponse:
    return _email_draft_response(_owned_email_draft(session, workspace, draft_id))


@router.post("/drafts", response_model=EmailDraftResponse, status_code=201)
def create_email_draft(
    request: SaveEmailDraftRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> EmailDraftResponse:
    now = datetime.now(UTC)
    row = WorkspaceEmailDraftORM(
        id=uuid4(), workspace_id=workspace.workspace_id,
        created_by=str(workspace.actor_id), version=1,
        data=_draft_payload(request), created_at=now, updated_at=now,
    )
    session.add(row)
    session.commit()
    return _email_draft_response(row)


@router.put("/drafts/{draft_id}", response_model=EmailDraftResponse)
def update_email_draft(
    draft_id: UUID,
    request: SaveEmailDraftRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> EmailDraftResponse:
    row = _owned_email_draft(session, workspace, draft_id)
    if request.expected_version != row.version:
        raise HTTPException(status_code=409, detail="This draft changed elsewhere. Reopen it before saving.")
    row.data = _draft_payload(request)
    row.version += 1
    row.updated_at = datetime.now(UTC)
    session.commit()
    return _email_draft_response(row)


@router.delete("/drafts/{draft_id}", status_code=204)
def delete_email_draft(
    draft_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.edit")),
) -> None:
    row = _owned_email_draft(session, workspace, draft_id)
    session.delete(row)
    session.commit()


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
    if lead is not None and request.thread_id:
        messages = session.scalars(select(PipelineEventORM).where(
            PipelineEventORM.workspace_id == workspace.workspace_id,
            PipelineEventORM.lead_id == lead.id,
            PipelineEventORM.stage == "gmail_message",
        )).all()
        if not matches_lead_thread(
            (event.details for event in messages if isinstance(event.details, dict)),
            connection_id=str(connection.id),
            thread_id=request.thread_id,
            in_reply_to=request.in_reply_to or "",
            subject=request.subject,
        ):
            raise HTTPException(
                status_code=409,
                detail="This reply no longer matches the lead’s Gmail thread. Refresh its history and reply again.",
            )
    draft = _owned_email_draft(session, workspace, request.draft_id) if request.draft_id else None

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
    if draft is not None:
        session.delete(draft)
    if lead is not None or draft is not None:
        try:
            session.commit()
        except SQLAlchemyError as error:
            session.rollback()
            raise HTTPException(
                status_code=500,
                detail=(
                    "Gmail accepted the email, but Corral could not save its activity. "
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
