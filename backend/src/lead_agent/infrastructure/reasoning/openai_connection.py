from __future__ import annotations

from importlib import import_module
from typing import Any, Literal

from pydantic import BaseModel

from lead_agent.domain.integrations import ConnectionValidation
from lead_agent.infrastructure.reasoning.openai_provider import OpenAIReasoningProvider


class _ConnectionProbe(BaseModel):
    status: Literal["ok"]


class OpenAIConnectionValidator:
    """Perform one bounded Responses API structured-output request with workspace credentials."""

    def __init__(self, *, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds

    def validate(
        self,
        provider: str,
        credentials: dict[str, str],
        scopes: list[str],
    ) -> ConnectionValidation:
        del scopes
        api_key = credentials.get("api_key", "")
        model = credentials.get("model", "")
        if provider != "openai" or not api_key or not model:
            return self._failure(
                "configuration_error",
                "configured",
                "OpenAI connection configuration is incomplete.",
                "openai_configuration_incomplete",
            )
        try:
            client_type = import_module("openai").OpenAI
            client = client_type(
                api_key=api_key,
                timeout=self.timeout_seconds,
                max_retries=0,
            )
            response = client.responses.parse(
                model=model,
                input=(
                    "Return status=ok. This is a minimal connection and structured-output "
                    "compatibility test."
                ),
                text_format=_ConnectionProbe,
                max_output_tokens=16,
                store=False,
            )
            parsed = _ConnectionProbe.model_validate(getattr(response, "output_parsed", None))
        except Exception as error:
            category, _, retryable = OpenAIReasoningProvider._classify_error(error)
            return self._classified_failure(category, retryable)
        if parsed.status != "ok":
            return self._failure(
                "malformed_response",
                "configured",
                "OpenAI returned an incompatible structured response.",
                "openai_structured_output_incompatible",
            )
        return ConnectionValidation(
            valid=True,
            verified=True,
            status="connected",
            credential_state="configured",
            message="OpenAI Responses API and structured output access verified.",
            retryable=False,
            metadata={"selected_model": model, "responses_api": True, "structured_output": True},
        )

    @classmethod
    def _classified_failure(cls, category: str, retryable: bool) -> ConnectionValidation:
        mapping: dict[str, tuple[str, str, str, str]] = {
            "authentication": (
                "invalid_credentials",
                "invalid",
                "OpenAI rejected the workspace credential.",
                "openai_invalid_credentials",
            ),
            "permission": (
                "insufficient_permissions",
                "configured",
                "The OpenAI project key lacks required API permissions.",
                "openai_insufficient_permissions",
            ),
            "quota": (
                "quota_exhausted",
                "configured",
                "The OpenAI project has no available quota or billing capacity.",
                "openai_quota_exhausted",
            ),
            "model_access": (
                "model_unavailable",
                "configured",
                "The selected OpenAI model is unavailable to this project.",
                "openai_model_unavailable",
            ),
            "rate_limit": (
                "rate_limited",
                "configured",
                "OpenAI rate limited the connection test.",
                "openai_rate_limited",
            ),
            "server": (
                "provider_outage",
                "configured",
                "OpenAI is temporarily unavailable.",
                "openai_provider_unavailable",
            ),
            "transport": (
                "transport_failure",
                "configured",
                "The OpenAI connection could not be reached.",
                "openai_transport_failure",
            ),
            "timeout": (
                "temporarily_unavailable",
                "configured",
                "The OpenAI connection test timed out.",
                "openai_timeout",
            ),
            "structured_output": (
                "malformed_response",
                "configured",
                "The selected model did not return compatible structured output.",
                "openai_structured_output_incompatible",
            ),
        }
        status, credential_state, message, code = mapping.get(
            category,
            (
                "temporarily_unavailable",
                "configured",
                "OpenAI connection verification failed.",
                "openai_verification_failed",
            ),
        )
        return cls._failure(status, credential_state, message, code, retryable=retryable)

    @staticmethod
    def _failure(
        status: Any,
        credential_state: Any,
        message: str,
        error_code: str,
        *,
        retryable: bool = False,
    ) -> ConnectionValidation:
        return ConnectionValidation(
            valid=False,
            verified=False,
            status=status,
            credential_state=credential_state,
            message=message,
            retryable=retryable,
            error_code=error_code,
            metadata={"category": status},
        )
