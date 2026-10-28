from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ProviderType = Literal[
    "mock",
    "gohighlevel",
    "twilio",
    "aircall",
    "ringcentral",
    "vapi",
    "retell",
    "cloudtalk",
    "salesforce",
    "hubspot",
    "slack",
    "gmail",
    "outlook",
    "google_calendar",
    "calendly",
    "linkedin",
    "instagram",
    "messenger",
    "whatsapp_business",
    "clutch",
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
            "crm.schemas.contacts.read",
            "crm.schemas.contacts.write",
        ],
        permission_reason=(
            "Import and update HubSpot Contacts, and create missing Corral contact properties."
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
        availability="future",
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
    "instagram": ProviderSetupMetadata(
        provider="instagram",
        display_name="Instagram",
        category="Social",
        availability="foundation",
        preferred_auth="oauth2",
        supported_auth=["oauth2", "private_token"],
        dashboard_path="Meta Developer app · Corral Social Leads Visual",
        credential_help=(
            "Sign in with Meta to choose the Instagram account and grant the approved "
            "lead-intake permissions. "
            "A workspace admin can use a page or account token as a secure fallback."
        ),
        required_scopes=["instagram_business_basic", "instagram_business_manage_messages"],
        permission_reason="Receive lead data only through officially approved Instagram access.",
        documentation_url="https://developers.facebook.com/docs/instagram-api/",
        manual_fields=["access_token", "account_id"],
    ),
    "messenger": ProviderSetupMetadata(
        provider="messenger",
        display_name="Messenger",
        category="Social",
        availability="foundation",
        preferred_auth="oauth2",
        supported_auth=["oauth2", "private_token"],
        dashboard_path="Meta Developer app · Corral Social Leads Inbox",
        credential_help=(
            "Sign in with Meta to choose the Facebook Page and grant the approved "
            "Messenger permissions. "
            "A workspace admin can use a page access token as a secure fallback."
        ),
        required_scopes=[
            "pages_show_list",
            "pages_manage_metadata",
            "pages_messaging",
        ],
        permission_reason="Receive lead data only through officially approved Messenger access.",
        documentation_url="https://developers.facebook.com/docs/messenger-platform/",
        manual_fields=["page_access_token", "page_id"],
    ),
    "whatsapp_business": ProviderSetupMetadata(
        provider="whatsapp_business",
        display_name="WhatsApp Business",
        category="Social",
        availability="foundation",
        preferred_auth="oauth2",
        supported_auth=["oauth2", "private_token"],
        dashboard_path="Meta Developer app · Corral Social Leads Business",
        credential_help=(
            "Sign in with Meta to select the WhatsApp Business account and grant the "
            "approved messaging permissions. "
            "A workspace admin can use a Cloud API token as a secure fallback."
        ),
        required_scopes=[
            "whatsapp_business_management",
            "whatsapp_business_messaging",
        ],
        permission_reason=(
            "Receive lead data only through officially approved WhatsApp Business access."
        ),
        documentation_url="https://developers.facebook.com/docs/whatsapp/",
        manual_fields=["access_token", "business_account_id", "phone_number_id"],
    ),
    "clutch": ProviderSetupMetadata(
        provider="clutch",
        display_name="Clutch",
        category="Social",
        availability="foundation",
        preferred_auth="api_key",
        supported_auth=["api_key"],
        dashboard_path="Clutch account settings",
        credential_help="Paste the Clutch API key and workspace account ID used for lead intake.",
        required_scopes=["Clutch lead access"],
        permission_reason="Receive lead data only through officially approved Clutch access.",
        documentation_url="https://help.clutch.co/",
        manual_fields=["api_key", "account_id"],
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


# These private connections use workspace-owned credentials. A shared SaaS OAuth
# application is not required; vendor API access and account permissions still are.
_COMMUNICATION_SETUP = {
    "gohighlevel": (
        "GoHighLevel",
        "CRM",
        "private_token",
        ["access_token", "location_id"],
        ["contacts.readonly", "contacts.write", "locations.readonly"],
        "https://marketplace.gohighlevel.com/docs/2021-07-28/Authorization/PrivateIntegrationsToken/index.html",
        (
            "Create a sub-account Private Integration token with contact and location "
            "permissions. Enter its location ID. Contact import is opt-in; writes require your "
            "explicit action. Contact writeback sends contact details; qualification custom "
            "fields are not mapped."
        ),
    ),
    "twilio": (
        "Twilio",
        "Dialer",
        "private_token",
        ["account_sid", "auth_token", "from_number"],
        ["Voice calls"],
        "https://www.twilio.com/docs/voice/api/call-resource",
        (
            "Use your Twilio Account SID, Auth Token and a voice-enabled number in "
            "international +country-code format. Calls ring your rep number first and bridge "
            "to the lead after you answer."
        ),
    ),
    "aircall": (
        "Aircall",
        "Dialer",
        "private_token",
        ["api_id", "api_token", "user_id", "number_id"],
        ["Calls", "Users", "Numbers"],
        "https://developers.aircall.io/api-references",
        (
            "Create API credentials in Aircall, then enter the user ID and number ID used to "
            "call. The selected user must be available in Aircall Workspace."
        ),
    ),
    "ringcentral": (
        "RingCentral",
        "Dialer",
        "private_token",
        ["client_id", "client_secret", "jwt", "from_number"],
        ["RingOut", "ReadCallLog", "ReadAccounts"],
        "https://developers.ringcentral.com/guide/voice/quick-start",
        (
            "Create a private RingCentral REST app with JWT authentication, RingOut, "
            "ReadCallLog and ReadAccounts permissions. Enter its client credentials, your user "
            "JWT and the rep number to ring first."
        ),
    ),
    "vapi": (
        "Vapi",
        "Voice agent",
        "api_key",
        ["api_key", "assistant_id", "phone_number_id"],
        ["Calls", "Assistants", "Phone numbers"],
        "https://docs.vapi.ai/calls/outbound-calling",
        (
            "Use a private Vapi API key, a configured assistant ID and a Vapi phone-number ID. "
            "This starts the saved voice agent only after a rep confirms a call."
        ),
    ),
    "retell": (
        "Retell AI",
        "Voice agent",
        "api_key",
        ["api_key", "agent_id", "from_number"],
        ["Calls", "Agents", "Phone numbers"],
        "https://docs.retellai.com/api-references/create-phone-call",
        (
            "Use your Retell API key, a published voice-agent ID and an owned/imported phone "
            "number. Calls require explicit rep confirmation."
        ),
    ),
    "cloudtalk": (
        "CloudTalk",
        "Dialer",
        "private_token",
        ["access_key_id", "access_key_secret", "agent_id"],
        ["Agents", "Calls", "Call history", "Conversation Intelligence"],
        "https://developers.cloudtalk.io/api-reference/calls/make-a-call",
        (
            "Enter your CloudTalk Access Key ID and Access Key Secret, plus the numeric agent "
            "ID that should place calls. Verification only reads that agent; calls are submitted "
            "only after a rep confirms them. Call history and Conversation Intelligence use "
            "CloudTalk's official API endpoints."
        ),
    ),
}
for _provider, (
    _name,
    _category,
    _auth,
    _fields,
    _scopes,
    _docs,
    _help,
) in _COMMUNICATION_SETUP.items():
    PROVIDER_SETUP[_provider] = ProviderSetupMetadata(
        provider=_provider,
        display_name=_name,
        category=_category,
        availability="available",
        preferred_auth=_auth,
        supported_auth=[_auth],
        dashboard_path=f"{_name} account settings",
        credential_help=_help,
        required_scopes=_scopes,
        permission_reason=(
            "Read approved account data; every outgoing call or CRM write requires an "
            "authorized user action."
        ),
        documentation_url=_docs,
        manual_fields=_fields,
    )


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
