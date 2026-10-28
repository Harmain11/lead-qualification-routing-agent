from __future__ import annotations

from functools import lru_cache
from uuid import UUID

from sqlalchemy.orm import Session

from lead_agent.domain.crm_inbound import CrmInboundProvider
from lead_agent.domain.integrations import SecretStore
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.infrastructure.crm.gohighlevel import GoHighLevelCrmProvider
from lead_agent.infrastructure.crm.hubspot import HubSpotInboundCrmProvider
from lead_agent.infrastructure.crm.salesforce import SalesforceInboundCrmProvider
from lead_agent.infrastructure.db.models import ProviderConnectionORM
from lead_agent.infrastructure.gmail import GmailInboundProvider
from lead_agent.infrastructure.outlook import OutlookInboundProvider
from lead_agent.infrastructure.provider_lifecycle import (
    WorkspaceAuthorizedConnectionResolver,
    get_workspace_connection_resolver,
)
from lead_agent.infrastructure.secrets import get_secret_store
from lead_agent.settings.config import Settings, get_settings


class WorkspaceInboundCrmProviderResolver:
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
        connection_id: UUID,
        *,
        actor_id: str,
    ) -> CrmInboundProvider:
        resolved = self.connection_resolver.resolve_connection(
            session,
            workspace_id,
            connection_id,
            actor_id=actor_id,
        )
        if resolved is None:
            raise ProviderOperationError(
                "The CRM connection is unavailable for inbound synchronization",
                provider="crm",
                code="crm_inbound_connection_unavailable",
                category="configuration",
                retryable=False,
            )
        if resolved.provider == "gohighlevel":
            return GoHighLevelCrmProvider(resolved.connection_id, resolved.credentials)
        if resolved.provider == "hubspot":
            return HubSpotInboundCrmProvider(
                connection_id=resolved.connection_id,
                access_token=resolved.credentials.get("access_token", ""),
                api_base_url=self.settings.hubspot_api_base_url,
                timeout_seconds=self.settings.crm_timeout_seconds,
            )
        if resolved.provider == "gmail":
            connection = session.get(ProviderConnectionORM, resolved.connection_id)
            metadata = connection.connection_metadata if connection is not None else {}
            mailbox = metadata.get("gmail_email") if isinstance(metadata, dict) else None
            if not isinstance(mailbox, str) or "@" not in mailbox:
                raise ProviderOperationError(
                    "The connected Gmail account identity is unavailable",
                    provider="gmail",
                    code="gmail_mailbox_identity_unavailable",
                    category="configuration",
                    retryable=False,
                )
            return GmailInboundProvider(
                connection_id=resolved.connection_id,
                access_token=resolved.credentials.get("access_token", ""),
                mailbox_email=mailbox,
                timeout_seconds=self.settings.crm_timeout_seconds,
            )
        if resolved.provider == "outlook":
            connection = session.get(ProviderConnectionORM, resolved.connection_id)
            metadata = connection.connection_metadata if connection is not None else {}
            mailbox = metadata.get("outlook_email") if isinstance(metadata, dict) else None
            if not isinstance(mailbox, str) or "@" not in mailbox:
                raise ProviderOperationError(
                    "The connected Outlook account identity is unavailable",
                    provider="outlook",
                    code="outlook_mailbox_identity_unavailable",
                    category="configuration",
                    retryable=False,
                )
            return OutlookInboundProvider(
                connection_id=resolved.connection_id,
                access_token=resolved.credentials.get("access_token", ""),
                mailbox_email=mailbox,
                initial_sync_days=self.settings.outlook_initial_sync_days,
                timeout_seconds=self.settings.crm_timeout_seconds,
            )
        if resolved.provider != "salesforce":
            raise ProviderOperationError(
                "Inbound synchronization is not implemented for this provider",
                provider=resolved.provider,
                code=f"{resolved.provider}_inbound_not_implemented",
                category="configuration",
                retryable=False,
            )
        config = self.settings.load_crm_config().salesforce
        return SalesforceInboundCrmProvider(
            connection_id=resolved.connection_id,
            client_id=resolved.credentials.get("client_id", self.settings.salesforce_client_id),
            client_secret=resolved.credentials.get(
                "client_secret", self.settings.salesforce_client_secret
            ),
            refresh_token=resolved.credentials.get("refresh_token", ""),
            access_token=resolved.credentials.get("access_token", ""),
            instance_url=resolved.credentials.get("instance_url", ""),
            login_base_url=self.settings.salesforce_login_base_url,
            api_version=self.settings.salesforce_api_version,
            config=config,
            timeout_seconds=self.settings.crm_timeout_seconds,
        )


@lru_cache
def get_workspace_inbound_crm_resolver() -> WorkspaceInboundCrmProviderResolver:
    return WorkspaceInboundCrmProviderResolver(
        get_settings(), get_secret_store(), get_workspace_connection_resolver()
    )
