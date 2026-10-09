from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from lead_agent.domain.integrations import (
    PROVIDER_SETUP,
    ConnectionValidation,
    CredentialState,
    ManualConnectionInput,
    OAuthBroker,
    OAuthStart,
    ProviderConnectionValidator,
    ProviderRevoker,
    ProviderValidationStatus,
    RevocationResult,
    SecretStore,
)
from lead_agent.domain.oauth_errors import OAuthProviderError
from lead_agent.domain.provider_errors import ProviderErrorCategory, ProviderOperationError
from lead_agent.infrastructure.db.models import (
    CrmInboundSyncRunORM,
    CrmInboundSyncStateORM,
    CrmProviderRecordLinkORM,
    ProviderConnectionORM,
)
from lead_agent.infrastructure.oauth import pkce_challenge
from lead_agent.infrastructure.provider_lifecycle import (
    WorkspaceAuthorizedConnectionResolver,
    record_provider_lifecycle_audit,
)
from lead_agent.infrastructure.reasoning.configuration import (
    REASONING_PROVIDERS,
    validate_reasoning_model,
)
from lead_agent.infrastructure.repositories import (
    ensure_workspace,
    get_provider_connection,
)

CRM_PROVIDER_TYPES = {"mock", "salesforce", "hubspot"}
SENSITIVE_METADATA_FRAGMENTS = ("secret", "token", "password", "credential", "api_key")
REQUIRED_MANUAL_CREDENTIALS: dict[str, set[str]] = {
    "salesforce": {"client_id", "client_secret", "refresh_token"},
    "hubspot": {"access_token"},
    "openai": {"api_key"},
    "gemini": {"api_key"},
    "claude": {"api_key"},
}


def _credential_state(value: str) -> CredentialState:
    if value in {"configured", "missing", "expired", "invalid"}:
        return cast(CredentialState, value)
    return "invalid"


def _token_lifetime_seconds(values: dict[str, str]) -> int:
    try:
        seconds = int(values.get("expires_in", "3600"))
    except (TypeError, ValueError):
        return 3600
    return max(60, min(seconds, 31_536_000))


def _discard_connection_secret(connection: ProviderConnectionORM, secret_store: SecretStore) -> str:
    reference = connection.secret_reference
    connection.secret_reference = None
    if not reference:
        return "not_present"
    try:
        secret_store.invalidate(reference)
        secret_store.delete(reference)
    except Exception:
        return "cleanup_failed"
    return "deleted"


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
    unexpected = sorted(credentials.keys() - REQUIRED_MANUAL_CREDENTIALS.get(provider, set()))
    if unexpected:
        raise ValueError(f"unsupported credential fields: {', '.join(unexpected)}")


def _activate_connection(session: Session, connection: ProviderConnectionORM) -> None:
    # Gmail's mailbox identity is unknown until the read-only profile validation
    # succeeds. Keep OAuth-completed but unverified Gmail rows unschedulable.
    if connection.provider_type in {"gmail", "outlook", "google_calendar", "calendly"}:
        connection.is_active = False
        return
    # A workspace has one active account per provider. Keep the old connection
    # untouched while OAuth is pending and switch accounts only after the new
    # account has successfully completed provider verification.
    session.execute(
        update(ProviderConnectionORM)
        .where(
            ProviderConnectionORM.workspace_id == connection.workspace_id,
            ProviderConnectionORM.provider_type.in_(
                REASONING_PROVIDERS
                if connection.provider_type in REASONING_PROVIDERS
                else [connection.provider_type]
            ),
            ProviderConnectionORM.id != connection.id,
        )
        .values(is_active=False)
    )
    connection.is_active = True


def _rebind_mailbox_state(
    session: Session,
    connection: ProviderConnectionORM,
    mailbox_email: str,
    now: datetime,
    provider: str,
) -> None:
    """Move prior same-mailbox inbound state and message identity onto a verified connection."""

    mailbox = mailbox_email.strip().casefold()
    if not mailbox:
        return
    prior_connections = list(
        session.scalars(
            select(ProviderConnectionORM)
            .where(
                ProviderConnectionORM.workspace_id == connection.workspace_id,
                ProviderConnectionORM.provider_type == provider,
                ProviderConnectionORM.id != connection.id,
                func.lower(func.trim(ProviderConnectionORM.external_account_id)) == mailbox,
            )
            .with_for_update()
        )
    )
    if not prior_connections:
        connection.is_active = True
        return

    prior_ids = [prior.id for prior in prior_connections]
    states = list(
        session.scalars(
            select(CrmInboundSyncStateORM)
            .where(
                CrmInboundSyncStateORM.workspace_id == connection.workspace_id,
                CrmInboundSyncStateORM.provider == provider,
                CrmInboundSyncStateORM.object_type == "message",
                CrmInboundSyncStateORM.connection_id.in_([*prior_ids, connection.id]),
            )
            .order_by(CrmInboundSyncStateORM.updated_at.desc())
            .with_for_update()
        )
    )
    active_runs = list(
        session.scalars(
            select(CrmInboundSyncRunORM).where(
                CrmInboundSyncRunORM.workspace_id == connection.workspace_id,
                CrmInboundSyncRunORM.provider == provider,
                CrmInboundSyncRunORM.connection_id.in_([*prior_ids, connection.id]),
                CrmInboundSyncRunORM.status == "running",
            )
        )
    )
    # A claimed scheduler run must finish against the connection it already
    # resolved. Do not move its state underneath it.
    if active_runs:
        connection.is_active = False
        return

    target_state = next((state for state in states if state.connection_id == connection.id), None)
    prior_states = [state for state in states if state.connection_id != connection.id]
    selected_state = prior_states[0] if prior_states else target_state
    all_states = ([target_state] if target_state is not None else []) + prior_states

    if (
        selected_state is not None
        and target_state is not None
        and target_state.id != selected_state.id
    ):
        for run in session.scalars(
            select(CrmInboundSyncRunORM).where(
                CrmInboundSyncRunORM.state_id == target_state.id,
            )
        ):
            run.state_id = selected_state.id
            run.connection_id = connection.id
        session.delete(target_state)
        session.flush()

    if selected_state is not None:
        # Gmail history IDs are monotonically increasing decimal values. Keep
        # the furthest durable checkpoint if older duplicate logical states exist.
        cursors = [
            state.last_successful_cursor
            for state in all_states
            if state.last_successful_cursor
        ]
        if cursors:
            numeric_cursors = [
                (int(cursor["history_id"]), cursor)
                for cursor in cursors
                if isinstance(cursor.get("history_id"), str)
                and cursor["history_id"].isdigit()
            ]
            if numeric_cursors:
                selected_state.last_successful_cursor = max(
                    numeric_cursors, key=lambda pair: pair[0]
                )[1]
        selected_state.initial_import_completed = any(
            state.initial_import_completed for state in all_states
        )
        selected_state.last_successful_sync_at = max(
            (
                state.last_successful_sync_at
                for state in all_states
                if state.last_successful_sync_at
            ),
            default=selected_state.last_successful_sync_at,
        )
        selected_state.records_processed = max(
            (state.records_processed for state in all_states),
            default=selected_state.records_processed,
        )
        selected_state.connection_id = connection.id
        selected_state.next_sync_at = now if selected_state.enabled else None
        selected_state.status = "ready" if selected_state.enabled else "disabled"
        selected_state.error_category = None
        selected_state.error_code = None
        selected_state.last_error = None
        selected_state.updated_at = now

        for run in session.scalars(
            select(CrmInboundSyncRunORM).where(
                CrmInboundSyncRunORM.workspace_id == connection.workspace_id,
                CrmInboundSyncRunORM.provider == provider,
                CrmInboundSyncRunORM.connection_id.in_([*prior_ids, connection.id]),
            )
        ):
            run.connection_id = connection.id
            if run.state_id != selected_state.id:
                run.state_id = selected_state.id
        for redundant in prior_states:
            if redundant.id != selected_state.id:
                session.delete(redundant)

    links = list(
        session.scalars(
            select(CrmProviderRecordLinkORM)
            .where(
                CrmProviderRecordLinkORM.workspace_id == connection.workspace_id,
                CrmProviderRecordLinkORM.provider == provider,
                CrmProviderRecordLinkORM.object_type == "message",
                CrmProviderRecordLinkORM.connection_id.in_([*prior_ids, connection.id]),
            )
            .order_by(CrmProviderRecordLinkORM.last_seen_at.desc())
            .with_for_update()
        )
    )
    preferred: dict[str, CrmProviderRecordLinkORM] = {}
    for link in links:
        current = preferred.get(link.provider_record_id)
        if current is None or link.connection_id == connection.id:
            if current is not None:
                session.delete(current)
            preferred[link.provider_record_id] = link
        else:
            session.delete(link)
    for link in preferred.values():
        link.connection_id = connection.id

    for prior in prior_connections:
        prior.is_active = False
        prior.updated_at = now
    connection.is_active = False
    session.flush()
    connection.is_active = True


def _rebind_gmail_mailbox_state(
    session: Session,
    connection: ProviderConnectionORM,
    mailbox_email: str,
    now: datetime,
) -> None:
    _rebind_mailbox_state(session, connection, mailbox_email, now, "gmail")


def _rebind_outlook_mailbox_state(
    session: Session,
    connection: ProviderConnectionORM,
    mailbox_email: str,
    now: datetime,
) -> None:
    _rebind_mailbox_state(session, connection, mailbox_email, now, "outlook")


def create_manual_connection(
    session: Session,
    workspace_id: UUID,
    provider: str,
    request: ManualConnectionInput,
    secret_store: SecretStore,
    actor_id: str = "system",
) -> ProviderConnectionORM:
    _validate_manual_credentials(provider, request.auth_type, request.credentials)
    metadata = _safe_metadata(request.metadata)
    if provider in REASONING_PROVIDERS:
        metadata = {
            "selected_model": validate_reasoning_model(
                provider, metadata.get("selected_model")
            )
        }
    ensure_workspace(session, workspace_id)
    session.commit()
    reference = secret_store.store(workspace_id, provider, request.credentials)
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
        connection_metadata=metadata,
        is_active=False,
        disconnected_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    if request.activate and provider not in REASONING_PROVIDERS:
        _activate_connection(session, row)
    elif request.activate and provider in REASONING_PROVIDERS:
        # Permit a one-off verification probe without interrupting the current
        # verified provider. Successful validation performs the actual switch.
        row.is_active = True
    if provider in REASONING_PROVIDERS:
        record_provider_lifecycle_audit(
            session,
            row,
            action=f"{provider}_credential_configured",
            outcome="configured",
            actor_id=actor_id,
            details={"selected_model": metadata["selected_model"]},
        )
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
    actor_id: str,
) -> tuple[ProviderConnectionORM, OAuthStart]:
    setup = PROVIDER_SETUP.get(provider)
    if setup is None or "oauth2" not in setup.supported_auth:
        raise ValueError(f"OAuth is not supported for {provider}")
    ensure_workspace(session, workspace_id)
    session.commit()
    connection_id = uuid4()
    state = f"{connection_id}.{secrets.token_urlsafe(32)}"
    verifier = secrets.token_urlsafe(64)
    redirect_uri = f"{public_base_url.rstrip('/')}/v1/integrations/{provider}/oauth/callback"
    active_scopes = scopes or setup.required_scopes
    if provider == "hubspot" and set(active_scopes) != set(setup.required_scopes):
        raise ValueError(
            "HubSpot OAuth scopes must exactly match the configured OAuth and Contacts scopes"
        )
    if provider == "slack" and set(active_scopes) != set(setup.required_scopes):
        raise ValueError("Slack OAuth scopes must exactly match the configured notification scopes")
    if provider == "slack":
        # Request channel management on new authorizations, but keep the original
        # notification scopes as the verification minimum for existing tokens.
        active_scopes = [*active_scopes, "channels:join", "groups:read"]
    if provider == "gmail" and set(active_scopes) != set(setup.required_scopes):
        raise ValueError("Gmail OAuth scopes must exactly match mailbox read and send permissions")
    if provider == "outlook" and set(active_scopes) != set(setup.required_scopes):
        raise ValueError("Outlook OAuth scopes must exactly match the read-only Graph scope set")
    if provider in {"google_calendar", "calendly"} and set(active_scopes) != set(
        setup.required_scopes
    ):
        raise ValueError(f"{setup.display_name} OAuth scopes must match the configured permissions")
    authorization_url = broker.begin(
        provider,
        connection_id,
        state,
        pkce_challenge(verifier),
        redirect_uri,
        active_scopes,
    )
    temporary_reference = secret_store.store(
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
            "oauth_callback_provider": provider,
            "oauth_actor_id": actor_id,
        },
        is_active=False,
        disconnected_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    if provider == "gmail":
        record_provider_lifecycle_audit(
            session,
            row,
            action="oauth_initiated",
            outcome="started",
            actor_id=actor_id,
            details={"scopes": list(active_scopes)},
        )
        session.commit()
    return row, OAuthStart(
        connection_id=row.id,
        state=state,
        authorization_url=authorization_url,
    )


def _pending_oauth_transaction(
    session: Session,
    workspace_id: UUID,
    connection_id: UUID,
    state: str,
    secret_store: SecretStore,
    actor_id: str,
) -> ProviderConnectionORM:
    row = session.scalar(
        select(ProviderConnectionORM).where(
            ProviderConnectionORM.id == connection_id,
            ProviderConnectionORM.workspace_id == workspace_id,
        )
    )
    if row is None or row.status != "awaiting_authorization":
        raise ValueError("pending connection was not found")
    expected = row.connection_metadata.get("oauth_state_hash")
    actual = hashlib.sha256(state.encode()).hexdigest()
    if not isinstance(expected, str) or not secrets.compare_digest(expected, actual):
        raise ValueError("OAuth state validation failed")
    if row.connection_metadata.get("oauth_callback_provider") != row.provider_type:
        raise ValueError("OAuth callback association is invalid")
    if row.connection_metadata.get("oauth_actor_id") != actor_id:
        raise ValueError("OAuth transaction belongs to a different actor")
    expires_raw = row.connection_metadata.get("oauth_state_expires_at")
    if not isinstance(expires_raw, str) or datetime.fromisoformat(expires_raw) <= datetime.now(UTC):
        row.status = "authorization_expired"
        row.credential_state = "expired"
        cleanup = _discard_connection_secret(row, secret_store)
        row.last_validation_result = {
            "status": "authorization_expired",
            "local_secret": cleanup,
        }
        session.commit()
        raise ValueError("OAuth authorization request expired; reconnect and try again")
    return row


def reject_oauth_connection(
    session: Session,
    workspace_id: UUID,
    connection_id: UUID,
    state: str,
    secret_store: SecretStore,
    actor_id: str,
    error: OAuthProviderError,
) -> None:
    # Never invalidate a pending transaction from an untrusted or replayed state.
    row = _pending_oauth_transaction(
        session, workspace_id, connection_id, state, secret_store, actor_id
    )
    claimed = session.execute(
        update(ProviderConnectionORM)
        .where(
            ProviderConnectionORM.id == connection_id,
            ProviderConnectionORM.status == "awaiting_authorization",
        )
        .values(status="connection_failed", updated_at=datetime.now(UTC))
    )
    if claimed.rowcount != 1:
        session.rollback()
        raise ValueError("OAuth state has already been redeemed")
    session.commit()
    session.refresh(row)
    row.credential_state = "invalid"
    row.last_validation_result = {
        "status": "failed",
        "message": str(error),
        "error_code": error.error_code,
        "local_secret": _discard_connection_secret(row, secret_store),
    }
    session.commit()


def complete_oauth_connection(
    session: Session,
    workspace_id: UUID,
    connection_id: UUID,
    state: str,
    authorization_code: str,
    secret_store: SecretStore,
    broker: OAuthBroker,
    actor_id: str,
) -> ProviderConnectionORM:
    row = _pending_oauth_transaction(
        session, workspace_id, connection_id, state, secret_store, actor_id
    )

    # Claim the state before provider I/O. The compare-and-set is portable and
    # prevents two concurrent callbacks from exchanging the same authorization code.
    claimed: Any = session.execute(
        update(ProviderConnectionORM)
        .where(
            ProviderConnectionORM.id == connection_id,
            ProviderConnectionORM.workspace_id == workspace_id,
            ProviderConnectionORM.status == "awaiting_authorization",
        )
        .values(status="validating", updated_at=datetime.now(UTC))
    )
    if claimed.rowcount != 1:
        session.rollback()
        raise ValueError("OAuth state has already been redeemed")
    session.commit()
    session.refresh(row)

    pending_values = (
        secret_store.retrieve_for_workspace(workspace_id, row.provider_type, row.secret_reference)
        if row.secret_reference
        else None
    )
    verifier = (pending_values or {}).get("pkce_verifier")
    redirect_uri = (pending_values or {}).get("redirect_uri")
    if not verifier or not redirect_uri:
        row.status = "connection_failed"
        row.credential_state = "invalid"
        cleanup = _discard_connection_secret(row, secret_store)
        row.last_validation_result = {
            "status": "failed",
            "message": "OAuth PKCE transaction is unavailable",
            "local_secret": cleanup,
        }
        session.commit()
        raise ValueError("OAuth PKCE transaction is unavailable")
    try:
        values = broker.exchange(
            row.provider_type,
            authorization_code,
            verifier,
            redirect_uri,
        )
    except Exception as error:
        safe_error = (
            error if isinstance(error, OAuthProviderError)
            else OAuthProviderError(row.provider_type, "oauth_exchange_failed")
        )
        logging.getLogger(__name__).warning(
            "OAuth token exchange failed for provider=%s reason=%s",
            row.provider_type, safe_error.error_code,
        )
        row.status = "connection_failed"
        row.credential_state = "invalid"
        row.last_validation_result = {
            "status": "failed",
            "message": str(safe_error),
            "error_code": safe_error.error_code,
            "error_type": type(error).__name__,
        }
        row.last_validation_result["local_secret"] = _discard_connection_secret(row, secret_store)
        row.updated_at = datetime.now(UTC)
        session.commit()
        raise safe_error from None
    if row.secret_reference is None:
        raise ValueError("OAuth PKCE transaction is unavailable")
    try:
        row.secret_reference = secret_store.replace_for_workspace(
            workspace_id, row.provider_type, row.secret_reference, values
        )
    except Exception:
        row.status = "connection_failed"
        row.credential_state = "invalid"
        cleanup = _discard_connection_secret(row, secret_store)
        row.last_validation_result = {
            "status": "failed",
            "message": "OAuth credential storage failed",
            "local_secret": cleanup,
        }
        row.updated_at = datetime.now(UTC)
        session.commit()
        raise ValueError("OAuth credential storage failed") from None
    row.credential_state = "configured"
    row.status = "connected"
    row.account_name = values.get("account_name", row.account_name)
    row.external_account_id = values.get("external_account_id", row.external_account_id)
    row.updated_at = datetime.now(UTC)
    row.last_validation_at = datetime.now(UTC)
    row.last_validation_result = {
        "status": "not_verified",
        "message": "OAuth credentials stored; provider verification is still required",
    }
    token_metadata: dict[str, object] = {
        "refresh_available": bool(values.get("refresh_token")),
    }
    # Some OAuth providers (including Salesforce) omit expires_in even though
    # access tokens are short lived. Use the shared conservative lifetime
    # fallback so scheduled workers refresh before the provider rejects a token.
    if row.provider_type != "slack" or values.get("expires_in"):
        token_metadata["token_expires_at"] = (
            datetime.now(UTC) + timedelta(seconds=_token_lifetime_seconds(values))
        ).isoformat()
    row.connection_metadata = token_metadata
    _activate_connection(session, row)
    if row.provider_type == "slack":
        record_provider_lifecycle_audit(
            session,
            row,
            action="oauth_connect",
            outcome="connected",
            actor_id=actor_id,
            details={"scopes": list(row.scopes)},
        )
    if row.provider_type in {"gmail", "outlook", "google_calendar", "calendly"}:
        record_provider_lifecycle_audit(
            session,
            row,
            action="oauth_connect",
            outcome="connected",
            actor_id=actor_id,
            details={"scopes": list(row.scopes)},
        )
    session.commit()
    session.refresh(row)
    return row


def validate_connection(
    session: Session,
    workspace_id: UUID,
    connection_id: UUID,
    secret_store: SecretStore,
    broker: OAuthBroker | None = None,
    validator: ProviderConnectionValidator | None = None,
    actor_id: str = "system",
) -> tuple[ProviderConnectionORM, ConnectionValidation]:
    row = get_provider_connection(session, workspace_id, connection_id)
    if row is None:
        raise LookupError("provider connection not found")
    resolver = WorkspaceAuthorizedConnectionResolver(secret_store, broker)
    try:
        resolved = resolver.resolve_connection(
            session,
            workspace_id,
            connection_id,
            actor_id=actor_id,
            allow_unverified=True,
        )
    except ProviderOperationError as error:
        status: ProviderValidationStatus = (
            "expired"
            if error.category == "revoked"
            else "invalid_credentials"
            if error.category == "invalid_credentials"
            else "temporarily_unavailable"
            if error.retryable
            else "configuration_error"
        )
        validation = ConnectionValidation(
            valid=False,
            verified=False,
            status=status,
            credential_state=(
                "expired"
                if status == "expired"
                else "invalid"
                if status == "invalid_credentials"
                else _credential_state(row.credential_state)
            ),
            message=str(error),
            retryable=error.retryable,
            error_code=error.code,
            metadata={"category": error.category},
        )
        return _persist_validation(session, row, validation, actor_id)
    if resolved is None:
        validation = ConnectionValidation(
            valid=False,
            verified=False,
            status="configuration_error",
            credential_state=_credential_state(row.credential_state),
            message="Provider connection is inactive or unavailable.",
            error_code="provider_connection_unavailable",
        )
        return _persist_validation(session, row, validation, actor_id)
    expected = (
        {"access_token"}
        if row.auth_type == "oauth2"
        else REQUIRED_MANUAL_CREDENTIALS.get(row.provider_type, set())
    )
    missing = sorted(expected - set(resolved.credentials))
    if missing:
        validation = ConnectionValidation(
            valid=False,
            verified=False,
            status="configuration_error",
            credential_state="invalid",
            message=f"Missing required credential fields: {', '.join(missing)}",
            error_code="credential_fields_missing",
        )
        return _persist_validation(session, row, validation, actor_id)
    if validator is None:
        validation = ConnectionValidation(
            valid=False,
            verified=False,
            status="configuration_error",
            credential_state="configured",
            message="Live provider validation is not configured.",
            error_code="provider_validation_not_configured",
        )
        return _persist_validation(session, row, validation, actor_id)
    validation_credentials = dict(resolved.credentials)
    if row.provider_type in REASONING_PROVIDERS:
        try:
            validation_credentials["model"] = validate_reasoning_model(
                row.provider_type, row.connection_metadata.get("selected_model")
            )
        except ValueError:
            display_name = PROVIDER_SETUP[row.provider_type].display_name
            validation = ConnectionValidation(
                valid=False,
                verified=False,
                status="model_unavailable",
                credential_state="configured",
                message=f"The selected {display_name} model is not supported by this application.",
                retryable=False,
                error_code=f"{row.provider_type}_model_not_supported",
            )
            return _persist_validation(session, row, validation, actor_id)
    try:
        validation = validator.validate(
            row.provider_type, validation_credentials, list(resolved.scopes)
        )
    except ProviderOperationError as error:
        status_by_category: dict[ProviderErrorCategory, ProviderValidationStatus] = {
            "invalid_credentials": "invalid_credentials",
            "insufficient_permissions": "insufficient_permissions",
            "rate_limited": "rate_limited",
            "temporarily_unavailable": "temporarily_unavailable",
            "timeout": "temporarily_unavailable",
            "malformed_response": "malformed_response",
            "revoked": "revoked",
            "configuration": "configuration_error",
        }
        status = status_by_category.get(error.category, "configuration_error")
        credential_state: CredentialState = (
            "expired"
            if status in {"expired", "revoked"}
            else "invalid"
            if status == "invalid_credentials"
            else "configured"
        )
        validation = ConnectionValidation(
            valid=False,
            verified=False,
            status=status,
            credential_state=credential_state,
            message=str(error),
            retryable=error.retryable,
            error_code=error.code,
            metadata={"category": error.category, **error.metadata},
        )
    return _persist_validation(session, row, validation, actor_id)


def _persist_validation(
    session: Session,
    row: ProviderConnectionORM,
    validation: ConnectionValidation,
    actor_id: str,
) -> tuple[ProviderConnectionORM, ConnectionValidation]:
    now = datetime.now(UTC)
    if validation.verified and validation.status == "connected":
        row.status = "verified"
    elif validation.status == "expired":
        row.status = "expired"
    elif validation.status in {"invalid_credentials", "revoked"}:
        row.status = "needs_reauthorization"
    elif row.provider_type in REASONING_PROVIDERS and validation.status in {
        "quota_exhausted",
        "model_unavailable",
        "temporarily_unavailable",
        "provider_outage",
        "transport_failure",
        "rate_limited",
    }:
        row.status = "needs_attention"
    else:
        row.status = "error"
    row.last_validation_at = now
    row.last_validation_result = validation.model_dump(mode="json")
    if row.provider_type in REASONING_PROVIDERS:
        if validation.verified and validation.status == "connected":
            _activate_connection(session, row)
        else:
            row.is_active = False
    if row.provider_type in {
        "slack",
        "gmail",
        "outlook",
        "google_calendar",
        "calendly",
        *REASONING_PROVIDERS,
    } and validation.metadata:
        row.connection_metadata = {**row.connection_metadata, **validation.metadata}
        if isinstance(validation.metadata.get("slack_team_id"), str):
            row.external_account_id = str(validation.metadata["slack_team_id"])
        if isinstance(validation.metadata.get("slack_team_name"), str):
            row.account_name = str(validation.metadata["slack_team_name"])
        if isinstance(validation.metadata.get("gmail_email"), str):
            mailbox_identity = str(validation.metadata["gmail_email"]).strip().casefold()
            row.connection_metadata = {
                **row.connection_metadata,
                "gmail_email": mailbox_identity,
            }
            row.account_name = mailbox_identity
            row.external_account_id = mailbox_identity
        if isinstance(validation.metadata.get("outlook_email"), str):
            mailbox_identity = str(validation.metadata["outlook_email"]).strip().casefold()
            row.connection_metadata = {
                **row.connection_metadata,
                "outlook_email": mailbox_identity,
                "outlook_account_id": validation.metadata.get("outlook_account_id", ""),
            }
            row.account_name = mailbox_identity
            row.external_account_id = mailbox_identity
        calendar_identity = validation.metadata.get("google_calendar_name")
        if row.provider_type == "google_calendar" and isinstance(calendar_identity, str):
            row.account_name = calendar_identity
            row.external_account_id = str(
                validation.metadata.get("google_calendar_id") or "primary"
            )
        calendly_email = validation.metadata.get("calendly_email")
        if row.provider_type == "calendly" and isinstance(calendly_email, str):
            row.account_name = calendly_email
            row.external_account_id = str(
                validation.metadata.get("calendly_user_uri") or calendly_email
            )
    row.credential_state = validation.credential_state
    row.updated_at = now
    if row.provider_type in {"gmail", "outlook", "google_calendar", "calendly"}:
        if validation.verified and validation.status == "connected":
            if row.provider_type in {"gmail", "outlook"}:
                mailbox_key = "gmail_email" if row.provider_type == "gmail" else "outlook_email"
                verified_mailbox = validation.metadata.get(mailbox_key)
            else:
                verified_mailbox = (
                    validation.metadata.get("google_calendar_id")
                    if row.provider_type == "google_calendar"
                    else validation.metadata.get("calendly_user_uri")
                )
            if isinstance(verified_mailbox, str) and verified_mailbox.strip():
                session.execute(
                    update(ProviderConnectionORM)
                    .where(
                        ProviderConnectionORM.workspace_id == row.workspace_id,
                        ProviderConnectionORM.provider_type == row.provider_type,
                        ProviderConnectionORM.id != row.id,
                    )
                    .values(is_active=False)
                )
                if row.provider_type == "gmail":
                    _rebind_gmail_mailbox_state(session, row, verified_mailbox, now)
                elif row.provider_type == "outlook":
                    _rebind_outlook_mailbox_state(session, row, verified_mailbox, now)
                else:
                    row.is_active = True
            else:
                row.is_active = False
        else:
            row.is_active = False
    record_provider_lifecycle_audit(
        session,
        row,
        action=(
            f"{row.provider_type}_test_connection"
            if row.provider_type in REASONING_PROVIDERS
            else
            ("gmail_verified" if validation.verified else "gmail_validation")
            if row.provider_type == "gmail"
            else ("outlook_verified" if validation.verified else "outlook_validation")
            if row.provider_type == "outlook"
            else "connection_validation"
        ),
        outcome="verified" if validation.verified else validation.status,
        actor_id=actor_id,
        error_code=validation.error_code,
        details={
            "verified": validation.verified,
            "retryable": validation.retryable,
            "validation_status": validation.status,
        },
    )
    if row.provider_type in REASONING_PROVIDERS and validation.verified:
        record_provider_lifecycle_audit(
            session,
            row,
            action=f"{row.provider_type}_connection_verified",
            outcome="verified",
            actor_id=actor_id,
            details={"selected_model": row.connection_metadata.get("selected_model")},
        )
    session.commit()
    session.refresh(row)
    # Verified Gmail and CRM sources acquire new records automatically.
    # A new mailbox starts from its OWN verification history checkpoint, never
    # a different mailbox's history. Explicitly paused states stay paused.
    if (
        validation.verified
        and row.is_active
        and row.provider_type in {"salesforce", "hubspot", "gmail"}
    ):
        object_type = {
            "gmail": "message",
            "outlook": "message",
            "salesforce": "Lead",
            "hubspot": "contacts",
        }[row.provider_type]
        existing_state = session.scalar(
            select(CrmInboundSyncStateORM).where(
                CrmInboundSyncStateORM.workspace_id == row.workspace_id,
                CrmInboundSyncStateORM.connection_id == row.id,
                CrmInboundSyncStateORM.provider == row.provider_type,
                CrmInboundSyncStateORM.object_type == object_type,
            )
        )
        # A disabled state is an explicit user choice, including after a mailbox reconnect.
        if existing_state is None:
            from lead_agent.application.crm_inbound import configure_inbound_sync

            history_id = validation.metadata.get("history_id")
            configure_inbound_sync(
                session,
                row,
                enabled=True,
                object_type=object_type,
                interval_seconds=60,
                actor_id=actor_id,
                initial_cursor=(
                    {"history_id": str(history_id)}
                    if row.provider_type == "gmail" and history_id else None
                ),
            )
    return row, validation


def disconnect_connection(
    session: Session,
    workspace_id: UUID,
    connection_id: UUID,
    secret_store: SecretStore,
    revoker: ProviderRevoker | None = None,
    actor_id: str = "system",
) -> ProviderConnectionORM:
    row = get_provider_connection(session, workspace_id, connection_id)
    if row is None:
        raise LookupError("provider connection not found")
    now = datetime.now(UTC)
    reference = row.secret_reference
    secret_retrieval = "not_present"
    values: dict[str, str] | None = None
    if reference:
        try:
            values = secret_store.retrieve_for_workspace(workspace_id, row.provider_type, reference)
            secret_retrieval = "available" if values is not None else "unavailable"
        except Exception:
            secret_retrieval = "failed"
    row.secret_reference = None
    row.credential_state = "missing"
    row.status = "disconnected"
    row.is_active = False
    row.disconnected_at = now
    row.updated_at = now
    row.last_validation_at = now
    row.last_validation_result = {
        "status": "disconnected",
        "remote_revocation": "pending" if revoker is not None and values else "not_applicable",
        "local_secret": "pending" if reference else "not_present",
        "secret_retrieval": secret_retrieval,
    }
    session.commit()

    remote_revocation = "not_applicable"
    revocation = RevocationResult(status="unsupported")
    if revoker is not None and values is not None:
        try:
            revocation = revoker.revoke(row.provider_type, values)
        except Exception:
            revocation = RevocationResult(status="failed", error_code="provider_revocation_failed")
        remote_revocation = revocation.status
    local_secret = "not_present"
    if reference and values is not None:
        local_secret = "invalidated"
        try:
            secret_store.invalidate(reference)
            secret_store.delete(reference)
            local_secret = "deleted"
        except Exception:
            # The database reference was already removed and the connection deactivated,
            # so application use remains impossible even if backend cleanup needs repair.
            local_secret = "cleanup_failed"
    row.last_validation_result = {
        "status": "disconnected",
        "remote_revocation": remote_revocation,
        "local_secret": local_secret,
        "secret_retrieval": secret_retrieval,
    }
    record_provider_lifecycle_audit(
        session,
        row,
        action=(
            "gmail_disconnect"
            if row.provider_type == "gmail"
            else f"{row.provider_type}_disconnected"
            if row.provider_type in REASONING_PROVIDERS
            else "disconnect"
        ),
        outcome="disconnected",
        actor_id=actor_id,
        error_code=revocation.error_code,
        details={
            "remote_revocation": remote_revocation,
            "local_secret": local_secret,
            "secret_retrieval": secret_retrieval,
        },
    )
    session.commit()
    session.refresh(row)
    return row


def reconnect_connection(
    session: Session,
    workspace_id: UUID,
    connection_id: UUID,
    credentials: dict[str, str],
    secret_store: SecretStore,
    actor_id: str = "system",
) -> ProviderConnectionORM:
    row = get_provider_connection(session, workspace_id, connection_id)
    if row is None:
        raise LookupError("provider connection not found")
    _validate_manual_credentials(row.provider_type, row.auth_type, credentials)
    if row.secret_reference:
        row.secret_reference = secret_store.replace_for_workspace(
            workspace_id, row.provider_type, row.secret_reference, credentials
        )
    else:
        row.secret_reference = secret_store.store(workspace_id, row.provider_type, credentials)
    row.credential_state = "configured"
    row.status = "connected"
    row.last_validation_result = {"status": "not_verified"}
    row.disconnected_at = None
    row.updated_at = datetime.now(UTC)
    if row.provider_type in REASONING_PROVIDERS:
        # Replacing a key never switches providers before the new credential
        # has passed the provider-specific verification probe.
        row.is_active = False
    else:
        _activate_connection(session, row)
    if row.provider_type in REASONING_PROVIDERS:
        record_provider_lifecycle_audit(
            session,
            row,
            action=f"{row.provider_type}_credential_replaced",
            outcome="configured",
            actor_id=actor_id,
            details={"selected_model": row.connection_metadata.get("selected_model")},
        )
    session.commit()
    session.refresh(row)
    return row


def update_openai_model(
    session: Session,
    workspace_id: UUID,
    connection_id: UUID,
    model: str,
    *,
    actor_id: str = "system",
) -> ProviderConnectionORM:
    row = get_provider_connection(session, workspace_id, connection_id)
    if row is None or row.provider_type not in REASONING_PROVIDERS:
        raise LookupError("AI provider connection not found")
    selected_model = validate_reasoning_model(row.provider_type, model)
    previous_model = row.connection_metadata.get("selected_model")
    row.connection_metadata = {**row.connection_metadata, "selected_model": selected_model}
    if previous_model != selected_model:
        row.status = "connected"
        row.last_validation_result = {
            "status": "not_verified",
            "message": "Model changed; test the connection before workspace use.",
        }
        row.updated_at = datetime.now(UTC)
        record_provider_lifecycle_audit(
            session,
            row,
            action=f"{row.provider_type}_model_changed",
            outcome="configured",
            actor_id=actor_id,
            details={"previous_model": previous_model, "selected_model": selected_model},
        )
        session.commit()
        session.refresh(row)
    return row


def activate_reasoning_provider(
    session: Session,
    workspace_id: UUID,
    connection_id: UUID,
    *,
    actor_id: str = "system",
) -> ProviderConnectionORM:
    row = get_provider_connection(session, workspace_id, connection_id)
    if row is None:
        raise LookupError("AI provider connection not found")
    if row.provider_type not in REASONING_PROVIDERS:
        raise LookupError("AI provider connection not found")
    if row.status != "verified" or not row.secret_reference:
        raise ValueError("Verify the selected AI provider before making it active")
    _activate_connection(session, row)
    row.updated_at = datetime.now(UTC)
    record_provider_lifecycle_audit(
        session,
        row,
        action="reasoning_provider_selected",
        outcome="selected",
        actor_id=actor_id,
        details={
            "provider": row.provider_type,
            "selected_model": row.connection_metadata.get("selected_model"),
        },
    )
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
