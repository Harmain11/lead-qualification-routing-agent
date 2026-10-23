from __future__ import annotations

import hashlib
import logging
import re
import secrets
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from lead_agent.infrastructure.observability import current_request_id
from lead_agent.settings.config import Settings, get_settings

GOOGLE_SCOPES = ("openid", "email", "profile")
_LOGGER = logging.getLogger("lead_agent.google_auth")
_SAFE_PROVIDER_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def _log_provider_http_failure(stage: str, response: httpx.Response) -> None:
    fields: dict[str, str | int] = {
        "request_id": current_request_id(),
        "provider": "google",
        "stage": stage,
        "http_status": response.status_code,
    }
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        candidate = payload.get("error")
        if isinstance(candidate, str) and _SAFE_PROVIDER_ERROR_CODE.fullmatch(candidate):
            fields["provider_error_code"] = candidate
    _LOGGER.warning("google.oauth.request_failed", extra={"fields": fields})


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    display_name: str
    email_verified: bool


class GoogleAuthClient(Protocol):
    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str: ...

    def exchange(self, *, code: str, code_verifier: str, redirect_uri: str) -> GoogleIdentity: ...


class DevelopmentGoogleAuthClient:
    """Local-only deterministic client; production always uses Google endpoints."""

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return "/v1/auth/google/development-authorize?" + urlencode(
            {"state": state, "code_challenge": code_challenge, "redirect_uri": redirect_uri}
        )

    def exchange(self, *, code: str, code_verifier: str, redirect_uri: str) -> GoogleIdentity:
        if not code or not code_verifier or not redirect_uri:
            raise ValueError("Google authorization response is incomplete")
        # This path is never exposed in production. It keeps local QA deterministic without
        # manufacturing a real user unless the developer explicitly follows the flow.
        digest = hashlib.sha256(code.encode()).hexdigest()[:16]
        return GoogleIdentity(
            subject=f"development-{digest}",
            email=f"google-{digest}@localhost.invalid",
            display_name="Google Development User",
            email_verified=True,
        )


class ProviderGoogleAuthClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.Client(timeout=20.0)

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        if not self.settings.google_client_id:
            raise ValueError("Google OAuth application credentials are not configured")
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": self.settings.google_client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(GOOGLE_SCOPES),
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",
                "prompt": "select_account",
            }
        )

    @staticmethod
    def _string_values(payload: dict[str, Any]) -> dict[str, str]:
        return {
            key: str(value)
            for key, value in payload.items()
            if isinstance(value, (str, int, bool))
        }

    def exchange(self, *, code: str, code_verifier: str, redirect_uri: str) -> GoogleIdentity:
        response = self.client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.settings.google_client_id,
                "client_secret": self.settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            _log_provider_http_failure("token_exchange", response)
            raise
        token_payload = response.json()
        access_token = token_payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("Google did not return an access token")
        userinfo = self.client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        try:
            userinfo.raise_for_status()
        except httpx.HTTPStatusError:
            _log_provider_http_failure("userinfo", userinfo)
            raise
        claims = userinfo.json()
        subject = claims.get("sub")
        email = claims.get("email")
        if not isinstance(subject, str) or not isinstance(email, str):
            raise ValueError("Google identity response is missing a stable subject or email")
        verified = claims.get("email_verified") is True
        if not verified:
            raise ValueError("Google email is not verified")
        return GoogleIdentity(
            subject=subject,
            email=email.strip().lower(),
            display_name=str(claims.get("name") or email.split("@", 1)[0]),
            email_verified=True,
        )


@lru_cache
def get_google_auth_client() -> GoogleAuthClient:
    settings = get_settings()
    if settings.oauth_broker == "development":
        return DevelopmentGoogleAuthClient()
    if settings.oauth_broker == "provider":
        return ProviderGoogleAuthClient(settings)
    raise ValueError(f"unsupported OAuth broker: {settings.oauth_broker}")


def new_pkce_verifier() -> str:
    return secrets.token_urlsafe(64)
