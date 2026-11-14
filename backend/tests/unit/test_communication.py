from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.datastructures import Headers, UploadFile

from lead_agent.api.routes.communication import (
    ConversationCreateRequest,
    MessageCreateRequest,
    ProjectCreateRequest,
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
