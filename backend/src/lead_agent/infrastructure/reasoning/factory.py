from __future__ import annotations

from functools import lru_cache
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.domain.analysis import ReasoningProvider
from lead_agent.domain.integrations import SecretStore
from lead_agent.infrastructure.db.models import ProviderConnectionORM
from lead_agent.infrastructure.reasoning.configuration import validate_openai_model
from lead_agent.infrastructure.reasoning.deterministic import DeterministicReasoningProvider
from lead_agent.infrastructure.reasoning.openai_provider import OpenAIReasoningProvider
from lead_agent.infrastructure.secrets import get_secret_store
from lead_agent.settings.config import Settings, get_settings


def build_reasoning_provider(settings: Settings) -> ReasoningProvider:
    provider = settings.llm_provider.strip().lower()
    if provider in {"", "none", "workspace", "deterministic", "mock"}:
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


def resolve_workspace_reasoning_provider(
    session: Session,
    workspace_id: UUID,
    *,
    settings: Settings | None = None,
    secret_store: SecretStore | None = None,
) -> ReasoningProvider:
    """Resolve one workspace's verified key/model without any cross-workspace fallback."""

    active = settings or get_settings()
    mode = active.llm_provider.strip().lower()
    if mode in {"", "none", "deterministic", "mock"}:
        return DeterministicReasoningProvider()
    if mode == "openai":
        if active.app_env == "production":
            return DeterministicReasoningProvider()
        # Explicit development/test-only operator mode retained for adapter testing.
        return build_reasoning_provider(active)
    if mode != "workspace":
        return DeterministicReasoningProvider()

    connection = session.scalar(
        select(ProviderConnectionORM)
        .where(
            ProviderConnectionORM.workspace_id == workspace_id,
            ProviderConnectionORM.provider_type == "openai",
            ProviderConnectionORM.is_active.is_(True),
            ProviderConnectionORM.status == "verified",
        )
        .order_by(ProviderConnectionORM.updated_at.desc())
    )
    if connection is None or connection.secret_reference is None:
        return DeterministicReasoningProvider()
    try:
        model = validate_openai_model(connection.connection_metadata.get("selected_model"), active)
        credentials = (secret_store or get_secret_store()).retrieve_for_workspace(
            workspace_id,
            "openai",
            connection.secret_reference,
        )
    except (KeyError, RuntimeError, ValueError):
        return DeterministicReasoningProvider()
    api_key = (credentials or {}).get("api_key", "")
    if not api_key:
        return DeterministicReasoningProvider()
    return OpenAIReasoningProvider(
        api_key=api_key,
        model=model,
        organization=None,
        timeout_seconds=active.openai_timeout_seconds,
        max_retries=0,
        store_responses=active.openai_store_responses,
    )


@lru_cache
def get_reasoning_provider() -> ReasoningProvider:
    return build_reasoning_provider(get_settings())
