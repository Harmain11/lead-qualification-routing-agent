from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ProviderType = Literal[
    "mock",
    "salesforce",
    "hubspot",
    "slack",
    "gmail",
    "outlook",
    "linkedin",
    "openai",
]
ConnectionStatus = Literal[
    "pending",
    "connected",
    "needs_attention",
    "disconnected",
]
CredentialState = Literal["configured", "missing", "expired", "invalid"]
AuthenticationType = Literal["oauth2", "private_token", "api_key", "development"]


class ProviderSetupMetadata(BaseModel):
    provider: ProviderType
    display_name: str
    category: str
    availability: Literal["available", "foundation", "future"]
    preferred_auth: AuthenticationType
    supported_auth: list[AuthenticationType]
    dashboard_path: str
    credential_help: str
    required_scopes: list[str]
    permission_reason: str
    documentation_url: str
    manual_fields: list[str] = Field(default_factory=list)


PROVIDER_SETUP: dict[str, ProviderSetupMetadata] = {
    "salesforce": ProviderSetupMetadata(
        provider="salesforce",
        display_name="Salesforce",
        category="CRM",
        availability="available",
        preferred_auth="oauth2",
        supported_auth=["oauth2", "private_token"],
        dashboard_path="Setup → App Manager → External Client Apps",
        credential_help="Create an OAuth app and authorize a least-privilege integration user.",
        required_scopes=["api", "refresh_token/offline_access"],
        permission_reason="Create or update Lead records and the configured sales fields.",
        documentation_url=(
            "https://developer.salesforce.com/docs/platform/api-rest/guide/"
            "intro-oauth-and-connected-apps.html"
        ),
        manual_fields=["client_id", "client_secret", "refresh_token"],
    ),
    "hubspot": ProviderSetupMetadata(
        provider="hubspot",
        display_name="HubSpot",
        category="CRM",
        availability="available",
        preferred_auth="oauth2",
        supported_auth=["oauth2", "private_token"],
        dashboard_path="Settings → Integrations → Private Apps → Create private app",
        credential_help="Use OAuth for customer installs or a private-app token for one account.",
        required_scopes=[
            "crm.objects.contacts.read/write",
            "crm.objects.companies.read/write",
            "crm.objects.deals.read/write",
        ],
        permission_reason="Upsert contacts, companies, deals, and their associations.",
        documentation_url="https://developers.hubspot.com/docs/api/private-apps",
        manual_fields=["access_token"],
    ),
    "slack": ProviderSetupMetadata(
        provider="slack",
        display_name="Slack",
        category="Notifications",
        availability="foundation",
        preferred_auth="oauth2",
        supported_auth=["oauth2", "private_token"],
        dashboard_path="api.slack.com/apps → Create New App → OAuth & Permissions",
        credential_help="Install a bot to the selected workspace and choose a lead-alert channel.",
        required_scopes=["chat:write"],
        permission_reason="Deliver routed lead summaries to an approved channel.",
        documentation_url="https://api.slack.com/authentication/oauth-v2",
        manual_fields=["bot_token", "channel_id"],
    ),
    "gmail": ProviderSetupMetadata(
        provider="gmail",
        display_name="Gmail",
        category="Email",
        availability="future",
        preferred_auth="oauth2",
        supported_auth=["oauth2"],
        dashboard_path="Google Cloud Console → APIs & Services → Credentials",
        credential_help="Configure OAuth consent and Gmail scopes for the intake mailbox.",
        required_scopes=["gmail.readonly"],
        permission_reason="Read approved inbound lead messages without accessing unrelated mail.",
        documentation_url="https://developers.google.com/gmail/api/auth/web-server",
    ),
    "outlook": ProviderSetupMetadata(
        provider="outlook",
        display_name="Outlook",
        category="Email",
        availability="future",
        preferred_auth="oauth2",
        supported_auth=["oauth2"],
        dashboard_path="Microsoft Entra admin center → App registrations",
        credential_help="Register an application and authorize the intake mailbox.",
        required_scopes=["Mail.Read", "offline_access"],
        permission_reason="Read approved inbound lead messages through Microsoft Graph.",
        documentation_url="https://learn.microsoft.com/graph/auth-v2-user",
    ),
    "linkedin": ProviderSetupMetadata(
        provider="linkedin",
        display_name="LinkedIn",
        category="Lead intake",
        availability="future",
        preferred_auth="oauth2",
        supported_auth=["oauth2"],
        dashboard_path="LinkedIn Developer Portal → My Apps → Products",
        credential_help="Request an officially supported lead-form or approved API product.",
        required_scopes=["Provider-approved lead access"],
        permission_reason="Receive lead data only through officially approved LinkedIn access.",
        documentation_url="https://learn.microsoft.com/linkedin/",
    ),
    "openai": ProviderSetupMetadata(
        provider="openai",
        display_name="OpenAI",
        category="Reasoning",
        availability="foundation",
        preferred_auth="api_key",
        supported_auth=["api_key"],
        dashboard_path="OpenAI Platform → Project → API keys",
        credential_help="Create a project-scoped key with the minimum required project access.",
        required_scopes=["Responses API access"],
        permission_reason="Run schema-constrained context and intent analysis.",
        documentation_url="https://platform.openai.com/docs/quickstart",
        manual_fields=["api_key"],
    ),
}


class SecretStore(Protocol):
    def put(self, workspace_id: UUID, provider: str, values: dict[str, str]) -> str: ...

    def get(self, reference: str) -> dict[str, str] | None: ...

    def delete(self, reference: str) -> None: ...


class ConnectionValidation(BaseModel):
    valid: bool
    credential_state: CredentialState
    message: str


class OAuthStart(BaseModel):
    connection_id: UUID
    state: str
    authorization_url: str


class OAuthBroker(Protocol):
    def begin(self, provider: str, connection_id: UUID, state: str) -> str: ...

    def exchange(self, provider: str, authorization_code: str) -> dict[str, str]: ...


class ManualConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_name: str = Field(min_length=1, max_length=255)
    external_account_id: str | None = Field(default=None, max_length=255)
    auth_type: AuthenticationType
    scopes: list[str] = Field(default_factory=list)
    credentials: dict[str, str]
    metadata: dict[str, object] = Field(default_factory=dict)
    activate: bool = True
