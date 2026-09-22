from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from lead_agent.domain.integrations import (
    PROVIDER_SETUP,
    ConnectionValidation,
    ManualConnectionInput,
    OAuthBroker,
    OAuthStart,
    SecretStore,
)
from lead_agent.infrastructure.db.models import ProviderConnectionORM
from lead_agent.infrastructure.oauth import pkce_challenge
from lead_agent.infrastructure.repositories import (
    ensure_workspace,
    get_provider_connection,
)

CRM_PROVIDER_TYPES = {"mock", "salesforce", "hubspot"}
SENSITIVE_METADATA_FRAGMENTS = ("secret", "token", "password", "credential", "api_key")
REQUIRED_MANUAL_CREDENTIALS: dict[str, set[str]] = {
    "salesforce": {"client_id", "client_secret", "refresh_token"},
    "hubspot": {"access_token"},
    "slack": {"bot_token", "channel_id"},
    "openai": {"api_key"},
}


def _safe_metadata(metadata: dict[str, object]) -> dict[str, object]:
    def contains_sensitive_key(value: object) -> bool:
        if isinstance(value, dict):
            return any(
                any(fragment in str(key).lower() for fragment in SENSITIVE_METADATA_FRAGMENTS)
                or contains_sensitive_key(nested)
                for key, nested in value.items()
            )
        if isinstance(value, list):
            return any(contains_sensitive_key(item) for item in value)
        return False

    if contains_sensitive_key(metadata):
        raise ValueError("connection metadata cannot contain credential values")
    return dict(metadata)


def _validate_manual_credentials(
    provider: str, auth_type: str, credentials: dict[str, str]
) -> None:
    if not credentials or any(not value.strip() for value in credentials.values()):
        raise ValueError("credential values are required")
    if provider == "mock":
        return
    setup = PROVIDER_SETUP.get(provider)
    if setup is None:
        raise ValueError(f"unsupported provider: {provider}")
    if auth_type == "oauth2" or auth_type not in setup.supported_auth:
        raise ValueError(f"manual {auth_type} authentication is not supported for {provider}")
    missing = sorted(REQUIRED_MANUAL_CREDENTIALS.get(provider, set()) - credentials.keys())
    if missing:
        raise ValueError(f"missing credential fields: {', '.join(missing)}")


def _activate_connection(session: Session, connection: ProviderConnectionORM) -> None:
    if connection.provider_type not in CRM_PROVIDER_TYPES:
        connection.is_active = True
        return
    session.execute(
        update(ProviderConnectionORM)
        .where(
            ProviderConnectionORM.workspace_id == connection.workspace_id,
            ProviderConnectionORM.provider_type.in_(CRM_PROVIDER_TYPES),
            ProviderConnectionORM.id != connection.id,
        )
        .values(is_active=False)
    )
    connection.is_active = True


def create_manual_connection(
    session: Session,
    workspace_id: UUID,
    provider: str,
    request: ManualConnectionInput,
    secret_store: SecretStore,
) -> ProviderConnectionORM:
    _validate_manual_credentials(provider, request.auth_type, request.credentials)
    ensure_workspace(session, workspace_id)
    reference = secret_store.put(workspace_id, provider, request.credentials)
    now = datetime.now(UTC)
    row = ProviderConnectionORM(
        id=uuid4(),
        workspace_id=workspace_id,
        provider_type=provider,
        external_account_id=request.external_account_id,
        account_name=request.account_name,
        auth_type=request.auth_type,
        status="connected",
        scopes=request.scopes,
        secret_reference=reference,
        credential_state="configured",
        last_validation_at=None,
        last_validation_result={"status": "not_tested"},
        connection_metadata=_safe_metadata(request.metadata),
        is_active=False,
        disconnected_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    if request.activate:
        _activate_connection(session, row)
    session.commit()
    session.refresh(row)
    return row


def begin_oauth_connection(
    session: Session,
    workspace_id: UUID,
    provider: str,
    account_name: str,
    scopes: list[str],
    broker: OAuthBroker,
    secret_store: SecretStore,
    public_base_url: str,
) -> tuple[ProviderConnectionORM, OAuthStart]:
    setup = PROVIDER_SETUP.get(provider)
    if setup is None or "oauth2" not in setup.supported_auth:
        raise ValueError(f"OAuth is not supported for {provider}")
    ensure_workspace(session, workspace_id)
    connection_id = uuid4()
    state = f"{connection_id}.{secrets.token_urlsafe(32)}"
    verifier = secrets.token_urlsafe(64)
    redirect_uri = f"{public_base_url.rstrip('/')}/v1/integrations/{provider}/oauth/callback"
    active_scopes = scopes or setup.required_scopes
    authorization_url = broker.begin(
        provider,
        connection_id,
        state,
        pkce_challenge(verifier),
        redirect_uri,
        active_scopes,
    )
    temporary_reference = secret_store.put(
        workspace_id,
        provider,
        {"pkce_verifier": verifier, "redirect_uri": redirect_uri},
    )
    now = datetime.now(UTC)
    row = ProviderConnectionORM(
        id=connection_id,
        workspace_id=workspace_id,
        provider_type=provider,
        external_account_id=None,
        account_name=account_name,
        auth_type="oauth2",
        status="awaiting_authorization",
        scopes=active_scopes,
        secret_reference=temporary_reference,
        credential_state="missing",
        last_validation_at=None,
        last_validation_result={"status": "authorization_pending"},
        connection_metadata={
            "oauth_state_hash": hashlib.sha256(state.encode()).hexdigest(),
            "oauth_state_expires_at": (now + timedelta(minutes=10)).isoformat(),
            "oauth_redirect_uri": redirect_uri,
        },
        is_active=False,
        disconnected_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return row, OAuthStart(
        connection_id=row.id,
        state=state,
        authorization_url=authorization_url,
    )


def complete_oauth_connection(
    session: Session,
    workspace_id: UUID,
    connection_id: UUID,
    state: str,
    authorization_code: str,
    secret_store: SecretStore,
    broker: OAuthBroker,
) -> ProviderConnectionORM:
    # Serialize redemption in production databases so the same one-time state
    # cannot be exchanged concurrently by two callback requests.
    row = session.scalar(
        select(ProviderConnectionORM)
        .where(
            ProviderConnectionORM.id == connection_id,
            ProviderConnectionORM.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if row is None or row.status != "awaiting_authorization":
        raise ValueError("pending connection was not found")
    expected = row.connection_metadata.get("oauth_state_hash")
    actual = hashlib.sha256(state.encode()).hexdigest()
    if not isinstance(expected, str) or not secrets.compare_digest(expected, actual):
        raise ValueError("OAuth state validation failed")
    expires_raw = row.connection_metadata.get("oauth_state_expires_at")
    if not isinstance(expires_raw, str) or datetime.fromisoformat(expires_raw) <= datetime.now(UTC):
        row.status = "authorization_expired"
        row.credential_state = "expired"
        session.commit()
        raise ValueError("OAuth authorization request expired; reconnect and try again")
    pending_values = secret_store.get(row.secret_reference) if row.secret_reference else None
    verifier = (pending_values or {}).get("pkce_verifier")
    redirect_uri = (pending_values or {}).get("redirect_uri")
    if not verifier or not redirect_uri:
        row.status = "connection_failed"
        row.credential_state = "invalid"
        session.commit()
        raise ValueError("OAuth PKCE transaction is unavailable")
    row.status = "validating"
    session.flush()
    try:
        values = broker.exchange(
            row.provider_type,
            authorization_code,
            verifier,
            redirect_uri,
        )
    except Exception as error:
        row.status = "connection_failed"
        row.credential_state = "invalid"
        row.last_validation_result = {"status": "failed", "message": str(error)[:500]}
        row.updated_at = datetime.now(UTC)
        session.commit()
        raise ValueError("OAuth provider authorization failed") from error
    old_reference = row.secret_reference
    row.secret_reference = secret_store.put(workspace_id, row.provider_type, values)
    if old_reference:
        secret_store.delete(old_reference)
    row.credential_state = "configured"
    row.status = "connected"
    row.account_name = values.get("account_name", row.account_name)
    row.external_account_id = values.get("external_account_id", row.external_account_id)
    row.updated_at = datetime.now(UTC)
    expires_in = int(values.get("expires_in", "3600"))
    row.last_validation_at = datetime.now(UTC)
    row.last_validation_result = {"status": "valid", "message": "OAuth account authorized"}
    row.connection_metadata = {
        "token_expires_at": (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat(),
        "refresh_available": bool(values.get("refresh_token")),
    }
    _activate_connection(session, row)
    session.commit()
    session.refresh(row)
    return row


def validate_connection(
    session: Session,
    workspace_id: UUID,
    connection_id: UUID,
    secret_store: SecretStore,
    broker: OAuthBroker | None = None,
) -> tuple[ProviderConnectionORM, ConnectionValidation]:
    row = get_provider_connection(session, workspace_id, connection_id)
    if row is None:
        raise LookupError("provider connection not found")
    values = secret_store.get(row.secret_reference) if row.secret_reference else None
    if row.auth_type == "oauth2" and values is not None:
        expires_raw = row.connection_metadata.get("token_expires_at")
        expired = isinstance(expires_raw, str) and datetime.fromisoformat(
            expires_raw
        ) <= datetime.now(UTC)
        if expired:
            refresh_token = values.get("refresh_token")
            if broker is None or not refresh_token:
                validation = ConnectionValidation(
                    valid=False,
                    credential_state="expired",
                    message="Authorization expired; reconnect the provider account.",
                )
                row.status = "authorization_expired"
                row.credential_state = "expired"
                row.last_validation_at = datetime.now(UTC)
                row.last_validation_result = validation.model_dump(mode="json")
                session.commit()
                return row, validation
            try:
                refreshed = broker.refresh(row.provider_type, refresh_token)
                refreshed.setdefault("refresh_token", refresh_token)
                old_reference = row.secret_reference
                row.secret_reference = secret_store.put(
                    workspace_id, row.provider_type, refreshed
                )
                if old_reference:
                    secret_store.delete(old_reference)
                values = refreshed
                expires_in = int(refreshed.get("expires_in", "3600"))
                row.connection_metadata = {
                    **row.connection_metadata,
                    "token_expires_at": (
                        datetime.now(UTC) + timedelta(seconds=expires_in)
                    ).isoformat(),
                    "refresh_available": True,
                }
            except Exception as error:
                validation = ConnectionValidation(
                    valid=False,
                    credential_state="invalid",
                    message="Authorization refresh failed; reconnect the provider account.",
                )
                row.status = "reconnect_required"
                row.credential_state = "invalid"
                row.last_validation_at = datetime.now(UTC)
                row.last_validation_result = {
                    **validation.model_dump(mode="json"),
                    "error": str(error)[:500],
                }
                session.commit()
                return row, validation
    expected = {"access_token"} if row.auth_type == "oauth2" else REQUIRED_MANUAL_CREDENTIALS.get(
        row.provider_type, set()
    )
    missing = sorted(expected - set(values or {}))
    valid = values is not None and not missing
    now = datetime.now(UTC)
    validation = ConnectionValidation(
        valid=valid,
        credential_state="configured" if valid else "invalid",
        message=(
            "Credential structure validated; live provider validation remains adapter-specific."
            if valid
            else f"Missing credential fields: {', '.join(missing) or 'secret reference'}"
        ),
    )
    row.last_validation_at = now
    row.last_validation_result = validation.model_dump(mode="json")
    row.credential_state = validation.credential_state
    row.status = "connected" if valid else "needs_attention"
    row.updated_at = now
    session.commit()
    session.refresh(row)
    return row, validation


def disconnect_connection(
    session: Session,
    workspace_id: UUID,
    connection_id: UUID,
    secret_store: SecretStore,
    broker: OAuthBroker | None = None,
) -> ProviderConnectionORM:
    row = get_provider_connection(session, workspace_id, connection_id)
    if row is None:
        raise LookupError("provider connection not found")
    if row.secret_reference:
        values = secret_store.get(row.secret_reference)
        if broker is not None and values is not None:
            try:
                broker.revoke(row.provider_type, values)
            except Exception:
                pass
        secret_store.delete(row.secret_reference)
    now = datetime.now(UTC)
    row.secret_reference = None
    row.credential_state = "missing"
    row.status = "disconnected"
    row.is_active = False
    row.disconnected_at = now
    row.updated_at = now
    session.commit()
    session.refresh(row)
    return row


def reconnect_connection(
    session: Session,
    workspace_id: UUID,
    connection_id: UUID,
    credentials: dict[str, str],
    secret_store: SecretStore,
) -> ProviderConnectionORM:
    row = get_provider_connection(session, workspace_id, connection_id)
    if row is None:
        raise LookupError("provider connection not found")
    _validate_manual_credentials(row.provider_type, row.auth_type, credentials)
    if row.secret_reference:
        secret_store.delete(row.secret_reference)
    row.secret_reference = secret_store.put(workspace_id, row.provider_type, credentials)
    row.credential_state = "configured"
    row.status = "connected"
    row.disconnected_at = None
    row.updated_at = datetime.now(UTC)
    _activate_connection(session, row)
    session.commit()
    session.refresh(row)
    return row


def find_pending_oauth_connection(
    session: Session, workspace_id: UUID, provider: str
) -> ProviderConnectionORM | None:
    return session.scalar(
        select(ProviderConnectionORM)
        .where(
            ProviderConnectionORM.workspace_id == workspace_id,
            ProviderConnectionORM.provider_type == provider,
            ProviderConnectionORM.status == "awaiting_authorization",
        )
        .order_by(ProviderConnectionORM.created_at.desc())
    )
