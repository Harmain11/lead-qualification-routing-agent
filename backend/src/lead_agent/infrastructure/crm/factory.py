from __future__ import annotations

from functools import lru_cache
from uuid import UUID

from sqlalchemy.orm import Session

from lead_agent.domain.crm import (
    CanonicalCrmPayload,
    CrmEntityReferences,
    CrmProvider,
    CrmProviderError,
    CrmSyncResult,
)
from lead_agent.domain.integrations import SecretStore
from lead_agent.infrastructure.crm.hubspot import HubSpotCrmProvider
from lead_agent.infrastructure.crm.mock import MockCrmProvider
from lead_agent.infrastructure.crm.salesforce import SalesforceCrmProvider
from lead_agent.infrastructure.repositories import get_active_provider_connection
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
    def __init__(self, settings: Settings, secret_store: SecretStore) -> None:
        self.settings = settings
        self.secret_store = secret_store

    def resolve(
        self,
        session: Session,
        workspace_id: UUID,
        fallback: CrmProvider | None = None,
    ) -> CrmProvider:
        provider_types = (
            {"salesforce", "hubspot"}
            if self.settings.app_env == "production"
            else {"mock", "salesforce", "hubspot"}
        )
        connection = get_active_provider_connection(session, workspace_id, provider_types)
        if connection is None:
            return fallback or build_crm_provider(self.settings)
        credentials = (
            self.secret_store.get(connection.secret_reference)
            if connection.secret_reference
            else None
        )
        if credentials is None:
            return UnavailableConnectedCrmProvider(
                connection.provider_type,
                connection.id,
                "The workspace CRM connection has no available secret reference.",
            )
        provider = build_connected_crm_provider(
            self.settings, connection.provider_type, credentials
        )
        return ConnectedCrmProvider(provider, connection.id)


@lru_cache
def get_crm_provider() -> CrmProvider:
    return build_crm_provider(get_settings())


@lru_cache
def get_workspace_crm_resolver() -> WorkspaceCrmProviderResolver:
    return WorkspaceCrmProviderResolver(get_settings(), get_secret_store())
