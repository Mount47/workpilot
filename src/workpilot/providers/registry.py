"""Data-driven Provider registry and adapter construction."""

from typing import Any

from workpilot.config import Settings
from workpilot.providers.base import LLMProvider
from workpilot.providers.specs import (
    PROVIDER_DESCRIPTORS,
    ProviderDescriptor,
    ProviderTransport,
)
from workpilot.providers.stub import StubProvider


AVAILABLE = sorted(PROVIDER_DESCRIPTORS)

# Backward-compatible metadata exports for callers that used the old registry.
OPENAI_COMPATIBLE_DEFAULTS: dict[str, dict[str, str]] = {
    name: {
        "model": descriptor.default_model or "",
        "base_url": descriptor.default_base_url or "",
    }
    for name, descriptor in PROVIDER_DESCRIPTORS.items()
    if descriptor.transport == ProviderTransport.OPENAI_COMPATIBLE
    and name != "custom"
}
CLAUDE_DEFAULT_MODEL = PROVIDER_DESCRIPTORS["claude"].default_model or ""


def get_provider_descriptor(name: str) -> ProviderDescriptor:
    """Return an isolated descriptor for UI, validation and diagnostics."""
    descriptor = PROVIDER_DESCRIPTORS.get(name)
    if descriptor is None:
        raise ValueError(f"Unknown provider '{name}'. Available: {', '.join(AVAILABLE)}")
    return descriptor.model_copy(deep=True)


def get_provider(name: str, **kwargs: Any) -> LLMProvider:
    """Build the adapter selected by a ProviderDescriptor."""
    descriptor = get_provider_descriptor(name)
    if descriptor.transport == ProviderTransport.STUB:
        return StubProvider()

    settings = Settings()
    api_key = kwargs.pop("api_key", None) or settings.api_key_for(name)
    model = kwargs.pop("model", None) or settings.workpilot_model or descriptor.default_model
    configured_base_url = kwargs.pop("base_url", None) or settings.workpilot_base_url
    base_url = configured_base_url or descriptor.default_base_url

    if descriptor.transport == ProviderTransport.ANTHROPIC_NATIVE:
        from workpilot.providers.claude_provider import ClaudeProvider

        if not model:
            raise ValueError(f"Provider '{name}' requires an explicit model")
        return ClaudeProvider(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_name=name,
            **kwargs,
        )

    if descriptor.transport == ProviderTransport.OPENAI_COMPATIBLE:
        from workpilot.providers.openai_provider import OpenAIProvider

        if not model:
            raise ValueError(f"Provider '{name}' requires --model or WORKPILOT_MODEL")
        if not base_url:
            raise ValueError(
                f"Provider '{name}' requires --base-url or WORKPILOT_BASE_URL"
            )
        return OpenAIProvider(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_name=name,
            **kwargs,
        )

    raise RuntimeError(f"Unsupported provider transport: {descriptor.transport.value}")
