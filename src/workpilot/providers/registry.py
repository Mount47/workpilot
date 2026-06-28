"""Provider registry — resolve provider by name."""

from workpilot.providers.base import LLMProvider
from workpilot.providers.stub import StubProvider


_PROVIDERS: dict[str, type[LLMProvider]] = {
    "stub": StubProvider,
}


def get_provider(name: str, **kwargs) -> LLMProvider:
    """Get a provider instance by name.

    Args:
        name: Provider name (e.g. 'stub', 'openai', 'deepseek')
        **kwargs: Provider-specific configuration (api_key, model, base_url, etc.)

    Returns:
        An initialized LLMProvider instance.

    Raises:
        ValueError: If provider name is not registered.
    """
    provider_cls = _PROVIDERS.get(name)
    if provider_cls is None:
        available = ", ".join(sorted(_PROVIDERS.keys()))
        raise ValueError(
            f"Unknown provider '{name}'. Available: {available}"
        )
    return provider_cls(**kwargs)
