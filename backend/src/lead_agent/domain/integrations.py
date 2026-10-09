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
    "google_calendar",
    "calendly",
    "linkedin",
    "openai",
    "gemini",
    "claude",
    "deepseek",
    "xai",
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
    "quota_exhausted",
    "model_unavailable",
    "temporarily_unavailable",
    "provider_outage",
    "transport_failure",
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
        credential_help=(
            "Install the Corral Salesforce package in the organization you want to connect "
            "before authorizing. Connect opens the installer and a step-by-step guide. "
            "Then sign in to Salesforce and approve access. If authorization is denied, the "
            "Salesforce app owner must allow your user to authorize the app and enable "
            "its API and refresh-token permissions."
        ),
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
        credential_help=(
            "Choose a Slack workspace and approve the lead-alert bot. An undistributed app "
            "can only connect to the workspace where it was created; the app owner must "
            "enable App Distribution to support other workspaces."
        ),
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
        availability="available",
        preferred_auth="oauth2",
        supported_auth=["oauth2"],
        dashboard_path="Managed by Ingenuity Systems · Google OAuth consent",
        credential_help=(
            "Sign in with Google to read the intake mailbox and send one confirmation to new leads."
        ),
        required_scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        ],
        permission_reason=(
            "Read approved inbound lead messages and send the configured lead confirmation email."
        ),
        documentation_url="https://developers.google.com/gmail/api/auth/web-server",
    ),
    "google_calendar": ProviderSetupMetadata(
        provider="google_calendar",
        display_name="Google Calendar",
        category="Calendar",
        availability="available",
        preferred_auth="oauth2",
        supported_auth=["oauth2"],
        dashboard_path="Managed by Ingenuity Systems · Google OAuth consent",
        credential_help=(
            "Sign in with the Google account this workspace should use for availability "
            "and confirmed meeting creation."
        ),
        required_scopes=[
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/calendar.events.freebusy",
            "https://www.googleapis.com/auth/calendar.calendars.readonly",
        ],
        permission_reason=(
            "Read calendar metadata and free/busy availability; create events only after a "
            "workspace admin confirms."
        ),
        documentation_url="https://developers.google.com/calendar/api/auth",
    ),
    "calendly": ProviderSetupMetadata(
        provider="calendly",
        display_name="Calendly",
        category="Calendar",
        availability="available",
        preferred_auth="oauth2",
        supported_auth=["oauth2"],
        dashboard_path="Managed by Ingenuity Systems · Calendly OAuth app",
        credential_help=(
            "Connect the Calendly account used by this workspace. Direct booking requires a "
            "Calendly Standard plan or above."
        ),
        required_scopes=[
            "users:read",
            "event_types:read",
            "availability:read",
            "scheduled_events:read",
            "scheduled_events:write",
        ],
        permission_reason=(
            "Read event types and availability; book a selected slot only after an admin confirms."
        ),
        documentation_url="https://developer.calendly.com/api-docs/calendly-o-auth-2-0",
    ),
    "outlook": ProviderSetupMetadata(
        provider="outlook",
        display_name="Microsoft Outlook",
        category="Email",
        availability="available",
        preferred_auth="oauth2",
        supported_auth=["oauth2"],
        dashboard_path="Managed by Ingenuity Systems · Microsoft Entra application",
        credential_help="Sign in with Microsoft and approve read-only access to the Outlook Inbox.",
        required_scopes=["openid", "profile", "email", "offline_access", "Mail.Read"],
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
        credential_help="Lead-source integration",
        required_scopes=["Provider-approved lead access"],
        permission_reason="Receive lead data only through officially approved LinkedIn access.",
        documentation_url="https://learn.microsoft.com/linkedin/",
    ),
    "openai": ProviderSetupMetadata(
        provider="openai",
        display_name="OpenAI",
        category="AI analysis",
        availability="available",
        preferred_auth="api_key",
        supported_auth=["api_key"],
        dashboard_path="OpenAI Platform → Project → API keys",
        credential_help=(
            "Connect a dedicated OpenAI Project API key for this workspace. OpenAI API "
            "usage is billed directly to your OpenAI account."
        ),
        required_scopes=["Responses API access"],
        permission_reason="Run schema-constrained context and intent analysis.",
        documentation_url="https://developers.openai.com/api/docs/quickstart",
        manual_fields=["api_key"],
        connection_mode="workspace",
    ),
    "gemini": ProviderSetupMetadata(
        provider="gemini",
        display_name="Google Gemini",
        category="AI analysis",
        availability="available",
        preferred_auth="api_key",
        supported_auth=["api_key"],
        dashboard_path="Google AI Studio → API keys",
        credential_help=(
            "Connect a Google AI Studio API key for this workspace. Gemini API usage is "
            "billed directly to your Google account."
        ),
        required_scopes=["Generative Language API access"],
        permission_reason="Run schema-constrained context and intent analysis.",
        documentation_url="https://ai.google.dev/gemini-api/docs/api-key",
        manual_fields=["api_key"],
        connection_mode="workspace",
    ),
    "claude": ProviderSetupMetadata(
        provider="claude",
        display_name="Anthropic Claude",
        category="AI analysis",
        availability="available",
        preferred_auth="api_key",
        supported_auth=["api_key"],
        dashboard_path="Anthropic Console → API keys",
        credential_help=(
            "Connect an Anthropic API key for this workspace. Claude API usage is "
            "billed directly to your Anthropic account."
        ),
        required_scopes=["Messages API access"],
        permission_reason="Run schema-constrained context and intent analysis.",
        documentation_url="https://docs.anthropic.com/en/api/getting-started",
        manual_fields=["api_key"],
        connection_mode="workspace",
    ),
    "deepseek": ProviderSetupMetadata(
        provider="deepseek",
        display_name="DeepSeek",
        category="AI analysis",
        availability="available",
        preferred_auth="api_key",
        supported_auth=["api_key"],
        dashboard_path="DeepSeek Platform → API keys",
        credential_help=(
            "Connect a DeepSeek API key for this workspace. DeepSeek API usage is "
            "billed directly to your DeepSeek account."
        ),
        required_scopes=["Chat Completions API access"],
        permission_reason="Run JSON-constrained context and intent analysis.",
        documentation_url="https://api-docs.deepseek.com/",
        manual_fields=["api_key"],
        connection_mode="workspace",
    ),
    "xai": ProviderSetupMetadata(
        provider="xai",
        display_name="xAI",
        category="AI analysis",
        availability="available",
        preferred_auth="api_key",
        supported_auth=["api_key"],
        dashboard_path="xAI Console → API keys",
        credential_help=(
            "Connect an xAI API key for this workspace. xAI API usage is billed "
            "directly to your xAI account."
        ),
        required_scopes=["Responses API access"],
        permission_reason="Run schema-constrained context and intent analysis.",
        documentation_url="https://docs.x.ai/overview",
        manual_fields=["api_key"],
        connection_mode="workspace",
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
