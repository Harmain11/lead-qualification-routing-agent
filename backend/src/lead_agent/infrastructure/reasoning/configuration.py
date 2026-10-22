from __future__ import annotations

from lead_agent.settings.config import Settings, get_settings


def supported_openai_models(settings: Settings | None = None) -> tuple[str, ...]:
    """Return the server-controlled, ordered workspace model allowlist."""

    active = settings or get_settings()
    models = tuple(
        dict.fromkeys(
            model.strip()
            for model in active.openai_supported_models.split(",")
            if model.strip()
        )
    )
    if not models:
        raise RuntimeError("OPENAI_SUPPORTED_MODELS must contain at least one model")
    return models


def default_openai_model(settings: Settings | None = None) -> str:
    active = settings or get_settings()
    allowed = supported_openai_models(active)
    configured = active.openai_default_model.strip()
    if configured and configured in allowed:
        return configured
    return allowed[0]


def validate_openai_model(model: object, settings: Settings | None = None) -> str:
    if not isinstance(model, str) or not model.strip():
        raise ValueError("an OpenAI model selection is required")
    selected = model.strip()
    if selected not in supported_openai_models(settings):
        raise ValueError("selected OpenAI model is not supported by this application")
    return selected
