"""Public OAuth diagnostics. Never include provider response bodies or credentials."""

from lead_agent.domain.integrations import PROVIDER_SETUP

_MESSAGES = {
    "access_denied": (
        "Authorization was declined. Approve access and check that the OAuth app "
        "permits your user to authorize it."
    ),
    "invalid_scope": (
        "The OAuth app does not allow the requested permissions. Enable the required "
        "scopes in the provider's app settings."
    ),
    "invalid_client": (
        "The OAuth application credentials were rejected. Check the server's client ID "
        "and client secret."
    ),
    "invalid_client_id": (
        "The OAuth client ID was rejected. Check the server's OAuth application configuration."
    ),
    "redirect_uri_mismatch": (
        "The callback URL does not match the provider's registered OAuth redirect URL."
    ),
    "bad_redirect_uri": (
        "The callback URL does not match the provider's registered OAuth redirect URL."
    ),
    "invalid_redirect_uri": (
        "The callback URL does not match the provider's registered OAuth redirect URL."
    ),
    "invalid_grant": (
        "The authorization code or PKCE transaction was rejected. Start a fresh connection "
        "and check the OAuth app's PKCE and callback settings."
    ),
    "invalid_code": "The authorization code expired or was already used. Start a fresh connection.",
    "unauthorized_client": (
        "The OAuth app is not permitted to use this authorization flow. Enable the "
        "authorization-code flow and allow your user to authorize the app."
    ),
    "invalid_request": (
        "The provider rejected the authorization request. Check the OAuth app's callback URL, "
        "permissions and PKCE settings."
    ),
    "unsupported_response_type": "Enable the authorization-code flow in the OAuth app's settings.",
    "server_error": "The provider encountered an error. Try connecting again later.",
    "temporarily_unavailable": (
        "The provider is temporarily unavailable. Try connecting again later."
    ),
    "invalid_team_for_non_distributed_app": (
        "This Slack app can only connect to the workspace where it was created. Select that "
        "workspace, or ask the app owner to enable Slack App Distribution for other workspaces."
    ),
    "missing_authorization_code": (
        "The provider did not return an authorization code. Start a fresh connection "
        "and approve access."
    ),
    "missing_access_token": (
        "The provider did not return an access token. Check the OAuth app's authorization settings."
    ),
    "oauth_exchange_failed": (
        "The server could not complete the OAuth token exchange. Start a fresh connection; "
        "if it fails again, check the server logs."
    ),
    "oauth_provider_error": (
        "The provider rejected authorization. Check the OAuth app's permissions "
        "and user access policy, then reconnect."
    ),
}


class OAuthProviderError(ValueError):
    """An allowlisted provider error safe for logs, storage and browser notices."""

    def __init__(self, provider: str, code: object, status_code: int | None = None):
        self.error_code = (
            code if isinstance(code, str) and code in _MESSAGES else "oauth_provider_error"
        )
        self.status_code = status_code
        name = PROVIDER_SETUP[provider].display_name if provider in PROVIDER_SETUP else "Provider"
        super().__init__(f"{name}: {_MESSAGES[self.error_code]} ({self.error_code})")
