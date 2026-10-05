from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx

from lead_agent.domain.integrations import OAuthBroker
from lead_agent.settings.config import Settings, get_settings


@dataclass(frozen=True)
class OAuthProviderConfig:
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str


class DevelopmentOAuthBroker:
    """Deterministic local broker exercising state, PKCE, and secret boundaries."""

    def begin(
        self,
        provider: str,
        connection_id: UUID,
        state: str,
        code_challenge: str,
        redirect_uri: str,
        scopes: list[str],
    ) -> str:
        query = urlencode(
            {
                "connection_id": str(connection_id),
                "provider": provider,
                "state": state,
                "code_challenge": code_challenge,
                "redirect_uri": redirect_uri,
                "scope": " ".join(scopes),
            }
        )
        return f"/v1/integrations/oauth/development-authorize?{query}"

    def exchange(
        self,
        provider: str,
        authorization_code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> dict[str, str]:
        if not authorization_code.strip() or not code_verifier or not redirect_uri:
            raise ValueError("authorization code, PKCE verifier, and redirect URI are required")
        digest = hashlib.sha256(f"{authorization_code}:{code_verifier}".encode()).hexdigest()
        return {
            "access_token": f"development-oauth-{digest}",
            "refresh_token": f"development-refresh-{digest}",
            "token_type": "bearer",
            "expires_in": "3600",
            "provider": provider,
        }

    def refresh(self, provider: str, refresh_token: str) -> dict[str, str]:
        digest = hashlib.sha256(refresh_token.encode()).hexdigest()
        return {
            "access_token": f"development-refreshed-{digest}",
            "refresh_token": refresh_token,
            "expires_in": "3600",
            "provider": provider,
        }

    def revoke(self, provider: str, values: dict[str, str]) -> bool:
        return True


class ProviderOAuthBroker:
    """Operator-configured authorization-code broker; secrets never enter the browser."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.Client(timeout=20.0)

    def _config(self, provider: str) -> OAuthProviderConfig:
        tenant = self.settings.microsoft_tenant_id or "common"
        configs = {
            "gmail": OAuthProviderConfig(
                self.settings.google_client_id,
                self.settings.google_client_secret,
                "https://accounts.google.com/o/oauth2/v2/auth",
                "https://oauth2.googleapis.com/token",
            ),
            "google_calendar": OAuthProviderConfig(
                self.settings.google_client_id,
                self.settings.google_client_secret,
                "https://accounts.google.com/o/oauth2/v2/auth",
                "https://oauth2.googleapis.com/token",
            ),
            "calendly": OAuthProviderConfig(
                self.settings.calendly_client_id,
                self.settings.calendly_client_secret,
                "https://auth.calendly.com/oauth/authorize",
                "https://auth.calendly.com/oauth/token",
            ),
            "outlook": OAuthProviderConfig(
                self.settings.microsoft_client_id,
                self.settings.microsoft_client_secret,
                f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
                f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            ),
            "slack": OAuthProviderConfig(
                self.settings.slack_client_id,
                self.settings.slack_client_secret,
                "https://slack.com/oauth/v2/authorize",
                "https://slack.com/api/oauth.v2.access",
            ),
            "salesforce": OAuthProviderConfig(
                self.settings.salesforce_client_id,
                self.settings.salesforce_client_secret,
                f"{self.settings.salesforce_login_base_url}/services/oauth2/authorize",
                f"{self.settings.salesforce_login_base_url}/services/oauth2/token",
            ),
            "hubspot": OAuthProviderConfig(
                self.settings.hubspot_client_id,
                self.settings.hubspot_client_secret,
                "https://app.hubspot.com/oauth/authorize",
                f"https://api.hubspot.com/oauth/{self.settings.hubspot_oauth_api_version}/token",
            ),
            "linkedin": OAuthProviderConfig(
                self.settings.linkedin_client_id,
                self.settings.linkedin_client_secret,
                "https://www.linkedin.com/oauth/v2/authorization",
                "https://www.linkedin.com/oauth/v2/accessToken",
            ),
        }
        config = configs.get(provider)
        if config is None:
            raise ValueError(f"OAuth is not configured for {provider}")
        if not config.client_id or not config.client_secret:
            raise ValueError(f"{provider} OAuth application credentials are not configured")
        return config

    def begin(
        self,
        provider: str,
        connection_id: UUID,
        state: str,
        code_challenge: str,
        redirect_uri: str,
        scopes: list[str],
    ) -> str:
        config = self._config(provider)
        query = {
            "client_id": config.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "scope": ",".join(scopes) if provider == "slack" else " ".join(scopes),
        }
        if provider != "calendly":
            query.update(
                {
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                }
            )
        if provider in {"gmail", "google_calendar"}:
            query.update({"access_type": "offline", "prompt": "consent"})
        return f"{config.authorize_url}?{urlencode(query)}"

    @staticmethod
    def _string_values(payload: dict[str, Any]) -> dict[str, str]:
        return {
            key: str(value)
            for key, value in payload.items()
            if isinstance(value, (str, int, float, bool))
        }

    def exchange(
        self,
        provider: str,
        authorization_code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> dict[str, str]:
        config = self._config(provider)
        token_payload = {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "redirect_uri": redirect_uri,
        }
        if provider != "calendly":
            token_payload["code_verifier"] = code_verifier
        response = self.client.post(
            config.token_url,
            data=token_payload,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("ok") is False:
            raise ValueError(str(payload.get("error", "OAuth token exchange failed")))
        values = self._string_values(payload)
        team = payload.get("team")
        if isinstance(team, dict):
            if isinstance(team.get("name"), str):
                values["account_name"] = team["name"]
            if isinstance(team.get("id"), str):
                values["external_account_id"] = team["id"]
        if "hub_id" in values:
            values["external_account_id"] = values["hub_id"]
        if not values.get("access_token"):
            raise ValueError("OAuth provider did not return an access token")
        return values

    def refresh(self, provider: str, refresh_token: str) -> dict[str, str]:
        config = self._config(provider)
        response = self.client.post(
            config.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": config.client_id,
                "client_secret": config.client_secret,
            },
        )
        response.raise_for_status()
        values = self._string_values(response.json())
        if not values.get("access_token"):
            raise ValueError("OAuth refresh did not return an access token")
        values.setdefault("refresh_token", refresh_token)
        return values

    def revoke(self, provider: str, values: dict[str, str]) -> bool:
        # Managed-secret deletion is authoritative; provider revocation is adapter-specific.
        return False


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


@lru_cache
def get_oauth_broker() -> OAuthBroker:
    settings = get_settings()
    if settings.oauth_broker == "development":
        return DevelopmentOAuthBroker()
    if settings.oauth_broker == "provider":
        return ProviderOAuthBroker(settings)
    raise ValueError(f"unsupported OAuth broker: {settings.oauth_broker}")
