from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from threading import Lock, RLock
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.domain.integrations import (
    ConnectionValidation,
    OAuthBroker,
    ProviderConnectionValidator,
    ProviderRevoker,
    RevocationResult,
    SecretStore,
)
from lead_agent.domain.provider_errors import (
    ProviderErrorCategory,
    ProviderOperationError,
    provider_http_error,
    provider_timeout_error,
)
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.crm.hubspot import (
    HubSpotConnectionValidator,
    HubSpotProviderRevoker,
)
from lead_agent.infrastructure.crm.salesforce import (
    SalesforceConnectionValidator,
    SalesforceProviderRevoker,
)
from lead_agent.infrastructure.db.models import (
    ProviderConnectionORM,
    ProviderLifecycleAuditORM,
)
from lead_agent.infrastructure.oauth import get_oauth_broker
from lead_agent.infrastructure.observability import current_request_id
from lead_agent.infrastructure.secrets import get_secret_store
from lead_agent.settings.config import get_settings

_REFRESH_SKEW = timedelta(seconds=60)
_CONNECTION_LOCKS: dict[UUID, RLock] = {}
_CONNECTION_LOCKS_GUARD = Lock()


def _connection_lock(connection_id: UUID) -> RLock:
    with _CONNECTION_LOCKS_GUARD:
        return _CONNECTION_LOCKS.setdefault(connection_id, RLock())


def record_provider_lifecycle_audit(
    session: Session,
    connection: ProviderConnectionORM,
    *,
    action: str,
    outcome: str,
    actor_id: str = "system",
    correlation_id: str | None = None,
    error_code: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    session.add(
        ProviderLifecycleAuditORM(
            id=uuid4(),
            workspace_id=connection.workspace_id,
            connection_id=connection.id,
            provider=connection.provider_type,
            actor_id=actor_id[:255],
            action=action,
            outcome=outcome,
            correlation_id=(correlation_id or current_request_id())[:255],
            error_code=error_code,
            details=dict(details or {}),
            created_at=datetime.now(UTC),
        )
    )


class DeferredProviderValidator:
    """Fail-closed validator until a provider supplies a real low-risk probe."""

    def validate(
        self,
        provider: str,
        credentials: dict[str, str],
        scopes: list[str],
    ) -> ConnectionValidation:
        del credentials, scopes
        return ConnectionValidation(
            valid=False,
            verified=False,
            status="configuration_error",
            credential_state="configured",
            message=f"Live {provider} connection validation is not implemented yet.",
            retryable=False,
            error_code="provider_validation_not_implemented",
        )


class ProviderConnectionValidatorRegistry:
    def __init__(
        self,
        validators: dict[str, ProviderConnectionValidator],
        fallback: ProviderConnectionValidator,
    ) -> None:
        self._validators = validators
        self._fallback = fallback

    def validate(
        self,
        provider: str,
        credentials: dict[str, str],
        scopes: list[str],
    ) -> ConnectionValidation:
        return self._validators.get(provider, self._fallback).validate(
            provider, credentials, scopes
        )


class ManualCredentialRevoker:
    """Removing a local secret does not revoke a key at an external provider."""

    def revoke(self, provider: str, values: dict[str, str]) -> RevocationResult:
        return RevocationResult(status="unsupported")


class OAuthBrokerRevoker:
    def __init__(self, broker: OAuthBroker) -> None:
        self._broker = broker

    def revoke(self, provider: str, values: dict[str, str]) -> RevocationResult:
        try:
            confirmed = self._broker.revoke(provider, values)
        except Exception:
            return RevocationResult(status="failed", error_code="provider_revocation_failed")
        return RevocationResult(status="confirmed" if confirmed else "unsupported")


class ProviderRevokerRegistry:
    def __init__(
        self,
        revokers: dict[str, ProviderRevoker],
        fallback: ProviderRevoker,
    ) -> None:
        self._revokers = revokers
        self._fallback = fallback

    def revoke(self, provider: str, values: dict[str, str]) -> RevocationResult:
        return self._revokers.get(provider, self._fallback).revoke(provider, values)


@dataclass(frozen=True)
class ResolvedProviderConnection:
    connection_id: UUID
    workspace_id: UUID
    provider: str
    credentials: dict[str, str]
    scopes: tuple[str, ...]
    status: str


class WorkspaceAuthorizedConnectionResolver:
    """Resolve and refresh one usable provider connection inside a workspace boundary."""

    def __init__(self, secret_store: SecretStore, broker: OAuthBroker | None = None) -> None:
        self.secret_store = secret_store
        self.broker = broker

    def resolve_for_context(
        self,
        session: Session,
        context: WorkspaceContext,
        provider_types: Collection[str],
        *,
        required_permission: str,
    ) -> ResolvedProviderConnection | None:
        if required_permission not in context.permissions:
            raise PermissionError(f"permission required: {required_permission}")
        return self.resolve(
            session, context.workspace_id, provider_types, actor_id=context.actor_id
        )

    def resolve(
        self,
        session: Session,
        workspace_id: UUID,
        provider_types: Collection[str],
        *,
        actor_id: str = "system",
    ) -> ResolvedProviderConnection | None:
        connection = session.scalar(
            select(ProviderConnectionORM)
            .where(
                ProviderConnectionORM.workspace_id == workspace_id,
                ProviderConnectionORM.provider_type.in_(set(provider_types)),
                ProviderConnectionORM.is_active.is_(True),
                ProviderConnectionORM.status.in_({"connected", "verified"}),
            )
            .order_by(ProviderConnectionORM.updated_at.desc())
        )
        if connection is None:
            return None
        return self.resolve_connection(session, workspace_id, connection.id, actor_id=actor_id)

    def resolve_connection(
        self,
        session: Session,
        workspace_id: UUID,
        connection_id: UUID,
        *,
        actor_id: str = "system",
        allow_unverified: bool = False,
    ) -> ResolvedProviderConnection | None:
        with _connection_lock(connection_id):
            locked = session.scalar(
                select(ProviderConnectionORM)
                .where(
                    ProviderConnectionORM.id == connection_id,
                    ProviderConnectionORM.workspace_id == workspace_id,
                )
                .with_for_update()
            )
            usable_statuses = (
                {"connected", "verified", "error"}
                if allow_unverified
                else {"connected", "verified"}
            )
            # OAuth rows remain inactive until a low-risk identity probe verifies
            # the account and required scopes.
            unverified_mailbox_probe = (
                allow_unverified
                and locked is not None
                and locked.provider_type in {"gmail", "outlook", "google_calendar", "calendly"}
                and locked.status == "connected"
            )
            if (
                locked is None
                or (not locked.is_active and not unverified_mailbox_probe)
                or locked.status not in usable_statuses
            ):
                return None
            credentials = self._load_credentials(locked)
            credentials = self._refresh_if_needed(session, locked, credentials, actor_id=actor_id)
            return ResolvedProviderConnection(
                connection_id=locked.id,
                workspace_id=locked.workspace_id,
                provider=locked.provider_type,
                credentials=dict(credentials),
                scopes=tuple(locked.scopes),
                status=locked.status,
            )

    def _load_credentials(self, connection: ProviderConnectionORM) -> dict[str, str]:
        if not connection.secret_reference:
            raise ProviderOperationError(
                "Provider connection has no available credential reference",
                provider=connection.provider_type,
                code="connection_secret_unavailable",
                category="configuration",
                retryable=False,
            )
        credentials = self.secret_store.retrieve_for_workspace(
            connection.workspace_id,
            connection.provider_type,
            connection.secret_reference,
        )
        if credentials is None:
            raise ProviderOperationError(
                "Provider connection credentials are unavailable",
                provider=connection.provider_type,
                code="connection_secret_unavailable",
                category="configuration",
                retryable=False,
            )
        return credentials

    def _refresh_if_needed(
        self,
        session: Session,
        connection: ProviderConnectionORM,
        credentials: dict[str, str],
        *,
        actor_id: str,
    ) -> dict[str, str]:
        if connection.auth_type != "oauth2":
            return credentials
        expires_raw = connection.connection_metadata.get("token_expires_at")
        if not isinstance(expires_raw, str):
            return credentials
        try:
            expires_at = datetime.fromisoformat(expires_raw)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
        except ValueError:
            return self._fail_refresh(
                session,
                connection,
                actor_id,
                code="invalid_token_expiration",
                message="Provider token expiration metadata is invalid",
                category="configuration",
            )
        if expires_at > datetime.now(UTC) + _REFRESH_SKEW:
            return credentials
        refresh_token = credentials.get("refresh_token")
        if not refresh_token:
            return self._fail_refresh(
                session,
                connection,
                actor_id,
                code="refresh_token_unavailable",
                message="Provider authorization expired and requires reconnection",
                category="revoked",
            )
        if self.broker is None:
            return self._fail_refresh(
                session,
                connection,
                actor_id,
                code="refresh_broker_unavailable",
                message="Provider refresh service is unavailable",
                category="configuration",
            )
        try:
            refreshed = self.broker.refresh(connection.provider_type, refresh_token)
        except httpx.TimeoutException:
            raise self._record_refresh_error(
                session, connection, actor_id, provider_timeout_error(connection.provider_type)
            ) from None
        except httpx.HTTPStatusError as error:
            translated = provider_http_error(connection.provider_type, error.response.status_code)
            raise self._record_refresh_error(session, connection, actor_id, translated) from error
        except Exception:
            translated = ProviderOperationError(
                "Provider token refresh failed",
                provider=connection.provider_type,
                code="provider_refresh_failed",
                category="temporarily_unavailable",
                retryable=True,
            )
            raise self._record_refresh_error(session, connection, actor_id, translated) from None
        access_token = refreshed.get("access_token")
        if not access_token:
            translated = ProviderOperationError(
                "Provider token refresh returned a malformed response",
                provider=connection.provider_type,
                code="provider_refresh_malformed_response",
                category="malformed_response",
                retryable=False,
            )
            raise self._record_refresh_error(session, connection, actor_id, translated)
        merged = {**credentials, **refreshed}
        merged.setdefault("refresh_token", refresh_token)
        if connection.secret_reference is None:
            raise RuntimeError("connection secret reference disappeared during refresh")
        connection.secret_reference = self.secret_store.replace_for_workspace(
            connection.workspace_id,
            connection.provider_type,
            connection.secret_reference,
            merged,
        )
        expires_in = _token_lifetime_seconds(refreshed)
        connection.connection_metadata = {
            **connection.connection_metadata,
            "token_expires_at": (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat(),
            "refresh_available": True,
        }
        connection.credential_state = "configured"
        connection.updated_at = datetime.now(UTC)
        record_provider_lifecycle_audit(
            session,
            connection,
            action="token_refresh",
            outcome="succeeded",
            actor_id=actor_id,
            details={
                "refresh_token_rotated": refreshed.get("refresh_token") not in {None, refresh_token}
            },
        )
        session.commit()
        return merged

    def _record_refresh_error(
        self,
        session: Session,
        connection: ProviderConnectionORM,
        actor_id: str,
        error: ProviderOperationError,
    ) -> ProviderOperationError:
        # OAuth refresh failures such as HubSpot's invalid_request response
        # commonly mean the refresh grant was revoked or belongs to an older
        # app configuration. Do not leave the connection looking verified;
        # outbound writes would otherwise continue to appear available while
        # every provider request is rejected.
        if (
            error.category in {"invalid_credentials", "revoked", "invalid_request"}
            and connection.auth_type == "oauth2"
        ):
            connection.status = "needs_reauthorization"
            connection.credential_state = "invalid"
        elif error.category == "configuration":
            connection.status = "error"
        record_provider_lifecycle_audit(
            session,
            connection,
            action="token_refresh",
            outcome="failed",
            actor_id=actor_id,
            error_code=error.code,
            details={"category": error.category, "retryable": error.retryable},
        )
        session.commit()
        return error

    def _fail_refresh(
        self,
        session: Session,
        connection: ProviderConnectionORM,
        actor_id: str,
        *,
        code: str,
        message: str,
        category: ProviderErrorCategory,
    ) -> dict[str, str]:
        error = ProviderOperationError(
            message,
            provider=connection.provider_type,
            code=code,
            category=category,
            retryable=False,
        )
        if category == "revoked":
            connection.status = "expired"
            connection.credential_state = "expired"
        else:
            connection.status = "error"
        record_provider_lifecycle_audit(
            session,
            connection,
            action="token_refresh",
            outcome="failed",
            actor_id=actor_id,
            error_code=code,
            details={"category": category, "retryable": False},
        )
        session.commit()
        raise error


def _token_lifetime_seconds(values: dict[str, str]) -> int:
    try:
        seconds = int(values.get("expires_in", "3600"))
    except (TypeError, ValueError):
        return 3600
    return max(60, min(seconds, 31_536_000))


@lru_cache
def get_provider_connection_validator() -> ProviderConnectionValidator:
    from lead_agent.infrastructure.calendar_providers import (
        CalendlyConnectionValidator,
        GoogleCalendarConnectionValidator,
    )
    from lead_agent.infrastructure.communications import CommunicationsValidator
    from lead_agent.infrastructure.gmail import GmailConnectionValidator
    from lead_agent.infrastructure.outlook import OutlookConnectionValidator
    from lead_agent.infrastructure.reasoning.openai_connection import OpenAIConnectionValidator
    from lead_agent.infrastructure.social import SocialConnectionValidator
    from lead_agent.infrastructure.slack import SlackConnectionValidator

    settings = get_settings()
    return ProviderConnectionValidatorRegistry(
        {
            "salesforce": SalesforceConnectionValidator(
                api_version=settings.salesforce_api_version,
                timeout_seconds=settings.crm_timeout_seconds,
            ),
            "hubspot": HubSpotConnectionValidator(
                oauth_api_version=settings.hubspot_oauth_api_version,
                timeout_seconds=settings.crm_timeout_seconds,
                client_id=settings.hubspot_client_id,
                client_secret=settings.hubspot_client_secret,
            ),
            "slack": SlackConnectionValidator(timeout_seconds=settings.crm_timeout_seconds),
            "gmail": GmailConnectionValidator(timeout_seconds=settings.crm_timeout_seconds),
            "outlook": OutlookConnectionValidator(timeout_seconds=settings.crm_timeout_seconds),
            "google_calendar": GoogleCalendarConnectionValidator(
                timeout_seconds=settings.crm_timeout_seconds
            ),
            "calendly": CalendlyConnectionValidator(timeout_seconds=settings.crm_timeout_seconds),
            **{
                key: CommunicationsValidator()
                for key in ("gohighlevel", "twilio", "aircall", "ringcentral", "vapi", "retell")
            },
            "openai": OpenAIConnectionValidator(timeout_seconds=settings.openai_timeout_seconds),
            **{
                provider: SocialConnectionValidator()
                for provider in ("instagram", "messenger", "whatsapp_business", "clutch")
            },
        },
        DeferredProviderValidator(),
    )


@lru_cache
def get_provider_revoker() -> ProviderRevoker:
    from lead_agent.infrastructure.slack import SlackProviderRevoker

    settings = get_settings()
    return ProviderRevokerRegistry(
        {
            "salesforce": SalesforceProviderRevoker(
                login_base_url=settings.salesforce_login_base_url,
                timeout_seconds=settings.crm_timeout_seconds,
            ),
            "hubspot": HubSpotProviderRevoker(
                oauth_api_version=settings.hubspot_oauth_api_version,
                timeout_seconds=settings.crm_timeout_seconds,
                client_id=settings.hubspot_client_id,
                client_secret=settings.hubspot_client_secret,
            ),
            "slack": SlackProviderRevoker(),
            **{
                provider: ManualCredentialRevoker()
                for provider in (
                    "gohighlevel",
                    "twilio",
                    "aircall",
                    "ringcentral",
                    "vapi",
                    "retell",
                    "instagram",
                    "messenger",
                    "whatsapp_business",
                    "clutch",
                )
            },
        },
        OAuthBrokerRevoker(get_oauth_broker()),
    )


@lru_cache
def get_workspace_connection_resolver() -> WorkspaceAuthorizedConnectionResolver:
    return WorkspaceAuthorizedConnectionResolver(get_secret_store(), get_oauth_broker())
