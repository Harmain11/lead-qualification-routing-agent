from __future__ import annotations

import re
import socket
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import PurePath
from typing import Any, Literal
from urllib.parse import urljoin, urlparse
from uuid import UUID, uuid4

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_runtime_settings, require_permission
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.communication_attachments import (
    delete_private_attachment,
    get_private_attachment,
    put_private_attachment,
)
from lead_agent.infrastructure.db.models import (
    CommunicationAttachmentORM,
    CommunicationConversationMemberORM,
    CommunicationConversationORM,
    CommunicationMessageLeadORM,
    CommunicationMessageMentionORM,
    CommunicationMessageORM,
    CommunicationMessagePinORM,
    CommunicationNotificationORM,
    CommunicationProjectORM,
    CommunicationReminderORM,
    LeadORM,
    QualificationResultORM,
    UserORM,
    WorkspaceMembershipORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.settings.config import Settings

router = APIRouter(prefix="/v1/communication", tags=["communication"])

ConversationKind = Literal["channel", "dm", "group_dm"]
AttachmentKind = Literal["file", "voice"]

_ALLOWED_ATTACHMENT_TYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/json",
        "text/plain",
        "text/csv",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
        "audio/aac",
        "audio/mpeg",
        "audio/mp4",
        "audio/ogg",
        "audio/wav",
        "audio/webm",
    }
)


class MemberSummary(BaseModel):
    id: UUID
    display_name: str
    email: str


class LeadReference(BaseModel):
    id: UUID
    full_name: str
    company_name: str
    qualification: str | None = None
    source: str


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    description: str
    is_archived: bool
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class ConversationResponse(BaseModel):
    id: UUID
    project_id: UUID | None
    kind: ConversationKind
    name: str | None
    description: str
    primary_lead: LeadReference | None = None
    is_archived: bool
    member_ids: list[UUID]
    updated_at: datetime


class MessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    parent_message_id: UUID | None
    author: MemberSummary
    body: dict[str, Any]
    plaintext: str
    mentioned_user_ids: list[UUID]
    leads: list[LeadReference]
    attachments: list[AttachmentResponse]
    is_pinned: bool
    edited_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime


class CommunicationBootstrapResponse(BaseModel):
    active_user_id: UUID
    projects: list[ProjectResponse]
    conversations: list[ConversationResponse]
    members: list[MemberSummary]


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4_000)


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: ConversationKind
    name: str | None = Field(default=None, max_length=120)
    description: str = Field(default="", max_length=4_000)
    project_id: UUID | None = None
    member_ids: list[UUID] = Field(default_factory=list, max_length=100)
    primary_lead_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def channel_name_required(cls, value: str | None, info: Any) -> str | None:
        if info.data.get("kind") == "channel" and not value:
            raise ValueError("a channel name is required")
        return value


class MessageCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: dict[str, Any] = Field(default_factory=dict)
    plaintext: str = Field(min_length=1, max_length=20_000)
    mentioned_user_ids: list[UUID] = Field(default_factory=list, max_length=100)
    lead_ids: list[UUID] = Field(default_factory=list, max_length=50)
    parent_message_id: UUID | None = None

    @field_validator("plaintext")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message text cannot be blank")
        return value


class MessageUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: dict[str, Any] = Field(default_factory=dict)
    plaintext: str = Field(min_length=1, max_length=20_000)


class ReminderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_at: datetime


class CommunicationNotificationResponse(BaseModel):
    id: UUID
    kind: str
    message_id: UUID
    read_at: datetime | None
    created_at: datetime


class LinkPreviewResponse(BaseModel):
    url: str
    domain: str
    title: str | None = None
    description: str | None = None
    image_url: str | None = None


class AttachmentResponse(BaseModel):
    id: UUID
    message_id: UUID
    kind: AttachmentKind
    filename: str
    content_type: str
    byte_size: int
    duration_ms: int | None = None
    created_at: datetime
    download_url: str


def _actor(workspace: WorkspaceContext) -> UUID:
    if workspace.user_id is None:
        raise HTTPException(status_code=403, detail="authenticated workspace actor required")
    return workspace.user_id


def _safe_preview_url(value: str) -> tuple[str, str]:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=422, detail="a public HTTP(S) URL is required")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise HTTPException(status_code=422, detail="local URLs cannot be previewed")
    try:
        addresses = {ip_address(hostname)}
    except ValueError:
        try:
            addresses = {
                ip_address(result[4][0])
                for result in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
            }
        except socket.gaierror as error:
            raise HTTPException(status_code=422, detail="link host cannot be resolved") from error
    if not addresses or any(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        for address in addresses
    ):
        raise HTTPException(status_code=422, detail="private URLs cannot be previewed")
    return parsed.geturl(), hostname


def _metadata(document: str, property_name: str) -> str | None:
    pattern = re.compile(
        rf"<meta[^>]+(?:property|name)=[\"']{re.escape(property_name)}[\"'][^>]+content=[\"']([^\"']+)",
        re.IGNORECASE,
    )
    match = pattern.search(document)
    return match.group(1).strip()[:500] if match else None


def _link_preview(url: str, domain: str) -> LinkPreviewResponse:
    try:
        with httpx.Client(timeout=3.0, follow_redirects=False) as client:
            response = client.get(url, headers={"User-Agent": "CorralLinkPreview/1.0"})
    except httpx.HTTPError as error:
        raise HTTPException(status_code=422, detail="link metadata is unavailable") from error
    if response.is_redirect:
        target = response.headers.get("location")
        if not target:
            raise HTTPException(status_code=422, detail="link preview redirect is invalid")
        redirect_url, redirect_domain = _safe_preview_url(urljoin(url, target))
        if redirect_domain != domain:
            raise HTTPException(
                status_code=422, detail="cross-domain redirects cannot be previewed"
            )
        return _link_preview(redirect_url, redirect_domain)
    if response.status_code >= 400 or "text/html" not in response.headers.get("content-type", ""):
        raise HTTPException(status_code=422, detail="link does not provide HTML metadata")
    document = response.text[:200_000]
    title = _metadata(document, "og:title") or _metadata(document, "twitter:title")
    description = _metadata(document, "og:description") or _metadata(document, "description")
    image_url = _metadata(document, "og:image")
    return LinkPreviewResponse(
        url=url, domain=domain, title=title, description=description, image_url=image_url
    )


def _members(session: Session, conversation_id: UUID) -> list[UUID]:
    return list(
        session.scalars(
            select(CommunicationConversationMemberORM.user_id).where(
                CommunicationConversationMemberORM.conversation_id == conversation_id
            )
        )
    )


def _member_rows(
    session: Session, workspace_id: UUID
) -> list[tuple[UserORM, WorkspaceMembershipORM]]:
    return list(
        session.execute(
            select(UserORM, WorkspaceMembershipORM)
            .join(WorkspaceMembershipORM, WorkspaceMembershipORM.user_id == UserORM.id)
            .where(
                WorkspaceMembershipORM.workspace_id == workspace_id,
                WorkspaceMembershipORM.status == "active",
                UserORM.status == "active",
            )
            .order_by(UserORM.display_name, UserORM.email)
        )
    )


def _lead_reference(
    session: Session, workspace_id: UUID, lead_id: UUID | None
) -> LeadReference | None:
    if lead_id is None:
        return None
    row = session.execute(
        select(LeadORM, QualificationResultORM)
        .outerjoin(QualificationResultORM, QualificationResultORM.lead_id == LeadORM.id)
        .where(LeadORM.id == lead_id, LeadORM.workspace_id == workspace_id)
    ).first()
    if row is None:
        return None
    lead, qualification = row
    return LeadReference(
        id=lead.id,
        full_name=lead.full_name,
        company_name=lead.company_name,
        qualification=qualification.classification if qualification else None,
        source=lead.source,
    )


def _conversation_response(
    session: Session, workspace_id: UUID, row: CommunicationConversationORM
) -> ConversationResponse:
    return ConversationResponse(
        id=row.id,
        project_id=row.project_id,
        kind=row.kind,
        name=row.name,
        description=row.description,
        primary_lead=_lead_reference(session, workspace_id, row.primary_lead_id),
        is_archived=row.is_archived,
        member_ids=_members(session, row.id),
        updated_at=row.updated_at,
    )


def _message_response(
    session: Session, workspace_id: UUID, row: CommunicationMessageORM
) -> MessageResponse:
    author = session.get(UserORM, row.author_id)
    if author is None:
        raise RuntimeError("communication message author is missing")
    mentioned = list(
        session.scalars(
            select(CommunicationMessageMentionORM.user_id).where(
                CommunicationMessageMentionORM.message_id == row.id
            )
        )
    )
    lead_ids = list(
        session.scalars(
            select(CommunicationMessageLeadORM.lead_id).where(
                CommunicationMessageLeadORM.message_id == row.id
            )
        )
    )
    attachments = list(
        session.scalars(
            select(CommunicationAttachmentORM)
            .where(CommunicationAttachmentORM.message_id == row.id)
            .order_by(CommunicationAttachmentORM.created_at)
        )
    )
    return MessageResponse(
        id=row.id,
        conversation_id=row.conversation_id,
        parent_message_id=row.parent_message_id,
        author=MemberSummary(id=author.id, display_name=author.display_name, email=author.email),
        body=row.body,
        plaintext=row.plaintext,
        mentioned_user_ids=mentioned,
        leads=[
            reference
            for lead_id in lead_ids
            if (reference := _lead_reference(session, workspace_id, lead_id))
        ],
        attachments=[_attachment_response(attachment) for attachment in attachments],
        is_pinned=session.scalar(
            select(CommunicationMessagePinORM.id).where(
                CommunicationMessagePinORM.message_id == row.id
            )
        )
        is not None,
        edited_at=row.edited_at,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
    )


def _attachment_response(row: CommunicationAttachmentORM) -> AttachmentResponse:
    return AttachmentResponse(
        id=row.id,
        message_id=row.message_id,
        kind=row.kind,
        filename=row.filename,
        content_type=row.content_type,
        byte_size=row.byte_size,
        duration_ms=row.duration_ms,
        created_at=row.created_at,
        download_url=f"/v1/communication/attachments/{row.id}",
    )


def _require_conversation_member(
    session: Session, workspace: WorkspaceContext, conversation_id: UUID
) -> CommunicationConversationORM:
    row = session.scalar(
        select(CommunicationConversationORM).where(
            CommunicationConversationORM.id == conversation_id,
            CommunicationConversationORM.workspace_id == workspace.workspace_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if (
        workspace.user_id is None
        or session.scalar(
            select(CommunicationConversationMemberORM.id).where(
                CommunicationConversationMemberORM.conversation_id == row.id,
                CommunicationConversationMemberORM.user_id == workspace.user_id,
            )
        )
        is None
    ):
        raise HTTPException(status_code=404, detail="conversation not found")
    return row


@router.get("/bootstrap", response_model=CommunicationBootstrapResponse)
def bootstrap(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("communication.view")),
) -> CommunicationBootstrapResponse:
    if workspace.user_id is None:
        raise HTTPException(status_code=403, detail="authenticated workspace actor required")
    conversations = list(
        session.scalars(
            select(CommunicationConversationORM)
            .join(CommunicationConversationMemberORM)
            .where(
                CommunicationConversationORM.workspace_id == workspace.workspace_id,
                CommunicationConversationMemberORM.user_id == workspace.user_id,
            )
            .order_by(CommunicationConversationORM.updated_at.desc())
        )
    )
    visible_project_ids = (
        select(CommunicationConversationORM.project_id)
        .join(
            CommunicationConversationMemberORM,
            CommunicationConversationMemberORM.conversation_id == CommunicationConversationORM.id,
        )
        .where(
            CommunicationConversationORM.workspace_id == workspace.workspace_id,
            CommunicationConversationMemberORM.user_id == workspace.user_id,
            CommunicationConversationORM.project_id.is_not(None),
        )
    )
    projects = list(
        session.scalars(
            select(CommunicationProjectORM)
            .where(
                CommunicationProjectORM.workspace_id == workspace.workspace_id,
                CommunicationProjectORM.is_archived.is_(False),
                (CommunicationProjectORM.created_by == workspace.user_id)
                | CommunicationProjectORM.id.in_(visible_project_ids),
            )
            .order_by(CommunicationProjectORM.updated_at.desc())
        )
    )
    return CommunicationBootstrapResponse(
        active_user_id=workspace.user_id,
        projects=[ProjectResponse.model_validate(row, from_attributes=True) for row in projects],
        conversations=[
            _conversation_response(session, workspace.workspace_id, row) for row in conversations
        ],
        members=[
            MemberSummary(id=user.id, display_name=user.display_name, email=user.email)
            for user, _ in _member_rows(session, workspace.workspace_id)
        ],
    )


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    request: ProjectCreateRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("communication.manage")),
) -> ProjectResponse:
    now = datetime.now(UTC)
    row = CommunicationProjectORM(
        id=uuid4(),
        workspace_id=workspace.workspace_id,
        name=request.name,
        description=request.description,
        created_by=_actor(workspace),
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return ProjectResponse.model_validate(row, from_attributes=True)


@router.post(
    "/conversations", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED
)
def create_conversation(
    request: ConversationCreateRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("communication.create")),
) -> ConversationResponse:
    actor_id = _actor(workspace)
    if (
        request.project_id is not None
        and session.scalar(
            select(CommunicationProjectORM.id).where(
                CommunicationProjectORM.id == request.project_id,
                CommunicationProjectORM.workspace_id == workspace.workspace_id,
                CommunicationProjectORM.is_archived.is_(False),
            )
        )
        is None
    ):
        raise HTTPException(status_code=422, detail="project not found")
    if (
        request.primary_lead_id is not None
        and _lead_reference(session, workspace.workspace_id, request.primary_lead_id) is None
    ):
        raise HTTPException(status_code=422, detail="lead not found")
    member_ids = set(request.member_ids)
    member_ids.add(actor_id)
    valid_ids = {user.id for user, _ in _member_rows(session, workspace.workspace_id)}
    if not member_ids.issubset(valid_ids):
        raise HTTPException(
            status_code=422, detail="all conversation members must be active workspace members"
        )
    if request.kind == "dm" and len(member_ids) != 2:
        raise HTTPException(status_code=422, detail="a direct message requires exactly two members")
    if request.kind == "group_dm" and len(member_ids) < 3:
        raise HTTPException(
            status_code=422, detail="a group direct message requires at least three members"
        )
    now = datetime.now(UTC)
    row = CommunicationConversationORM(
        id=uuid4(),
        workspace_id=workspace.workspace_id,
        project_id=request.project_id,
        kind=request.kind,
        name=request.name if request.kind == "channel" else None,
        description=request.description,
        primary_lead_id=request.primary_lead_id,
        created_by=actor_id,
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    session.add_all(
        [
            CommunicationConversationMemberORM(
                id=uuid4(),
                conversation_id=row.id,
                user_id=user_id,
                role="owner" if user_id == actor_id else "member",
                joined_at=now,
                last_read_at=now,
            )
            for user_id in member_ids
        ]
    )
    session.commit()
    return _conversation_response(session, workspace.workspace_id, row)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageResponse])
def list_messages(
    conversation_id: UUID,
    parent_message_id: UUID | None = Query(default=None),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("communication.view")),
) -> list[MessageResponse]:
    _require_conversation_member(session, workspace, conversation_id)
    rows = list(
        session.scalars(
            select(CommunicationMessageORM)
            .where(
                CommunicationMessageORM.conversation_id == conversation_id,
                CommunicationMessageORM.parent_message_id == parent_message_id,
            )
            .order_by(CommunicationMessageORM.created_at)
        )
    )
    return [_message_response(session, workspace.workspace_id, row) for row in rows]


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_message(
    conversation_id: UUID,
    request: MessageCreateRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("communication.create")),
) -> MessageResponse:
    conversation = _require_conversation_member(session, workspace, conversation_id)
    actor_id = _actor(workspace)
    if conversation.is_archived:
        raise HTTPException(status_code=409, detail="conversation is archived")
    if (
        request.parent_message_id is not None
        and session.scalar(
            select(CommunicationMessageORM.id).where(
                CommunicationMessageORM.id == request.parent_message_id,
                CommunicationMessageORM.conversation_id == conversation_id,
                CommunicationMessageORM.parent_message_id.is_(None),
            )
        )
        is None
    ):
        raise HTTPException(
            status_code=422, detail="thread parent must be a top-level message in this conversation"
        )
    member_ids = set(_members(session, conversation_id))
    if not set(request.mentioned_user_ids).issubset(member_ids):
        raise HTTPException(
            status_code=422, detail="mentioned users must belong to the conversation"
        )
    requested_lead_ids = set(request.lead_ids)
    existing_lead_ids = (
        set(
            session.scalars(
                select(LeadORM.id).where(
                    LeadORM.workspace_id == workspace.workspace_id,
                    LeadORM.id.in_(requested_lead_ids),
                )
            )
        )
        if requested_lead_ids
        else set()
    )
    if requested_lead_ids != existing_lead_ids:
        raise HTTPException(status_code=422, detail="one or more linked leads were not found")
    now = datetime.now(UTC)
    row = CommunicationMessageORM(
        id=uuid4(),
        workspace_id=workspace.workspace_id,
        conversation_id=conversation_id,
        parent_message_id=request.parent_message_id,
        author_id=actor_id,
        body=request.body,
        plaintext=request.plaintext,
        created_at=now,
    )
    session.add(row)
    session.flush()
    session.add_all(
        [
            CommunicationMessageMentionORM(
                id=uuid4(), message_id=row.id, user_id=user_id, created_at=now
            )
            for user_id in set(request.mentioned_user_ids)
            if user_id != actor_id
        ]
    )
    session.add_all(
        [
            CommunicationMessageLeadORM(
                id=uuid4(), message_id=row.id, lead_id=lead_id, created_at=now
            )
            for lead_id in requested_lead_ids
        ]
    )
    notified: set[tuple[UUID, str]] = {
        (user_id, "mention") for user_id in set(request.mentioned_user_ids) if user_id != actor_id
    }
    if conversation.kind in {"dm", "group_dm"}:
        notified.update((user_id, "dm") for user_id in member_ids if user_id != actor_id)
    if request.parent_message_id is not None:
        parent = session.get(CommunicationMessageORM, request.parent_message_id)
        if parent is not None and parent.author_id != actor_id:
            notified.add((parent.author_id, "thread_reply"))
    session.add_all(
        [
            CommunicationNotificationORM(
                id=uuid4(),
                workspace_id=workspace.workspace_id,
                recipient_id=recipient_id,
                message_id=row.id,
                kind=kind,
                created_at=now,
            )
            for recipient_id, kind in notified
        ]
    )
    conversation.updated_at = now
    session.commit()
    return _message_response(session, workspace.workspace_id, row)


@router.post(
    "/messages/{message_id}/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_attachment(
    message_id: UUID,
    file: UploadFile = File(...),
    kind: AttachmentKind = Form(default="file"),
    duration_ms: int | None = Form(default=None),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("communication.attach")),
    settings: Settings = Depends(get_runtime_settings),
) -> AttachmentResponse:
    message = session.scalar(
        select(CommunicationMessageORM).where(
            CommunicationMessageORM.id == message_id,
            CommunicationMessageORM.workspace_id == workspace.workspace_id,
            CommunicationMessageORM.deleted_at.is_(None),
        )
    )
    if message is None:
        raise HTTPException(status_code=404, detail="message not found")
    _require_conversation_member(session, workspace, message.conversation_id)
    if (
        message.author_id != _actor(workspace)
        and "communication.moderate" not in workspace.permissions
    ):
        raise HTTPException(status_code=403, detail="only the author may add an attachment")
    filename = PurePath(file.filename or "attachment").name[:255]
    content_type = (file.content_type or "application/octet-stream").split(";", 1)[0].lower()
    if content_type not in _ALLOWED_ATTACHMENT_TYPES:
        raise HTTPException(status_code=422, detail="this attachment type is not allowed")
    if kind == "voice" and not content_type.startswith("audio/"):
        raise HTTPException(status_code=422, detail="voice messages must be audio files")
    if kind == "voice" and (duration_ms is None or not 1 <= duration_ms <= 10 * 60 * 1000):
        raise HTTPException(
            status_code=422, detail="voice messages must be between 1 ms and 10 minutes"
        )
    if kind == "file" and duration_ms is not None:
        raise HTTPException(status_code=422, detail="only voice messages may include a duration")
    content = file.file.read(settings.communication_attachment_max_bytes + 1)
    if not content:
        raise HTTPException(status_code=422, detail="attachments cannot be empty")
    if len(content) > settings.communication_attachment_max_bytes:
        raise HTTPException(status_code=413, detail="attachment exceeds the workspace size limit")
    extension = PurePath(filename).suffix.lower()
    extension = extension if extension and extension[1:].isalnum() and len(extension) <= 12 else ""
    storage_key = f"communication/{workspace.workspace_id}/{message.id}/{uuid4().hex}{extension}"
    try:
        stored_key = put_private_attachment(storage_key, content, content_type)
    except Exception as error:
        raise HTTPException(status_code=503, detail="attachment storage is unavailable") from error
    attachment = CommunicationAttachmentORM(
        id=uuid4(),
        workspace_id=workspace.workspace_id,
        message_id=message.id,
        uploaded_by=_actor(workspace),
        kind=kind,
        filename=filename,
        content_type=content_type,
        byte_size=len(content),
        storage_key=stored_key,
        duration_ms=duration_ms,
        created_at=datetime.now(UTC),
    )
    try:
        session.add(attachment)
        session.commit()
    except Exception:
        session.rollback()
        delete_private_attachment(stored_key)
        raise
    return _attachment_response(attachment)


@router.get("/attachments/{attachment_id}")
def download_attachment(
    attachment_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("communication.view")),
) -> Response:
    attachment = session.scalar(
        select(CommunicationAttachmentORM).where(
            CommunicationAttachmentORM.id == attachment_id,
            CommunicationAttachmentORM.workspace_id == workspace.workspace_id,
        )
    )
    if attachment is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    message = session.get(CommunicationMessageORM, attachment.message_id)
    if message is None or message.deleted_at is not None:
        raise HTTPException(status_code=404, detail="attachment not found")
    _require_conversation_member(session, workspace, message.conversation_id)
    try:
        content = get_private_attachment(attachment.storage_key)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="attachment not found") from error
    except Exception as error:
        raise HTTPException(status_code=503, detail="attachment storage is unavailable") from error
    safe_filename = attachment.filename.replace('"', "_").replace("\\", "_")
    return Response(
        content=content,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{safe_filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(
    attachment_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("communication.attach")),
) -> None:
    attachment = session.scalar(
        select(CommunicationAttachmentORM).where(
            CommunicationAttachmentORM.id == attachment_id,
            CommunicationAttachmentORM.workspace_id == workspace.workspace_id,
        )
    )
    if attachment is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    message = session.get(CommunicationMessageORM, attachment.message_id)
    if message is None or message.deleted_at is not None:
        raise HTTPException(status_code=404, detail="attachment not found")
    _require_conversation_member(session, workspace, message.conversation_id)
    if (
        message.author_id != _actor(workspace)
        and "communication.moderate" not in workspace.permissions
    ):
        raise HTTPException(status_code=403, detail="only the author may delete an attachment")
    try:
        delete_private_attachment(attachment.storage_key)
    except Exception as error:
        raise HTTPException(status_code=503, detail="attachment storage is unavailable") from error
    session.delete(attachment)
    session.commit()


@router.post("/messages/{message_id}/pin", response_model=MessageResponse)
def pin_message(
    message_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("communication.create")),
) -> MessageResponse:
    row = session.scalar(
        select(CommunicationMessageORM).where(
            CommunicationMessageORM.id == message_id,
            CommunicationMessageORM.workspace_id == workspace.workspace_id,
            CommunicationMessageORM.deleted_at.is_(None),
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="message not found")
    _require_conversation_member(session, workspace, row.conversation_id)
    existing = session.scalar(
        select(CommunicationMessagePinORM).where(CommunicationMessagePinORM.message_id == row.id)
    )
    if existing is None:
        session.add(
            CommunicationMessagePinORM(
                id=uuid4(),
                conversation_id=row.conversation_id,
                message_id=row.id,
                pinned_by=_actor(workspace),
                created_at=datetime.now(UTC),
            )
        )
        session.commit()
    return _message_response(session, workspace.workspace_id, row)


@router.delete("/messages/{message_id}/pin", status_code=status.HTTP_204_NO_CONTENT)
def unpin_message(
    message_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("communication.create")),
) -> None:
    row = session.scalar(
        select(CommunicationMessageORM).where(
            CommunicationMessageORM.id == message_id,
            CommunicationMessageORM.workspace_id == workspace.workspace_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="message not found")
    _require_conversation_member(session, workspace, row.conversation_id)
    session.execute(
        delete(CommunicationMessagePinORM).where(CommunicationMessagePinORM.message_id == row.id)
    )
    session.commit()


@router.patch("/messages/{message_id}", response_model=MessageResponse)
def update_message(
    message_id: UUID,
    request: MessageUpdateRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("communication.create")),
) -> MessageResponse:
    row = session.scalar(
        select(CommunicationMessageORM).where(
            CommunicationMessageORM.id == message_id,
            CommunicationMessageORM.workspace_id == workspace.workspace_id,
            CommunicationMessageORM.deleted_at.is_(None),
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="message not found")
    _require_conversation_member(session, workspace, row.conversation_id)
    if row.author_id != _actor(workspace) and "communication.moderate" not in workspace.permissions:
        raise HTTPException(status_code=403, detail="only the author may edit this message")
    row.plaintext = request.plaintext.strip()
    row.body = request.body
    row.edited_at = datetime.now(UTC)
    session.commit()
    return _message_response(session, workspace.workspace_id, row)


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_message(
    message_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("communication.create")),
) -> None:
    row = session.scalar(
        select(CommunicationMessageORM).where(
            CommunicationMessageORM.id == message_id,
            CommunicationMessageORM.workspace_id == workspace.workspace_id,
            CommunicationMessageORM.deleted_at.is_(None),
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="message not found")
    _require_conversation_member(session, workspace, row.conversation_id)
    if row.author_id != _actor(workspace) and "communication.moderate" not in workspace.permissions:
        raise HTTPException(status_code=403, detail="only the author may delete this message")
    attachments = list(
        session.scalars(
            select(CommunicationAttachmentORM).where(
                CommunicationAttachmentORM.message_id == row.id
            )
        )
    )
    try:
        for attachment in attachments:
            delete_private_attachment(attachment.storage_key)
    except Exception as error:
        raise HTTPException(status_code=503, detail="attachment storage is unavailable") from error
    for attachment in attachments:
        session.delete(attachment)
    row.plaintext = "This message was deleted."
    row.body = {"version": 1, "type": "deleted"}
    row.deleted_at = datetime.now(UTC)
    session.commit()


@router.post("/messages/{message_id}/reminders", status_code=status.HTTP_201_CREATED)
def create_reminder(
    message_id: UUID,
    request: ReminderCreateRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("communication.view")),
) -> dict[str, UUID]:
    row = session.scalar(
        select(CommunicationMessageORM).where(
            CommunicationMessageORM.id == message_id,
            CommunicationMessageORM.workspace_id == workspace.workspace_id,
            CommunicationMessageORM.deleted_at.is_(None),
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="message not found")
    _require_conversation_member(session, workspace, row.conversation_id)
    if request.due_at <= datetime.now(UTC):
        raise HTTPException(status_code=422, detail="reminder must be scheduled in the future")
    reminder = CommunicationReminderORM(
        id=uuid4(),
        workspace_id=workspace.workspace_id,
        message_id=row.id,
        recipient_id=_actor(workspace),
        due_at=request.due_at,
        created_at=datetime.now(UTC),
    )
    session.add(reminder)
    session.commit()
    return {"id": reminder.id}


@router.get("/notifications", response_model=list[CommunicationNotificationResponse])
def list_notifications(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("communication.view")),
) -> list[CommunicationNotificationResponse]:
    actor_id = _actor(workspace)
    rows = session.scalars(
        select(CommunicationNotificationORM)
        .where(
            CommunicationNotificationORM.workspace_id == workspace.workspace_id,
            CommunicationNotificationORM.recipient_id == actor_id,
        )
        .order_by(CommunicationNotificationORM.created_at.desc())
        .limit(100)
    )
    return [
        CommunicationNotificationResponse.model_validate(row, from_attributes=True) for row in rows
    ]


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_notification_read(
    notification_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("communication.view")),
) -> None:
    row = session.scalar(
        select(CommunicationNotificationORM).where(
            CommunicationNotificationORM.id == notification_id,
            CommunicationNotificationORM.workspace_id == workspace.workspace_id,
            CommunicationNotificationORM.recipient_id == _actor(workspace),
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="notification not found")
    row.read_at = datetime.now(UTC)
    session.commit()


@router.get("/search", response_model=list[MessageResponse])
def search_messages(
    query: str = Query(min_length=2, max_length=200),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("communication.search")),
) -> list[MessageResponse]:
    if workspace.user_id is None:
        raise HTTPException(status_code=403, detail="authenticated workspace actor required")
    pattern = f"%{query.strip()}%"
    rows = list(
        session.scalars(
            select(CommunicationMessageORM)
            .join(
                CommunicationConversationMemberORM,
                and_(
                    CommunicationConversationMemberORM.conversation_id
                    == CommunicationMessageORM.conversation_id,
                    CommunicationConversationMemberORM.user_id == workspace.user_id,
                ),
            )
            .where(
                CommunicationMessageORM.workspace_id == workspace.workspace_id,
                CommunicationMessageORM.deleted_at.is_(None),
                CommunicationMessageORM.plaintext.ilike(pattern),
            )
            .order_by(CommunicationMessageORM.created_at.desc())
            .limit(50)
        )
    )
    return [_message_response(session, workspace.workspace_id, row) for row in rows]


@router.get("/link-preview", response_model=LinkPreviewResponse)
def link_preview(
    url: str = Query(min_length=8, max_length=2_048),
    _: WorkspaceContext = Depends(require_permission("communication.view")),
) -> LinkPreviewResponse:
    safe_url, domain = _safe_preview_url(url)
    return _link_preview(safe_url, domain)
