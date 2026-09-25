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
    "configured",
    "connecting",
    "awaiting_authorization",
    "validating",
    "connected",
    "verified",
    "expired",
    "needs_reauthorization",
    "error",
    "needs_attention",
    "authorization_expired",
    "connection_failed",
    "reconnect_required",
    "disconnected",
]
CredentialState = Literal["configured", "missing", "expired", "invalid"]
AuthenticationType = Literal["oauth2", "private_token", "api_key", "development"]
ProviderValidationStatus = Literal[
    "connected",
    "invalid_credentials",
    "expired",
    "insufficient_permissions",
    "temporarily_unavailable",
    "rate_limited",
    "configuration_error",
    "malformed_response",
    "revoked",
]
VerificationStatus = Literal[
    "not_verified",
    "verified",
    "verification_failed",
    "reauthorization_required",
]


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
    connection_mode: Literal["workspace", "operator"] = "workspace"


PROVIDER_SETUP: dict[str, ProviderSetupMetadata] = {
    "salesforce": ProviderSetupMetadata(
        provider="salesforce",
        display_name="Salesforce",
        category="CRM",
        availability="available",
        preferred_auth="oauth2",
        supported_auth=["oauth2", "private_token"],
        dashboard_path="Managed by Ingenuity Systems · Salesforce OAuth app",
        credential_help="Sign in to Salesforce and approve access. No developer setup is required.",
        required_scopes=["api", "refresh_token"],
        permission_reason="Create or update Lead records and the configured sales fields.",
        documentation_url=(
            "https://developer.salesforce.com/docs/platform/api-rest/guide/"
            "intro-oauth-and-connected-apps.html"
        ),
        manual_fields=[],
    ),
    "hubspot": ProviderSetupMetadata(
        provider="hubspot",
        display_name="HubSpot",
        category="CRM",
        availability="available",
        preferred_auth="oauth2",
        supported_auth=["oauth2", "private_token"],
        dashboard_path="Managed by Ingenuity Systems · HubSpot public app",
        credential_help="Sign in to HubSpot, choose an account, and approve access.",
        required_scopes=[
            "oauth",
            "crm.objects.contacts.read",
            "crm.objects.contacts.write",
        ],
        permission_reason=(
            "Import HubSpot Contacts and allow explicitly initiated Contact create/update actions."
        ),
        documentation_url="https://developers.hubspot.com/docs/api/private-apps",
        manual_fields=[],
    ),
    "slack": ProviderSetupMetadata(
        provider="slack",
        display_name="Slack",
        category="Notifications",
        availability="available",
        preferred_auth="oauth2",
        supported_auth=["oauth2"],
        dashboard_path="Managed by Ingenuity Systems · Slack application",
        credential_help="Choose a Slack workspace and approve the lead-alert bot.",
        required_scopes=["chat:write", "channels:read"],
        permission_reason=(
            "Send routed lead alerts and list public channels for workspace channel selection."
        ),
        documentation_url="https://api.slack.com/authentication/oauth-v2",
        manual_fields=[],
    ),
    "gmail": ProviderSetupMetadata(
        provider="gmail",
        display_name="Gmail",
        category="Email",
        availability="foundation",
        preferred_auth="oauth2",
        supported_auth=["oauth2"],
        dashboard_path="Managed by Ingenuity Systems · Google OAuth consent",
        credential_help="Sign in with Google and approve read-only access to the intake mailbox.",
        required_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        permission_reason="Read approved inbound lead messages without accessing unrelated mail.",
        documentation_url="https://developers.google.com/gmail/api/auth/web-server",
    ),
    "outlook": ProviderSetupMetadata(
        provider="outlook",
        display_name="Outlook",
        category="Email",
        availability="foundation",
        preferred_auth="oauth2",
        supported_auth=["oauth2"],
        dashboard_path="Managed by Ingenuity Systems · Microsoft Entra application",
        credential_help="Sign in with Microsoft and approve access to the intake mailbox.",
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
        credential_help=(
            "OpenAI is operator-managed in this release. An administrator configures one "
            "project-scoped key outside the browser for the application runtime."
        ),
        required_scopes=["Responses API access"],
        permission_reason="Run schema-constrained context and intent analysis.",
        documentation_url="https://platform.openai.com/docs/quickstart",
        manual_fields=[],
        connection_mode="operator",
    ),
}


class SecretStore(Protocol):
    def store(self, workspace_id: UUID, provider: str, values: dict[str, str]) -> str: ...

    def retrieve(self, reference: str) -> dict[str, str] | None: ...

    def retrieve_for_workspace(
        self, workspace_id: UUID, provider: str, reference: str
    ) -> dict[str, str] | None: ...

    def replace(self, reference: str, values: dict[str, str]) -> str: ...

    def replace_for_workspace(
        self, workspace_id: UUID, provider: str, reference: str, values: dict[str, str]
    ) -> str: ...

    def invalidate(self, reference: str) -> None: ...

    def put(self, workspace_id: UUID, provider: str, values: dict[str, str]) -> str: ...

    def get(self, reference: str) -> dict[str, str] | None: ...

    def delete(self, reference: str) -> None: ...


class ConnectionValidation(BaseModel):
    valid: bool
    verified: bool = False
    status: ProviderValidationStatus
    credential_state: CredentialState
    message: str
    retryable: bool = False
    error_code: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class RevocationResult(BaseModel):
    status: Literal["confirmed", "unsupported", "failed"]
    error_code: str | None = None


class ProviderConnectionValidator(Protocol):
    def validate(
        self,
        provider: str,
        credentials: dict[str, str],
        scopes: list[str],
    ) -> ConnectionValidation: ...


class ProviderRevoker(Protocol):
    def revoke(self, provider: str, values: dict[str, str]) -> RevocationResult: ...


class OAuthStart(BaseModel):
    connection_id: UUID
    state: str
    authorization_url: str


class OAuthBroker(Protocol):
    def begin(
        self,
        provider: str,
        connection_id: UUID,
        state: str,
        code_challenge: str,
        redirect_uri: str,
        scopes: list[str],
    ) -> str: ...

    def exchange(
        self,
        provider: str,
        authorization_code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> dict[str, str]: ...

    def refresh(self, provider: str, refresh_token: str) -> dict[str, str]: ...

    def revoke(self, provider: str, values: dict[str, str]) -> bool:
        """Return true only when the remote provider confirms revocation."""
        ...


class ManualConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_name: str = Field(min_length=1, max_length=255)
    external_account_id: str | None = Field(default=None, max_length=255)
    auth_type: AuthenticationType
    scopes: list[str] = Field(default_factory=list)
    credentials: dict[str, str]
    metadata: dict[str, object] = Field(default_factory=dict)
    activate: bool = True
