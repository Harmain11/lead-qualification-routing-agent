from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_db, get_workspace_context
from lead_agent.api.main import app
from lead_agent.application.services import _deliver_lead_confirmation_email
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    LeadFollowUpEmailORM,
    LeadORM,
    ProviderConnectionORM,
    WorkspaceEmailAutomationSettingORM,
)
from lead_agent.infrastructure.gmail import GMAIL_SEND_SCOPE
from lead_agent.infrastructure.repositories import ensure_workspace


def test_lead_confirmation_automation_defaults_off_and_is_workspace_scoped(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lead-email-automation.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    first_workspace_id = uuid4()
    second_workspace_id = uuid4()
    ensure_workspace(session, first_workspace_id, name="First automation workspace")
    ensure_workspace(session, second_workspace_id, name="Second automation workspace")
    session.commit()

    selected = {
        "workspace_id": first_workspace_id,
        "permissions": frozenset({"integrations.manage"}),
    }
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        workspace_id=selected["workspace_id"],
        actor_id="automation-admin",
        permissions=selected["permissions"],
    )
    try:
        with TestClient(app) as client:
            endpoint = "/v1/settings/email-automation/lead-confirmations"
            assert client.get(endpoint).json() == {"enabled": False}
            assert client.put(endpoint, json={"enabled": True}).json() == {
                "enabled": True
            }

            selected["workspace_id"] = second_workspace_id
            assert client.get(endpoint).json() == {"enabled": False}

            selected["workspace_id"] = first_workspace_id
            assert client.get(endpoint).json() == {"enabled": True}
            selected["permissions"] = frozenset()
            assert client.put(endpoint, json={"enabled": False}).status_code == 403
            selected["permissions"] = frozenset({"integrations.manage"})
            assert client.put(endpoint, json={"enabled": False}).json() == {
                "enabled": False
            }
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()


def _lead(workspace_id) -> LeadORM:
    now = datetime.now(UTC)
    return LeadORM(
        id=uuid4(),
        workspace_id=workspace_id,
        lead_number=1,
        source="website",
        full_name="Taylor Prospect",
        email="taylor@example.test",
        company_name="Example Co",
        product_or_service="Lead management",
        message="Please contact me about the product.",
        received_at=now,
        raw_payload={},
        normalization_version="v1",
        created_at=now,
        updated_at=now,
    )


def test_confirmation_service_does_not_send_when_default_is_off(
    tmp_path, monkeypatch
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lead-email-default-off.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        workspace_id = uuid4()
        ensure_workspace(session, workspace_id, name="Default off workspace")
        now = datetime.now(UTC)
        lead = _lead(workspace_id)
        session.add(lead)
        session.add(
            ProviderConnectionORM(
                id=uuid4(),
                workspace_id=workspace_id,
                provider_type="gmail",
                external_account_id="sender@example.test",
                account_name="sender@example.test",
                auth_type="oauth2",
                status="verified",
                scopes=[GMAIL_SEND_SCOPE],
                secret_reference="encrypted-reference",
                credential_state="configured",
                connection_metadata={"gmail_email": "sender@example.test"},
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

        def unexpected_send(*_args, **_kwargs):
            raise AssertionError("the default-off setting must prevent sending")

        monkeypatch.setattr(
            "lead_agent.application.services.get_workspace_connection_resolver",
            unexpected_send,
        )
        _deliver_lead_confirmation_email(session, lead)
        assert session.scalar(select(LeadFollowUpEmailORM)) is None
    engine.dispose()


def test_confirmation_service_sends_when_workspace_enables_it(
    tmp_path, monkeypatch
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lead-email-enabled.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sent: list[dict[str, str]] = []
    with Session(engine) as session:
        workspace_id = uuid4()
        ensure_workspace(session, workspace_id, name="Enabled workspace")
        now = datetime.now(UTC)
        lead = _lead(workspace_id)
        session.add(lead)
        session.add(
            WorkspaceEmailAutomationSettingORM(
                workspace_id=workspace_id,
                lead_confirmation_enabled=True,
                updated_by="automation-admin",
                updated_at=now,
            )
        )
        connection = ProviderConnectionORM(
            id=uuid4(),
            workspace_id=workspace_id,
            provider_type="gmail",
            external_account_id="sender@example.test",
            account_name="sender@example.test",
            auth_type="oauth2",
            status="verified",
            scopes=[GMAIL_SEND_SCOPE],
            secret_reference="encrypted-reference",
            credential_state="configured",
            connection_metadata={"gmail_email": "sender@example.test"},
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(connection)
        session.commit()

        class FakeResolver:
            def resolve_connection(self, *_args, **_kwargs):
                return SimpleNamespace(credentials={"access_token": "fake-token"})

        class FakeOutboundProvider:
            def __init__(self, *_args, **_kwargs):
                pass

            def send_email(self, **kwargs):
                sent.append(kwargs)
                # A duplicate intake arriving while the first API call is in
                # flight must see the persisted "sending" claim and return.
                _deliver_lead_confirmation_email(session, lead)
                return "gmail-message-id"

        monkeypatch.setattr(
            "lead_agent.application.services.get_workspace_connection_resolver",
            lambda: FakeResolver(),
        )
        monkeypatch.setattr(
            "lead_agent.application.services.GmailOutboundProvider",
            FakeOutboundProvider,
        )

        _deliver_lead_confirmation_email(session, lead)
        _deliver_lead_confirmation_email(session, lead)
        email = session.scalar(select(LeadFollowUpEmailORM))
        assert email is not None
        assert email.status == "sent"
        assert email.external_message_id == "gmail-message-id"
        assert len(sent) == 1
        assert sent[0]["recipient"] == lead.email
    engine.dispose()
