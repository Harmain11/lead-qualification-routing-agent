from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_runtime_settings
from lead_agent.api.main import app
from lead_agent.application.crm_inbound import configure_inbound_sync
from lead_agent.application.integrations import create_manual_connection, validate_connection
from lead_agent.application.team import invite_member
from lead_agent.domain.integrations import ConnectionValidation, ManualConnectionInput
from lead_agent.infrastructure.crm.hubspot import HubSpotConnectionValidator
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.db.models import (
    AuthSessionORM,
    CrmInboundSyncStateORM,
    EmailVerificationTokenORM,
    ProviderConnectionORM,
    UserORM,
    WorkspaceInvitationORM,
    WorkspaceMembershipORM,
    WorkspaceORM,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.email import (
    CaptureEmailProvider,
    DisabledEmailProvider,
    get_email_provider,
)
from lead_agent.infrastructure.oauth import DevelopmentOAuthBroker, get_oauth_broker
from lead_agent.infrastructure.provider_lifecycle import get_provider_connection_validator
from lead_agent.infrastructure.secrets import DevelopmentSecretStore, get_secret_store
from lead_agent.settings.config import Settings


def auth_harness(tmp_path, *, foreign_keys: bool = False):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'auth.db'}",
        connect_args={"check_same_thread": False},
    )
    if foreign_keys:
        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(connection, _record) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    Base.metadata.create_all(engine)
    session = Session(engine)
    settings = Settings(
        app_env="test",
        auth_development_fallback=False,
        auth_cookie_secure=False,
        database_url=f"sqlite:///{tmp_path / 'auth.db'}",
    )

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_runtime_settings] = lambda: settings
    provider = CaptureEmailProvider()
    app.dependency_overrides[get_email_provider] = lambda: provider
    app.dependency_overrides[get_secret_store] = lambda: DevelopmentSecretStore()
    app.dependency_overrides[get_oauth_broker] = lambda: DevelopmentOAuthBroker()
    return session


def signup(client: TestClient, email: str = "owner@example.test"):
    created = client.post(
        "/v1/auth/signup",
        json={
            "email": email,
            "password": "correct-horse-battery-staple",
            "display_name": "Workspace Owner",
            "workspace_name": f"{email} workspace",
        },
    )
    assert created.status_code == 202
    token = created_request_token(app.dependency_overrides[get_email_provider]())
    verified = client.get("/v1/auth/verify-email", params={"token": token}, follow_redirects=False)
    assert verified.status_code == 303
    signed_in = client.post(
        "/v1/auth/signin",
        json={"email": email, "password": "correct-horse-battery-staple"},
    )
    return signed_in


def created_request_token(provider: CaptureEmailProvider) -> str:
    from urllib.parse import parse_qs, urlsplit

    assert provider.messages
    return parse_qs(urlsplit(provider.messages[-1].verification_url).query)["verify_email"][0]


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("lead_agent_csrf") or ""}


def test_browser_oauth_denial_records_safe_reason_and_redirects(tmp_path, caplog) -> None:
    from urllib.parse import parse_qs, urlsplit

    session = auth_harness(tmp_path)
    store = DevelopmentSecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    try:
        with TestClient(app) as client:
            signup(client)
            for provider, code in [
                ("salesforce", "access_denied"),
                ("salesforce", "invalid_app_access"),
                ("salesforce", "CSRF"),
                ("slack", "invalid_team_for_non_distributed_app"),
                ("salesforce", "unknown-private-error"),
                ("salesforce", None),
            ]:
                started = client.post(
                    f"/v1/integrations/{provider}/connect",
                    headers=csrf(client), json={"account_name": "Pending", "scopes": []},
                )
                assert started.status_code == 200
                payload = started.json()
                params = {"state": payload["state"]}
                if code is not None:
                    params["error"] = code
                params["error_description"] = "private-provider-description"
                response = client.get(
                    f"/v1/integrations/{provider}/oauth/callback",
                    params=params, follow_redirects=False,
                )
                assert response.status_code == 307
                notice = parse_qs(urlsplit(response.headers["location"]).query)
                assert notice["status"] == ["failed"]
                assert notice["integration"] == [provider]
                row = session.get(ProviderConnectionORM, UUID(payload["connection"]["id"]))
                assert row.status == "connection_failed"
                expected = code if code in {
                    "access_denied", "invalid_team_for_non_distributed_app", "invalid_app_access",
                    "CSRF",
                } else "oauth_provider_error" if code else "missing_authorization_code"
                assert row.last_validation_result["error_code"] == expected
                assert row.secret_reference is None
                assert expected in notice["message"][0]
            # The test client's own HTTP logging includes its request URL;
            # assert the application logger never copies untrusted OAuth values.
            app_logs = "\n".join(
                record.getMessage() for record in caplog.records
                if record.name.startswith("lead_agent.")
            )
            assert "unknown-private-error" not in app_logs
            assert "private-provider-description" not in app_logs
            assert "OAuth callback failed for provider=salesforce reason=access_denied" in app_logs
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_password_signup_is_pending_until_single_use_verification(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            created = client.post(
                "/v1/auth/signup",
                json={
                    "email": "pending@example.test",
                    "password": "correct-horse-battery-staple",
                    "display_name": "Pending Owner",
                    "workspace_name": "Pending Workspace",
                },
            )
            assert created.status_code == 202
            assert client.cookies.get("lead_agent_session") is None
            user = session.scalar(select(UserORM).where(UserORM.email == "pending@example.test"))
            assert user is not None and user.status == "pending_email"
            assert (
                session.scalar(
                    select(WorkspaceMembershipORM).where(
                        WorkspaceMembershipORM.user_id == user.id
                    )
                )
                is None
            )
            assert client.post(
                "/v1/auth/signin",
                json={"email": "pending@example.test", "password": "correct-horse-battery-staple"},
            ).status_code == 401

            provider = app.dependency_overrides[get_email_provider]()
            assert client.post(
                "/v1/auth/resend-verification",
                json={"email": "pending@example.test"},
            ).status_code == 202
            assert len(provider.messages) == 1
            token = created_request_token(provider)
            verified = client.get(
                "/v1/auth/verify-email", params={"token": token}, follow_redirects=False
            )
            assert verified.status_code == 303
            assert "email_verification=verified" in verified.headers["location"]
            assert client.get("/v1/auth/session").status_code == 200
            assert (
                session.scalar(
                    select(WorkspaceMembershipORM).where(
                        WorkspaceMembershipORM.user_id == user.id
                    )
                )
                is not None
            )
            replay = client.get(
                "/v1/auth/verify-email", params={"token": token}, follow_redirects=False
            )
            assert replay.status_code == 303
            assert "email_verification=expired" in replay.headers["location"]
            assert (
                session.scalar(
                    select(EmailVerificationTokenORM).where(
                        EmailVerificationTokenORM.consumed_at.is_not(None)
                    )
                )
                is not None
            )
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_password_signup_is_closed_when_email_delivery_is_unconfigured(tmp_path) -> None:
    session = auth_harness(tmp_path)
    app.dependency_overrides[get_email_provider] = lambda: DisabledEmailProvider()
    try:
        with TestClient(app) as client:
            assert client.get("/v1/auth/password-signup/status").json() == {"enabled": False}
            response = client.post(
                "/v1/auth/signup",
                json={
                    "email": "closed@example.test",
                    "password": "correct-horse-battery-staple",
                    "display_name": "Closed Owner",
                    "workspace_name": "Closed Workspace",
                },
            )
            assert response.status_code == 503
            assert response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_production_api_requires_authentication(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            status_response = client.get("/v1/auth/status")
            assert status_response.status_code == 200
            assert status_response.json() is None
            assert client.get("/v1/leads").status_code == 401
            assert client.get("/v1/analytics/dashboard?range=monthly").status_code == 401
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_signup_session_and_signout_lifecycle(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            created = signup(client)
            assert created.status_code == 200
            body = created.json()
            assert body["user"]["email"] == "owner@example.test"
            assert len(body["workspaces"]) == 1
            assert "workspace.manage" in body["workspaces"][0]["effective_permissions"]
            assert body["expires_at"] is not None
            assert client.cookies.get("lead_agent_session")
            assert client.cookies.get("lead_agent_csrf")
            restored = client.get("/v1/auth/session")
            assert restored.status_code == 200
            assert restored.json()["expires_at"] is not None
            assert client.get("/v1/leads").status_code == 200
            signed_out = client.post("/v1/auth/signout", headers=csrf(client))
            assert signed_out.status_code == 204
            assert client.get("/v1/auth/session").status_code == 401
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_suspended_member_cannot_use_provider_actions(tmp_path) -> None:
    session = auth_harness(tmp_path)
    store = DevelopmentSecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    try:
        with TestClient(app) as client:
            assert signup(client).status_code == 200
            membership = session.scalar(select(WorkspaceMembershipORM))
            assert membership is not None
            connection = create_manual_connection(
                session,
                membership.workspace_id,
                "hubspot",
                ManualConnectionInput(
                    account_name="Suspended member test",
                    auth_type="private_token",
                    scopes=[],
                    credentials={"access_token": "stored-provider-token"},
                ),
                store,
            )
            membership.status = "suspended"
            session.commit()
            tested = client.post(
                f"/v1/integrations/connections/{connection.id}/test",
                headers=csrf(client),
            )
            disconnected = client.post(
                f"/v1/integrations/connections/{connection.id}/disconnect",
                headers=csrf(client),
            )
            assert tested.status_code == 403
            assert disconnected.status_code == 403
            assert store.retrieve(connection.secret_reference) is not None
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_session_reports_current_workspace_permission_overrides(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            assert signup(client).status_code == 200
            membership = session.scalar(select(WorkspaceMembershipORM))
            assert membership is not None
            membership.permission_overrides = {
                "grants": [],
                "denies": ["leads.export", "icp.manage"],
            }
            session.commit()

            restored = client.get("/v1/auth/session")
            assert restored.status_code == 200
            permissions = restored.json()["workspaces"][0]["effective_permissions"]
            assert "leads.export" not in permissions
            assert "icp.manage" not in permissions
            assert "workspace.manage" in permissions
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_signin_rejects_bad_password_and_restores_session(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as first:
            assert signup(first).status_code == 200
        with TestClient(app) as client:
            denied = client.post(
                "/v1/auth/signin",
                json={"email": "owner@example.test", "password": "incorrect"},
            )
            accepted = client.post(
                "/v1/auth/signin",
                json={
                    "email": "owner@example.test",
                    "password": "correct-horse-battery-staple",
                },
            )
            assert denied.status_code == 401
            assert accepted.status_code == 200
            assert client.get("/v1/auth/session").status_code == 200
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_signin_rotates_and_revokes_existing_session(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            assert signup(client).status_code == 200
            original_token = client.cookies.get("lead_agent_session")
            accepted = client.post(
                "/v1/auth/signin",
                json={
                    "email": "owner@example.test",
                    "password": "correct-horse-battery-staple",
                },
            )
            rotated_token = client.cookies.get("lead_agent_session")
            rows = list(session.scalars(select(AuthSessionORM)))
            assert accepted.status_code == 200
            assert original_token and rotated_token and original_token != rotated_token
            assert len(rows) == 3
            assert sum(row.revoked_at is not None for row in rows) == 2
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_workspace_access_code_sets_password_for_first_signin(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            assert signup(client).status_code == 200
            owner = session.scalar(select(UserORM).where(UserORM.email == "owner@example.test"))
            assert owner is not None
            membership = session.scalar(
                select(WorkspaceMembershipORM).where(WorkspaceMembershipORM.user_id == owner.id)
            )
            assert membership is not None
            invited_email = "new-staff@example.test"
            invitation, access_code = invite_member(
                session,
                membership.workspace_id,
                owner.id,
                invited_email,
                "sales_rep",
            )

            missing_password = client.post(
                "/v1/auth/workspace-code",
                json={
                    "workspace_id": str(invitation.workspace_id),
                    "code": access_code,
                    "email": invited_email,
                    "display_name": "New Staff",
                    "password": None,
                },
            )
            assert missing_password.status_code == 422
            assert "Choose a password" in missing_password.json()["detail"]

            accepted = client.post(
                "/v1/auth/workspace-code",
                json={
                    "workspace_id": str(invitation.workspace_id),
                    "code": access_code,
                    "email": invited_email,
                    "display_name": "New Staff",
                    "password": "staff-login-password",
                },
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["user"]["display_name"] == "New Staff"

            signed_in = client.post(
                "/v1/auth/signin",
                json={"email": invited_email, "password": "staff-login-password"},
            )
            assert signed_in.status_code == 200, signed_in.text
            assert signed_in.json()["user"]["email"] == invited_email
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_cookie_mutation_requires_csrf(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            body = signup(client).json()
            workspace_id = body["current_workspace_id"]
            denied = client.post(
                "/v1/auth/workspace",
                json={"workspace_id": workspace_id},
            )
            allowed = client.post(
                "/v1/auth/workspace",
                headers=csrf(client),
                json={"workspace_id": workspace_id},
            )
            assert denied.status_code == 403
            assert allowed.status_code == 200
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_workspace_header_is_verified_against_membership(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client_a, TestClient(app) as client_b:
            workspace_a = signup(client_a, "a@example.test").json()["current_workspace_id"]
            workspace_b = signup(client_b, "b@example.test").json()["current_workspace_id"]
            assert (
                client_a.get("/v1/leads", headers={"X-Workspace-ID": workspace_a}).status_code
                == 200
            )
            assert (
                client_a.get("/v1/leads", headers={"X-Workspace-ID": workspace_b}).status_code
                == 403
            )
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_inactive_membership_cannot_select_workspace(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            signup_body = signup(client).json()
            user_membership = session.scalar(select(WorkspaceMembershipORM))
            assert user_membership is not None
            other = WorkspaceORM(
                id=uuid4(),
                name="Revoked workspace",
                slug=f"revoked-{uuid4().hex}",
                status="active",
                is_demo=False,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(other)
            session.flush()
            session.add(
                WorkspaceMembershipORM(
                    id=uuid4(),
                    workspace_id=other.id,
                    user_id=user_membership.user_id,
                    role="member",
                    status="inactive",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            session.commit()
            denied = client.post(
                "/v1/auth/workspace",
                headers={**csrf(client), "X-Workspace-ID": signup_body["current_workspace_id"]},
                json={"workspace_id": str(other.id)},
            )
            assert denied.status_code == 403
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_revoked_current_membership_cannot_restore_or_access_workspace(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            assert signup(client).status_code == 200
            membership = session.scalar(select(WorkspaceMembershipORM))
            assert membership is not None
            membership.status = "inactive"
            session.commit()

            assert client.get("/v1/auth/session").status_code == 403
            assert client.get("/v1/leads").status_code == 403
            assert client.get("/v1/auth/status").json() is None
            assert client.cookies.get("lead_agent_session") is None
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_session_status_selects_another_active_workspace_after_current_membership_is_removed(
    tmp_path,
) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            assert signup(client).status_code == 200
            current = session.scalar(select(WorkspaceMembershipORM))
            assert current is not None
            other = WorkspaceORM(
                id=uuid4(),
                name="Remaining workspace",
                slug=f"remaining-{uuid4().hex}",
                status="active",
                is_demo=False,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(other)
            session.flush()
            session.add(
                WorkspaceMembershipORM(
                    id=uuid4(),
                    workspace_id=other.id,
                    user_id=current.user_id,
                    role="member",
                    status="active",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            current.status = "revoked"
            session.commit()

            recovered = client.get("/v1/auth/status")
            assert recovered.status_code == 200
            assert recovered.json()["current_workspace_id"] == str(other.id)
            assert client.get("/v1/leads").status_code == 200
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_user_can_update_account_profile_and_invalid_photo_is_rejected(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            signed_in = signup(client)
            assert signed_in.status_code == 200
            user_id = UUID(signed_in.json()["user"]["id"])

            current = client.get("/v1/auth/profile")
            assert current.status_code == 200
            assert current.json()["email"] == "owner@example.test"
            assert current.json()["avatar_data_url"] is None

            updated = client.patch(
                "/v1/auth/profile",
                headers=csrf(client),
                json={
                    "display_name": "  Alex Morgan ",
                    "job_title": "Revenue Operations Lead",
                    "phone": "+1 555 010 1000",
                    "bio": "Building reliable revenue systems.",
                    "avatar_data_url": "data:image/jpeg;base64,/9j/2Q==",
                },
            )
            assert updated.status_code == 200
            assert updated.json()["display_name"] == "Alex Morgan"
            assert updated.json()["job_title"] == "Revenue Operations Lead"
            assert updated.json()["phone"] == "+1 555 010 1000"
            assert updated.json()["bio"] == "Building reliable revenue systems."
            assert updated.json()["avatar_data_url"] == "data:image/jpeg;base64,/9j/2Q=="

            invalid_photo = client.patch(
                "/v1/auth/profile",
                headers=csrf(client),
                json={"avatar_data_url": "data:image/svg+xml;base64,PHN2Zz4="},
            )
            assert invalid_photo.status_code == 422
            user = session.get(UserORM, user_id)
            assert user is not None
            assert user.display_name == "Alex Morgan"
            assert user.avatar_data_url == "data:image/jpeg;base64,/9j/2Q=="
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_account_deletion_preview_and_self_delete_remove_owned_workspace_safely(tmp_path) -> None:
    session = auth_harness(tmp_path, foreign_keys=True)
    try:
        with TestClient(app) as client:
            signed_in = signup(client)
            user_id = UUID(signed_in.json()["user"]["id"])
            membership = session.scalar(
                select(WorkspaceMembershipORM).where(WorkspaceMembershipORM.user_id == user_id)
            )
            assert membership is not None
            workspace_id = membership.workspace_id

            preview = client.get("/v1/auth/account-deletion-preview")
            assert preview.status_code == 200
            preview_body = preview.json()
            assert preview_body["email"] == "owner@example.test"
            assert preview_body["can_delete"] is True
            assert preview_body["recent_sign_in_required"] is False
            assert preview_body["owned_workspaces"][0]["id"] == str(workspace_id)

            wrong_confirmation = client.delete(
                "/v1/auth/account",
                headers=csrf(client),
                json={"confirmation_email": "someone-else@example.test"},
            )
            assert wrong_confirmation.status_code == 422
            assert session.get(UserORM, user_id) is not None
            assert session.get(WorkspaceORM, workspace_id) is not None

            deleted = client.delete(
                "/v1/auth/account",
                headers=csrf(client),
                json={"confirmation_email": " OWNER@example.test "},
            )
            assert deleted.status_code == 204
            assert session.get(UserORM, user_id) is None
            assert session.get(WorkspaceORM, workspace_id) is None
            assert session.scalar(
                select(AuthSessionORM).where(AuthSessionORM.user_id == user_id)
            ) is None
            assert client.get("/v1/auth/status").json() is None
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_account_deletion_cancels_sent_invites_and_preserves_shared_workspace(tmp_path) -> None:
    session = auth_harness(tmp_path, foreign_keys=True)
    try:
        with TestClient(app) as client:
            signed_in = signup(client)
            deleting_user_id = UUID(signed_in.json()["user"]["id"])
            owned_membership = session.scalar(
                select(WorkspaceMembershipORM).where(
                    WorkspaceMembershipORM.user_id == deleting_user_id
                )
            )
            assert owned_membership is not None
            owned_workspace_id = owned_membership.workspace_id
            now = datetime.now(UTC)

            shared_owner = UserORM(
                id=uuid4(),
                email="other-owner@example.test",
                display_name="Other Owner",
                password_hash="not-a-login-secret",
                email_verified=True,
                status="active",
                created_at=now,
                updated_at=now,
            )
            shared_workspace = WorkspaceORM(
                id=uuid4(),
                name="Shared workspace",
                slug=f"shared-{uuid4().hex}",
                status="active",
                is_demo=False,
                created_at=now,
                updated_at=now,
            )
            session.add_all([shared_owner, shared_workspace])
            session.flush()
            session.add_all(
                [
                    WorkspaceMembershipORM(
                        id=uuid4(),
                        workspace_id=shared_workspace.id,
                        user_id=shared_owner.id,
                        role="owner",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                    WorkspaceMembershipORM(
                        id=uuid4(),
                        workspace_id=shared_workspace.id,
                        user_id=deleting_user_id,
                        role="member",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                    WorkspaceInvitationORM(
                        id=uuid4(),
                        workspace_id=shared_workspace.id,
                        email="invitee@example.test",
                        role="member",
                        permission_overrides={},
                        token_hash=uuid4().hex * 2,
                        status="pending",
                        invited_by=deleting_user_id,
                        expires_at=now + timedelta(days=7),
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            session.commit()

            preview = client.get("/v1/auth/account-deletion-preview").json()
            assert preview["shared_workspaces"] == 1
            assert preview["invitations_to_cancel"] == 1

            deleted = client.delete(
                "/v1/auth/account",
                headers=csrf(client),
                json={"confirmation_email": "owner@example.test"},
            )
            assert deleted.status_code == 204
            assert session.get(UserORM, deleting_user_id) is None
            assert session.get(WorkspaceORM, owned_workspace_id) is None
            assert session.get(WorkspaceORM, shared_workspace.id) is not None
            assert session.get(UserORM, shared_owner.id) is not None
            assert session.scalar(
                select(WorkspaceMembershipORM).where(
                    WorkspaceMembershipORM.workspace_id == shared_workspace.id,
                    WorkspaceMembershipORM.user_id == shared_owner.id,
                )
            ) is not None
            assert session.scalar(
                select(WorkspaceInvitationORM).where(
                    WorkspaceInvitationORM.invited_by == deleting_user_id
                )
            ) is None
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_account_deletion_is_blocked_when_owned_workspace_has_other_members(tmp_path) -> None:
    session = auth_harness(tmp_path, foreign_keys=True)
    try:
        with TestClient(app) as client:
            signed_in = signup(client)
            user_id = UUID(signed_in.json()["user"]["id"])
            membership = session.scalar(
                select(WorkspaceMembershipORM).where(WorkspaceMembershipORM.user_id == user_id)
            )
            assert membership is not None
            now = datetime.now(UTC)
            other_member = UserORM(
                id=uuid4(),
                email="member@example.test",
                display_name="Member",
                password_hash="not-a-login-secret",
                email_verified=True,
                status="active",
                created_at=now,
                updated_at=now,
            )
            session.add(other_member)
            session.flush()
            session.add(
                WorkspaceMembershipORM(
                    id=uuid4(),
                    workspace_id=membership.workspace_id,
                    user_id=other_member.id,
                    role="member",
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()

            preview = client.get("/v1/auth/account-deletion-preview").json()
            assert preview["can_delete"] is False
            assert "remove the other members" in preview["blockers"][0]
            deleted = client.delete(
                "/v1/auth/account",
                headers=csrf(client),
                json={"confirmation_email": "owner@example.test"},
            )
            assert deleted.status_code == 409
            assert session.get(UserORM, user_id) is not None
            assert session.get(WorkspaceORM, membership.workspace_id) is not None
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_inactive_workspace_rejects_access_with_active_membership(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            signup_body = signup(client).json()
            workspace = session.get(WorkspaceORM, UUID(signup_body["current_workspace_id"]))
            assert workspace is not None
            workspace.status = "inactive"
            session.commit()

            denied = client.get(
                "/v1/leads",
                headers={"X-Workspace-ID": signup_body["current_workspace_id"]},
            )
            assert denied.status_code == 403
            assert client.post("/v1/auth/signout", headers=csrf(client)).status_code == 204
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_revoked_current_membership_does_not_block_switch_or_signout(tmp_path) -> None:
    session = auth_harness(tmp_path)
    try:
        with TestClient(app) as client:
            signup(client)
            current = session.scalar(select(WorkspaceMembershipORM))
            assert current is not None
            other = WorkspaceORM(
                id=uuid4(),
                name="Available workspace",
                slug=f"available-{uuid4().hex}",
                status="active",
                is_demo=False,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(other)
            session.flush()
            session.add(
                WorkspaceMembershipORM(
                    id=uuid4(),
                    workspace_id=other.id,
                    user_id=current.user_id,
                    role="member",
                    status="active",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            current.status = "inactive"
            session.commit()

            switched = client.post(
                "/v1/auth/workspace",
                headers=csrf(client),
                json={"workspace_id": str(other.id)},
            )
            assert switched.status_code == 200
            assert switched.json()["current_workspace_id"] == str(other.id)

            other_membership = session.scalar(
                select(WorkspaceMembershipORM).where(
                    WorkspaceMembershipORM.workspace_id == other.id
                )
            )
            assert other_membership is not None
            other_membership.status = "inactive"
            session.commit()
            assert client.post("/v1/auth/signout", headers=csrf(client)).status_code == 204
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_development_session_rejects_malformed_workspace_header(tmp_path) -> None:
    session = auth_harness(tmp_path)
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        app_env="development",
        auth_development_fallback=True,
        auth_cookie_secure=False,
        database_url=f"sqlite:///{tmp_path / 'auth.db'}",
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/auth/development-session",
                headers={"X-Workspace-ID": "not-a-uuid"},
            )
            assert response.status_code == 422
            assert response.json()["detail"] == "X-Workspace-ID must be a UUID"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_oauth_state_is_one_time_scoped_and_secrets_are_not_exposed(tmp_path) -> None:
    session = auth_harness(tmp_path)
    store = DevelopmentSecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    app.dependency_overrides[get_provider_connection_validator] = lambda: (
        HubSpotConnectionValidator(client_id="", client_secret="")
    )
    try:
        with TestClient(app) as client:
            assert signup(client).status_code == 200
            started = client.post(
                "/v1/integrations/hubspot/connect",
                headers=csrf(client),
                json={"account_name": "HubSpot account", "scopes": []},
            )
            assert started.status_code == 200
            payload = started.json()
            completed = client.post(
                "/v1/integrations/hubspot/callback",
                headers=csrf(client),
                json={
                    "connection_id": payload["connection"]["id"],
                    "state": payload["state"],
                    "authorization_code": "single-use-code",
                },
            )
            connection = session.get(ProviderConnectionORM, UUID(payload["connection"]["id"]))
            assert connection is not None
            stored_values = store.retrieve(connection.secret_reference)
            assert stored_values is not None
            assert "pkce_verifier" not in stored_values
            assert "redirect_uri" not in stored_values
            connection.connection_metadata = {
                **connection.connection_metadata,
                "token_expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            }
            session.commit()
            refreshed = client.post(
                f"/v1/integrations/connections/{payload['connection']['id']}/test",
                headers=csrf(client),
            )
            replay = client.post(
                "/v1/integrations/hubspot/callback",
                headers=csrf(client),
                json={
                    "connection_id": payload["connection"]["id"],
                    "state": payload["state"],
                    "authorization_code": "single-use-code",
                },
            )
            serialized = str(completed.json()).lower()
            assert completed.status_code == 200
            assert refreshed.status_code == 200
            assert refreshed.json()["valid"] is False
            assert refreshed.json()["verified"] is False
            assert refreshed.json()["error_code"] == "hubspot_credentials_missing"
            assert refreshed.json()["connection"]["status"] == "error"
            rotated_values = store.retrieve(connection.secret_reference)
            assert rotated_values is not None
            assert rotated_values["access_token"].startswith("development-refreshed-")
            assert completed.json()["status"] == "connected"
            assert replay.status_code == 422
            assert "access_token" not in serialized
            assert "refresh_token" not in serialized
            assert "secret_reference" not in serialized
            assert "oauth_state" not in serialized
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_slack_browser_callback_verifies_workspace_before_success(tmp_path) -> None:
    session = auth_harness(tmp_path)
    store = DevelopmentSecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    checked = []

    class SlackValidator:
        def validate(self, provider, credentials, scopes):
            checked.append(provider)
            assert credentials.get("access_token")
            return ConnectionValidation(
                valid=True, verified=True, status="connected",
                credential_state="configured", message="Slack workspace verified.",
                metadata={"slack_team_id": "TCHOICE", "slack_team_name": "Chosen workspace"},
            )

    app.dependency_overrides[get_provider_connection_validator] = lambda: SlackValidator()
    try:
        with TestClient(app) as client:
            assert signup(client).status_code == 200
            started = client.post(
                "/v1/integrations/slack/connect", headers=csrf(client),
                json={"account_name": "Pending Slack", "scopes": []},
            )
            assert started.status_code == 200
            payload = started.json()
            completed = client.get(
                "/v1/integrations/slack/oauth/callback",
                params={"state": payload["state"], "code": "test-approval"},
                follow_redirects=False,
            )
            assert completed.status_code == 307
            assert "integration=slack&status=connected" in completed.headers["location"]
            connection = session.get(ProviderConnectionORM, UUID(payload["connection"]["id"]))
            assert checked == ["slack"]
            assert connection.status == "verified" and connection.is_active
            assert connection.account_name == "Chosen workspace"
            assert connection.connection_metadata["slack_team_id"] == "TCHOICE"
            assert not connection.connection_metadata.get("slack_channel_id")
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_browser_oauth_callback_uses_connection_workspace_after_workspace_switch(
    tmp_path,
) -> None:
    session = auth_harness(tmp_path)
    store = DevelopmentSecretStore()
    app.dependency_overrides[get_secret_store] = lambda: store
    class Validator:
        def validate(self, provider, credentials, scopes):
            assert provider == "hubspot"
            assert credentials["access_token"]
            return ConnectionValidation(
                valid=True,
                verified=True,
                status="connected",
                credential_state="configured",
                message="Provider accepted the credentials.",
            )

    app.dependency_overrides[get_provider_connection_validator] = lambda: Validator()
    try:
        with TestClient(app) as client:
            signup_body = signup(client).json()
            started = client.post(
                "/v1/integrations/hubspot/connect",
                headers=csrf(client),
                json={"account_name": "HubSpot account", "scopes": []},
            )
            assert started.status_code == 200

            membership = session.scalar(select(WorkspaceMembershipORM))
            assert membership is not None
            other = WorkspaceORM(
                id=uuid4(),
                name="Other workspace",
                slug=f"other-{uuid4().hex}",
                status="active",
                is_demo=False,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(other)
            session.flush()
            session.add(
                WorkspaceMembershipORM(
                    id=uuid4(),
                    workspace_id=other.id,
                    user_id=membership.user_id,
                    role="member",
                    status="active",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            session.commit()
            switched = client.post(
                "/v1/auth/workspace",
                headers=csrf(client),
                json={"workspace_id": str(other.id)},
            )
            assert switched.status_code == 200

            payload = started.json()
            callback = client.get(
                "/v1/integrations/hubspot/oauth/callback",
                params={"state": payload["state"], "code": "browser-code"},
                follow_redirects=False,
            )
            connection = session.get(ProviderConnectionORM, UUID(payload["connection"]["id"]))
            assert callback.status_code == 307
            assert callback.headers["location"].endswith("?integration=hubspot&status=connected")
            assert connection is not None
            assert connection.workspace_id == UUID(signup_body["current_workspace_id"])
            assert connection.status == "verified"
            inbound = session.scalar(select(CrmInboundSyncStateORM))
            assert inbound is not None
            assert inbound.connection_id == connection.id
            assert inbound.object_type == "contacts"
            assert inbound.enabled is True
            assert inbound.sync_interval_seconds == 60
            configure_inbound_sync(
                session, connection, enabled=False, object_type="contacts", actor_id="owner"
            )
            validate_connection(
                session,
                connection.workspace_id,
                connection.id,
                store,
                DevelopmentOAuthBroker(),
                Validator(),
                "owner",
            )
            session.refresh(inbound)
            assert inbound.enabled is False
    finally:
        app.dependency_overrides.clear()
        session.close()
