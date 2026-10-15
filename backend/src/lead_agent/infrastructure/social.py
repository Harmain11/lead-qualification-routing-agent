from __future__ import annotations

from typing import Any

import httpx

from lead_agent.domain.integrations import ConnectionValidation
from lead_agent.domain.provider_errors import (
    ProviderOperationError,
    provider_http_error,
    provider_malformed_response_error,
    provider_timeout_error,
)


class SocialConnectionValidator:
    """Validate Meta tokens with a low-risk identity read and Clutch webhook credentials locally."""

    _META_PROVIDERS = {"instagram", "messenger", "whatsapp_business"}
    _GRAPH_BASE = "https://graph.facebook.com/v20.0"

    def __init__(self, *, client: httpx.Client | None = None, timeout_seconds: float = 10.0) -> None:
        self.client = client
        self.timeout_seconds = timeout_seconds

    def validate(
        self, provider: str, credentials: dict[str, str], scopes: list[str]
    ) -> ConnectionValidation:
        del scopes
        if provider == "clutch":
            return self._validate_clutch(credentials)
        if provider not in self._META_PROVIDERS:
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="configuration_error",
                credential_state="invalid",
                message=f"Unsupported social provider: {provider}.",
                error_code="social_provider_unsupported",
            )
        token = credentials.get("access_token") or credentials.get("page_access_token", "")
        if not token:
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="invalid_credentials",
                credential_state="invalid",
                message=f"{provider} access token is missing.",
                error_code="credential_fields_missing",
            )
        target = (
            credentials.get("account_id")
            or credentials.get("page_id")
            or credentials.get("business_account_id")
            or "me"
        )
        try:
            payload = self._graph_identity(provider, target, token)
        except ProviderOperationError as error:
            status = (
                "invalid_credentials"
                if error.category == "invalid_credentials"
                else "insufficient_permissions"
                if error.category == "insufficient_permissions"
                else "rate_limited"
                if error.category == "rate_limited"
                else "temporarily_unavailable"
                if error.retryable
                else "configuration_error"
            )
            return ConnectionValidation(
                valid=False,
                verified=False,
                status=status,
                credential_state="invalid" if status == "invalid_credentials" else "configured",
                message=str(error),
                retryable=error.retryable,
                error_code=error.code,
            )
        identity = str(payload.get("name") or payload.get("username") or payload.get("id") or target)
        return ConnectionValidation(
            valid=True,
            verified=True,
            status="connected",
            credential_state="configured",
            message=f"{provider} access verified.",
            metadata={"verified_account_id": str(payload.get("id") or target), "verified_account_name": identity},
        )

    def _graph_identity(self, provider: str, target: str, token: str) -> dict[str, Any]:
        client = self.client or httpx.Client(timeout=self.timeout_seconds)
        close_client = self.client is None
        try:
            response = client.get(
                f"{self._GRAPH_BASE}/{target}",
                params={"fields": "id,name,username"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as error:
            raise provider_timeout_error(provider) from error
        except httpx.HTTPError as error:
            raise ProviderOperationError(
                f"{provider} is temporarily unavailable.",
                provider=provider,
                code=f"{provider}_transport_failure",
                category="temporarily_unavailable",
                retryable=True,
            ) from error
        finally:
            if close_client:
                client.close()
        if response.status_code >= 400:
            raise provider_http_error(provider, response.status_code)
        try:
            payload = response.json()
        except ValueError as error:
            raise provider_malformed_response_error(provider) from error
        if not isinstance(payload, dict) or not payload.get("id"):
            raise provider_malformed_response_error(provider)
        return payload

    @staticmethod
    def _validate_clutch(credentials: dict[str, str]) -> ConnectionValidation:
        if not credentials.get("api_key") or not credentials.get("account_id"):
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="configuration_error",
                credential_state="invalid",
                message="Clutch API key and account ID are required.",
                error_code="credential_fields_missing",
            )
        return ConnectionValidation(
            valid=True,
            verified=True,
            status="connected",
            credential_state="configured",
            message="Clutch credentials are configured for inbound lead delivery.",
            metadata={"verified_account_id": credentials["account_id"], "verification_mode": "inbound_webhook"},
        )
