from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.datastructures import Headers, UploadFile

from lead_agent.api.routes.communication import (
    ConversationCreateRequest,
    MessageCreateRequest,
    ProjectCreateRequest,
    _link_preview,
    _safe_preview_url,
    bootstrap,
    create_conversation,
    create_message,
    create_project,
    pin_message,
    search_messages,
    upload_attachment,
)
from lead_agent.domain.rbac import ROLE_PERMISSIONS
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    CommunicationNotificationORM,
    CommunicationReminderORM,
    LeadORM,
    UserORM,
    WorkspaceMembershipORM,
    WorkspaceORM,
)
from lead_agent.settings.config import Settings


def test_all_standard_workspace_roles_can_participate_in_communication() -> None:
    for role in ("owner", "admin", "sales_manager", "sales_rep", "viewer"):
        assert {"communication.view", "communication.create", "communication.attach"} <= (
            ROLE_PERMISSIONS[role]
        )


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/", "http://localhost/"])
def test_link_previews_reject_local_and_metadata_addresses(url: str) -> None:
    with pytest.raises(HTTPException) as error:
        _safe_preview_url(url)
    assert error.value.status_code == 422


def test_link_preview_stops_redirect_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    class RedirectClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> RedirectClient:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def get(self, url: httpx.URL, **kwargs: object) -> httpx.Response:
            return httpx.Response(
                302, headers={"location": "/loop"}, request=httpx.Request("GET", url)
            )

    monkeypatch.setattr("lead_agent.api.routes.communication.httpx.Client", RedirectClient)
    monkeypatch.setattr(
        "lead_agent.api.routes.communication._preview_address", lambda _: "93.184.216.34"
    )
    monkeypatch.setattr(
        "lead_agent.api.routes.communication._safe_preview_url", lambda url: (url, "example.com")
    )
    with pytest.raises(HTTPException, match="too many link preview redirects"):
        _link_preview("https://example.com/loop", "example.com")


def test_link_preview_connects_to_validated_ip_with_original_tls_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class Client:
        def __init__(self, **kwargs: object) -> None:
            observed["options"] = kwargs

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def get(self, url: httpx.URL, **kwargs: object) -> httpx.Response:
            observed.update(url=url, **kwargs)
            return httpx.Response(
                200, headers={"content-type": "text/html"}, text="<title>Example</title>",
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr("lead_agent.api.routes.communication.httpx.Client", Client)
    monkeypatch.setattr(
        "lead_agent.api.routes.communication._preview_address", lambda _: "93.184.216.34"
    )
    result = _link_preview("https://example.com/path", "example.com")
    assert str(observed["url"]) == "https://93.184.216.34/path"
    assert observed["headers"]["Host"] == "example.com"
    assert observed["extensions"]["sni_hostname"] == "example.com"
    assert observed["options"]["trust_env"] is False
    assert result.url == "https://example.com/path"


def _seed(session: Session) -> tuple[WorkspaceORM, UserORM, UserORM, LeadORM]:
    now = datetime.now(UTC)
    workspace = WorkspaceORM(
        id=uuid4(),
        name="Communication",
        slug=f"communication-{uuid4().hex}",
        status="active",
        is_demo=False,
        created_at=now,
        updated_at=now,
    )
    owner = UserORM(
        id=uuid4(),
        email="owner@communication.test",
        display_name="Workspace Owner",
        password_hash="test",
        status="active",
        created_at=now,
        updated_at=now,
    )
    teammate = UserORM(
        id=uuid4(),
        email="teammate@communication.test",
        display_name="Teammate",
        password_hash="test",
        status="active",
        created_at=now,
        updated_at=now,
    )
    lead = LeadORM(
        id=uuid4(),
        workspace_id=workspace.id,
        source="website",
        source_detail=None,
        external_id="communication-lead",
        lead_number=1,
        full_name="Amina Noor",
        email="amina@example.test",
        phone=None,
        job_title=None,
        company_name="Northstar",
        industry=None,
        employee_count=None,
        annual_revenue=None,
        geography=None,
        budget=None,
        product_or_service="Qualification",
        message="Please contact us.",
        received_at=now,
        raw_payload={},
        normalization_version="test",
        created_at=now,
        updated_at=now,
    )
    session.add_all(
        [
            workspace,
            owner,
            teammate,
            lead,
            WorkspaceMembershipORM(
                id=uuid4(),
                workspace_id=workspace.id,
                user_id=owner.id,
                role="owner",
                status="active",
                created_at=now,
                updated_at=now,
            ),
            WorkspaceMembershipORM(
                id=uuid4(),
                workspace_id=workspace.id,
                user_id=teammate.id,
                role="sales_rep",
                status="active",
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    session.commit()
    return workspace, owner, teammate, lead


def test_communication_message_mentions_and_lead_references_are_workspace_scoped() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        workspace, owner, teammate, lead = _seed(session)
        context = WorkspaceContext(
            workspace_id=workspace.id,
            actor_id=str(owner.id),
            user_id=owner.id,
            role="owner",
            permissions=frozenset(
                {
                    "communication.view",
                    "communication.create",
                    "communication.manage",
                    "communication.search",
                    "communication.attach",
                }
            ),
        )
        conversation = create_conversation(
            ConversationCreateRequest(
                kind="channel",
                name="deal-desk",
                member_ids=[teammate.id],
                primary_lead_id=lead.id,
            ),
            session,
            context,
        )
        assert conversation.primary_lead is not None
        assert conversation.primary_lead.id == lead.id
        message = create_message(
            conversation.id,
            MessageCreateRequest(
                plaintext="@Teammate please review this lead",
                mentioned_user_ids=[teammate.id],
                lead_ids=[lead.id],
            ),
            session,
            context,
        )
        assert message.mentioned_user_ids == [teammate.id]
        assert message.leads[0].full_name == "Amina Noor"
        notification = session.scalar(select(CommunicationNotificationORM).where(
            CommunicationNotificationORM.message_id == message.id,
            CommunicationNotificationORM.recipient_id == teammate.id,
            CommunicationNotificationORM.kind == "mention",
        ))
        assert notification is not None
        teammate_context = WorkspaceContext(
            workspace_id=workspace.id, actor_id=str(teammate.id), user_id=teammate.id,
            role="sales_rep", permissions=frozenset({"communication.view"}),
        )
        teammate_bootstrap = bootstrap(session, teammate_context)
        assert {member.id for member in teammate_bootstrap.members} == {owner.id, teammate.id}
        assert [item.id for item in teammate_bootstrap.conversations] == [conversation.id]
        pinned = pin_message(message.id, session, context)
        assert pinned.is_pinned is True
        assert [item.id for item in search_messages("review", session, context)] == [message.id]


def test_voice_attachment_is_private_and_added_to_its_message(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        workspace, owner, teammate, _ = _seed(session)
        context = WorkspaceContext(
            workspace_id=workspace.id,
            actor_id=str(owner.id),
            user_id=owner.id,
            role="owner",
            permissions=frozenset(
                {"communication.view", "communication.create", "communication.attach"}
            ),
        )
        conversation = create_conversation(
            ConversationCreateRequest(kind="channel", name="recordings", member_ids=[teammate.id]),
            session,
            context,
        )
        message = create_message(
            conversation.id,
            MessageCreateRequest(plaintext="Call notes"),
            session,
            context,
        )
        stored: dict[str, str] = {}

        def store(key: str, content: bytes, content_type: str) -> str:
            stored.update(key=key, content_type=content_type)
            assert content == b"voice-bytes"
            return key

        monkeypatch.setattr("lead_agent.api.routes.communication.put_private_attachment", store)
        file = UploadFile(
            BytesIO(b"voice-bytes"),
            filename="voice.webm",
            headers=Headers({"content-type": "audio/webm"}),
        )
        attachment = upload_attachment(
            message.id,
            file,
            "voice",
            1_000,
            session,
            context,
            Settings(),
        )
        assert attachment.kind == "voice"
        assert attachment.download_url.endswith(str(attachment.id))
        assert stored["key"].startswith(f"communication/{workspace.id}/{message.id}/")
        messages = search_messages("Call", session, context)
        assert messages[0].attachments[0].filename == "voice.webm"


def test_due_reminders_are_dispatched_once(monkeypatch) -> None:
    from lead_agent.worker import celery_app as worker

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        workspace, owner, teammate, _ = _seed(session)
        context = WorkspaceContext(
            workspace_id=workspace.id,
            actor_id=str(owner.id),
            user_id=owner.id,
            role="owner",
            permissions=frozenset({"communication.view", "communication.create"}),
        )
        conversation = create_conversation(
            ConversationCreateRequest(kind="channel", name="reminders", member_ids=[teammate.id]),
            session,
            context,
        )
        message = create_message(
            conversation.id,
            MessageCreateRequest(plaintext="Follow up"),
            session,
            context,
        )
        session.add(
            CommunicationReminderORM(
                id=uuid4(),
                workspace_id=workspace.id,
                message_id=message.id,
                recipient_id=owner.id,
                due_at=datetime.now(UTC) - timedelta(minutes=1),
                created_at=datetime.now(UTC),
            )
        )
        session.commit()

        class SessionContext:
            def __enter__(self):
                return session

            def __exit__(self, *_args):
                return False

        monkeypatch.setattr(worker, "get_session_factory", lambda: lambda: SessionContext())
        assert worker.dispatch_due_communication_reminders.run() == 1
        assert worker.dispatch_due_communication_reminders.run() == 0
        notifications = list(session.scalars(select(CommunicationNotificationORM)))
        assert len(notifications) == 1
        assert notifications[0].kind == "reminder"


def test_private_project_metadata_is_not_exposed_to_unrelated_members() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        workspace, owner, teammate, _ = _seed(session)
        owner_context = WorkspaceContext(
            workspace_id=workspace.id,
            actor_id=str(owner.id),
            user_id=owner.id,
            role="owner",
            permissions=frozenset(
                {"communication.view", "communication.create", "communication.manage"}
            ),
        )
        project = create_project(
            ProjectCreateRequest(name="Sensitive account", description="Confidential account work"),
            session,
            owner_context,
        )
        create_conversation(
            ConversationCreateRequest(kind="channel", name="account-team", project_id=project.id),
            session,
            owner_context,
        )
        teammate_context = WorkspaceContext(
            workspace_id=workspace.id,
            actor_id=str(teammate.id),
            user_id=teammate.id,
            role="sales_rep",
            permissions=frozenset({"communication.view"}),
        )
        response = bootstrap(session, teammate_context)
        assert response.projects == []
        assert response.conversations == []


def test_vercel_cron_requires_a_secret_and_runs_both_scheduled_jobs(monkeypatch) -> None:
    from fastapi import HTTPException

    from lead_agent.api.routes import cron

    settings = Settings(cron_secret="c" * 48)
    with pytest.raises(HTTPException, match="unauthorized"):
        cron.run_scheduled_work(None, settings)
    monkeypatch.setattr(
        cron,
        "dispatch_due_communication_reminders",
        SimpleNamespace(run=lambda: 2),
    )
    monkeypatch.setattr(cron, "run_due_crm_inbound_syncs_inline", lambda: 3)
    assert cron.run_scheduled_work(f"Bearer {settings.cron_secret}", settings) == {
        "communication_reminders": 2,
        "crm_inbound_syncs": 3,
    }
