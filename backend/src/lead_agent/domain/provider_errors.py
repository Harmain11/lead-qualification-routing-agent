from __future__ import annotations

from typing import Literal

ProviderErrorCategory = Literal[
    "invalid_request",
    "invalid_credentials",
    "insufficient_permissions",
    "not_found",
    "conflict",
    "rate_limited",
    "temporarily_unavailable",
    "timeout",
    "malformed_response",
    "revoked",
    "configuration",
    "unknown",
]


class ProviderOperationError(RuntimeError):
    """A secret-safe provider failure suitable for persistence and API translation."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        code: str,
        category: ProviderErrorCategory,
        retryable: bool,
        status_code: int | None = None,
        metadata: dict[str, str | int | bool] | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.category = category
        self.retryable = retryable
        self.status_code = status_code
        self.metadata = dict(metadata or {})


def provider_http_error(provider: str, status_code: int) -> ProviderOperationError:
    safe_provider = provider.lower().replace(" ", "_")
    if status_code == 400:
        return ProviderOperationError(
            f"{provider} rejected the provider request",
            provider=provider,
            code=f"{safe_provider}_invalid_request",
            category="invalid_request",
            retryable=False,
            status_code=status_code,
        )
    if status_code == 401:
        return ProviderOperationError(
            f"{provider} credentials were rejected",
            provider=provider,
            code=f"{safe_provider}_invalid_credentials",
            category="invalid_credentials",
            retryable=False,
            status_code=status_code,
        )
    if status_code == 403:
        return ProviderOperationError(
            f"{provider} permissions are insufficient",
            provider=provider,
            code=f"{safe_provider}_insufficient_permissions",
            category="insufficient_permissions",
            retryable=False,
            status_code=status_code,
        )
    if status_code == 404:
        return ProviderOperationError(
            f"{provider} resource was not found",
            provider=provider,
            code=f"{safe_provider}_not_found",
            category="not_found",
            retryable=False,
            status_code=status_code,
        )
    if status_code == 409:
        return ProviderOperationError(
            f"{provider} reported a conflicting resource",
            provider=provider,
            code=f"{safe_provider}_conflict",
            category="conflict",
            retryable=False,
            status_code=status_code,
        )
    if status_code == 429:
        return ProviderOperationError(
            f"{provider} rate limit was reached",
            provider=provider,
            code=f"{safe_provider}_rate_limited",
            category="rate_limited",
            retryable=True,
            status_code=status_code,
        )
    if status_code in {408, 425}:
        return ProviderOperationError(
            f"{provider} is temporarily unavailable",
            provider=provider,
            code=f"{safe_provider}_temporarily_unavailable",
            category="temporarily_unavailable",
            retryable=True,
            status_code=status_code,
        )
    if status_code >= 500:
        return ProviderOperationError(
            f"{provider} is temporarily unavailable",
            provider=provider,
            code=f"{safe_provider}_temporarily_unavailable",
            category="temporarily_unavailable",
            retryable=True,
            status_code=status_code,
        )
    return ProviderOperationError(
        f"{provider} request failed",
        provider=provider,
        code=f"{safe_provider}_request_failed",
        category="unknown",
        retryable=False,
        status_code=status_code,
    )


def provider_timeout_error(provider: str) -> ProviderOperationError:
    safe_provider = provider.lower().replace(" ", "_")
    return ProviderOperationError(
        f"{provider} request timed out",
        provider=provider,
        code=f"{safe_provider}_timeout",
        category="timeout",
        retryable=True,
    )


def provider_malformed_response_error(provider: str) -> ProviderOperationError:
    safe_provider = provider.lower().replace(" ", "_")
    return ProviderOperationError(
        f"{provider} returned a malformed response",
        provider=provider,
        code=f"{safe_provider}_malformed_response",
        category="malformed_response",
        retryable=False,
    )
