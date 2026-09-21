from __future__ import annotations

from functools import lru_cache

from lead_agent.domain.analysis import ReasoningProvider
from lead_agent.infrastructure.reasoning.deterministic import DeterministicReasoningProvider
from lead_agent.infrastructure.reasoning.openai_provider import OpenAIReasoningProvider
from lead_agent.settings.config import Settings, get_settings


def build_reasoning_provider(settings: Settings) -> ReasoningProvider:
    provider = settings.llm_provider.strip().lower()
    if provider in {"", "none", "deterministic", "mock"}:
        return DeterministicReasoningProvider()
    if provider == "openai":
        return OpenAIReasoningProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            organization=settings.openai_org_id or None,
            timeout_seconds=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
            store_responses=settings.openai_store_responses,
        )
    raise ValueError(f"unsupported LLM_PROVIDER: {settings.llm_provider}")


@lru_cache
def get_reasoning_provider() -> ReasoningProvider:
    return build_reasoning_provider(get_settings())
