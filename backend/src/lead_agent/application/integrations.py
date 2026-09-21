from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
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
from lead_agent.infrastructure.repositories import (
    ensure_workspace,
    get_provider_connection,
)

CRM_PROVIDER_TYPES = {"mock", "salesforce", "hubspot"}
SENSITIVE_METADATA_FRAGMENTS = ("secret", "token", "password", "credential", "api_key")


def _safe_metadata(metadata: dict[str, object]) -> dict[str, object]:
    unsafe = [
        key
        for key in metadata
        if any(fragment in key.lower() for fragment in SENSITIVE_METADATA_FRAGMENTS)
    ]
    if unsafe:
        raise ValueError("connection metadata cannot contain credential values")
    return dict(metadata)


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
    if provider not in PROVIDER_SETUP and provider != "mock":
        raise ValueError(f"unsupported provider: {provider}")
    if not request.credentials or any(not value.strip() for value in request.credentials.values()):
        raise ValueError("credential values are required")
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
) -> tuple[ProviderConnectionORM, OAuthStart]:
    setup = PROVIDER_SETUP.get(provider)
    if setup is None or "oauth2" not in setup.supported_auth:
        raise ValueError(f"OAuth is not supported for {provider}")
    ensure_workspace(session, workspace_id)
    state = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    row = ProviderConnectionORM(
        id=uuid4(),
        workspace_id=workspace_id,
        provider_type=provider,
        external_account_id=None,
        account_name=account_name,
        auth_type="oauth2",
        status="pending",
        scopes=scopes or setup.required_scopes,
        secret_reference=None,
        credential_state="missing",
        last_validation_at=None,
        last_validation_result={"status": "authorization_pending"},
        connection_metadata={
            "oauth_state_hash": hashlib.sha256(state.encode()).hexdigest(),
            "oauth_broker": "development",
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
        authorization_url=broker.begin(provider, row.id, state),
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
    row = get_provider_connection(session, workspace_id, connection_id)
    if row is None or row.status != "pending":
        raise ValueError("pending connection was not found")
    expected = row.connection_metadata.get("oauth_state_hash")
    actual = hashlib.sha256(state.encode()).hexdigest()
    if not isinstance(expected, str) or not secrets.compare_digest(expected, actual):
        raise ValueError("OAuth state validation failed")
    values = broker.exchange(row.provider_type, authorization_code)
    row.secret_reference = secret_store.put(workspace_id, row.provider_type, values)
    row.credential_state = "configured"
    row.status = "connected"
    row.updated_at = datetime.now(UTC)
    row.connection_metadata = {
        key: value for key, value in row.connection_metadata.items() if key != "oauth_state_hash"
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
) -> tuple[ProviderConnectionORM, ConnectionValidation]:
    row = get_provider_connection(session, workspace_id, connection_id)
    if row is None:
        raise LookupError("provider connection not found")
    values = secret_store.get(row.secret_reference) if row.secret_reference else None
    required: dict[str, set[str]] = {
        "salesforce": {"client_id", "client_secret", "refresh_token"},
        "hubspot": {"access_token"},
        "slack": {"bot_token", "channel_id"},
        "openai": {"api_key"},
    }
    missing = sorted(required.get(row.provider_type, set()) - set(values or {}))
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
) -> ProviderConnectionORM:
    row = get_provider_connection(session, workspace_id, connection_id)
    if row is None:
        raise LookupError("provider connection not found")
    if row.secret_reference:
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
    if not credentials or any(not value.strip() for value in credentials.values()):
        raise ValueError("credential values are required")
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
            ProviderConnectionORM.status == "pending",
        )
        .order_by(ProviderConnectionORM.created_at.desc())
    )
