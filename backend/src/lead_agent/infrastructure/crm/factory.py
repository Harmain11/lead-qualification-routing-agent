from __future__ import annotations

from functools import lru_cache
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.domain.crm import (
    CanonicalCrmPayload,
    CrmEntityReferences,
    CrmProvider,
    CrmProviderError,
    CrmSyncResult,
)
from lead_agent.domain.integrations import SecretStore
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.infrastructure.crm.hubspot import HubSpotCrmProvider
from lead_agent.infrastructure.crm.mock import MockCrmProvider
from lead_agent.infrastructure.crm.salesforce import SalesforceCrmProvider
from lead_agent.infrastructure.db.models import ProviderConnectionORM
from lead_agent.infrastructure.provider_lifecycle import (
    WorkspaceAuthorizedConnectionResolver,
    get_workspace_connection_resolver,
)
from lead_agent.infrastructure.secrets import get_secret_store
from lead_agent.settings.config import Settings, get_settings


class ConnectedCrmProvider:
    def __init__(self, provider: CrmProvider, connection_id: UUID) -> None:
        self._provider = provider
        self.provider_name = provider.provider_name
        self.connection_id = connection_id

    def sync(
        self,
        payload: CanonicalCrmPayload,
        existing: CrmEntityReferences | None = None,
    ) -> CrmSyncResult:
        return self._provider.sync(payload, existing)


class UnavailableConnectedCrmProvider:
    def __init__(self, provider_name: str, connection_id: UUID, message: str) -> None:
        self.provider_name = provider_name
        self.connection_id = connection_id
        self._message = message

    def sync(
        self,
        payload: CanonicalCrmPayload,
        existing: CrmEntityReferences | None = None,
    ) -> CrmSyncResult:
        del payload, existing
        raise CrmProviderError(
            self._message,
            code="workspace_connection_unavailable",
            category="configuration",
            retryable=False,
        )


def build_crm_provider(settings: Settings) -> CrmProvider:
    provider = settings.crm_provider.strip().lower()
    config = settings.load_crm_config()
    if provider in {"mock", "none", "deterministic"}:
        return MockCrmProvider()
    if provider == "salesforce":
        return SalesforceCrmProvider(
            client_id=settings.salesforce_client_id,
            client_secret=settings.salesforce_client_secret,
            refresh_token=settings.salesforce_refresh_token,
            login_base_url=settings.salesforce_login_base_url,
            api_version=settings.salesforce_api_version,
            config=config.salesforce,
            timeout_seconds=settings.crm_timeout_seconds,
        )
    if provider == "hubspot":
        return HubSpotCrmProvider(
            access_token=settings.hubspot_access_token,
            api_base_url=settings.hubspot_api_base_url,
            config=config.hubspot,
            timeout_seconds=settings.crm_timeout_seconds,
        )
    raise ValueError(f"Unsupported CRM_PROVIDER: {settings.crm_provider}")


def build_connected_crm_provider(
    settings: Settings,
    provider_type: str,
    credentials: dict[str, str],
) -> CrmProvider:
    config = settings.load_crm_config()
    if provider_type == "mock":
        return MockCrmProvider()
    if provider_type == "salesforce":
        return SalesforceCrmProvider(
            client_id=credentials.get("client_id", settings.salesforce_client_id),
            client_secret=credentials.get("client_secret", settings.salesforce_client_secret),
            refresh_token=credentials.get("refresh_token", ""),
            login_base_url=settings.salesforce_login_base_url,
            api_version=settings.salesforce_api_version,
            config=config.salesforce,
            timeout_seconds=settings.crm_timeout_seconds,
        )
    if provider_type == "hubspot":
        return HubSpotCrmProvider(
            access_token=credentials.get("access_token", ""),
            api_base_url=settings.hubspot_api_base_url,
            config=config.hubspot,
            timeout_seconds=settings.crm_timeout_seconds,
        )
    raise ValueError(f"Unsupported workspace CRM provider: {provider_type}")


class WorkspaceCrmProviderResolver:
    def __init__(
        self,
        settings: Settings,
        secret_store: SecretStore,
        connection_resolver: WorkspaceAuthorizedConnectionResolver | None = None,
    ) -> None:
        self.settings = settings
        self.secret_store = secret_store
        self.connection_resolver = connection_resolver or WorkspaceAuthorizedConnectionResolver(
            secret_store
        )

    def resolve(
        self,
        session: Session,
        workspace_id: UUID,
        fallback: CrmProvider | None = None,
        *,
        actor_id: str = "system",
    ) -> CrmProvider:
        provider_types = (
            {"salesforce", "hubspot"}
            if self.settings.app_env == "production"
            else {"mock", "salesforce", "hubspot"}
        )
        try:
            resolved = self.connection_resolver.resolve(
                session, workspace_id, provider_types, actor_id=actor_id
            )
        except ProviderOperationError:
            resolved = None
        if resolved is not None:
            provider = build_connected_crm_provider(
                self.settings, resolved.provider, resolved.credentials
            )
            return ConnectedCrmProvider(provider, resolved.connection_id)

        connection = session.scalar(
            select(ProviderConnectionORM).where(
                ProviderConnectionORM.workspace_id == workspace_id,
                ProviderConnectionORM.provider_type.in_(provider_types),
                ProviderConnectionORM.is_active.is_(True),
            )
        )
        if connection is not None:
            return UnavailableConnectedCrmProvider(
                connection.provider_type,
                connection.id,
                "The workspace CRM connection is unavailable and requires attention.",
            )
        if self.settings.app_env == "production":
            return UnavailableConnectedCrmProvider(
                self.settings.crm_provider,
                UUID(int=0),
                "No usable CRM connection is configured for this workspace.",
            )
        return fallback or build_crm_provider(self.settings)


@lru_cache
def get_crm_provider() -> CrmProvider:
    return build_crm_provider(get_settings())


@lru_cache
def get_workspace_crm_resolver() -> WorkspaceCrmProviderResolver:
    return WorkspaceCrmProviderResolver(
        get_settings(), get_secret_store(), get_workspace_connection_resolver()
    )
