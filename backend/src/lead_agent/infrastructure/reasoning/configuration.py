from __future__ import annotations

from lead_agent.settings.config import Settings, get_settings

REASONING_PROVIDERS = ("openai", "gemini", "claude", "deepseek", "xai")
_MODEL_SETTING_NAMES = {
    "openai": ("openai_supported_models", "openai_default_model"),
    "gemini": ("gemini_supported_models", "gemini_default_model"),
    "claude": ("claude_supported_models", "claude_default_model"),
    "deepseek": ("deepseek_supported_models", "deepseek_default_model"),
    "xai": ("xai_supported_models", "xai_default_model"),
}
_PROVIDER_NAMES = {
    "openai": "OpenAI",
    "gemini": "Gemini",
    "claude": "Claude",
    "deepseek": "DeepSeek",
    "xai": "xAI",
}


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


def supported_reasoning_models(
    provider: str, settings: Settings | None = None
) -> tuple[str, ...]:
    active = settings or get_settings()
    names = _MODEL_SETTING_NAMES.get(provider)
    if names is None:
        raise ValueError(f"unsupported reasoning provider: {provider}")
    models = tuple(dict.fromkeys(
        model.strip() for model in getattr(active, names[0]).split(",") if model.strip()
    ))
    if not models:
        raise RuntimeError(f"{provider.upper()}_SUPPORTED_MODELS must contain at least one model")
    return models


def default_reasoning_model(provider: str, settings: Settings | None = None) -> str:
    active = settings or get_settings()
    names = _MODEL_SETTING_NAMES.get(provider)
    if names is None:
        raise ValueError(f"unsupported reasoning provider: {provider}")
    supported = supported_reasoning_models(provider, active)
    configured = getattr(active, names[1]).strip()
    return configured if configured in supported else supported[0]


def validate_reasoning_model(
    provider: str, model: object, settings: Settings | None = None
) -> str:
    provider_name = _PROVIDER_NAMES.get(provider, provider.title())
    if not isinstance(model, str) or not model.strip():
        raise ValueError(f"a {provider_name} model selection is required")
    selected = model.strip()
    if selected not in supported_reasoning_models(provider, settings):
        raise ValueError(f"selected {provider_name} model is not supported by this application")
    return selected
