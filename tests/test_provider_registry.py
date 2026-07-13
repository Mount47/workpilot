"""Tests for data-driven multi-Provider registration and capabilities."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from workpilot.config import Settings
from workpilot.providers.openai_provider import OpenAIProvider
from workpilot.providers.registry import (
    AVAILABLE,
    get_provider,
    get_provider_descriptor,
)
from workpilot.providers.specs import ProviderTransport


def test_all_required_provider_names_are_registered() -> None:
    assert {
        "stub",
        "openai",
        "claude",
        "deepseek",
        "qwen",
        "bailian",
        "glm",
        "gemini",
        "custom",
    }.issubset(AVAILABLE)


def test_provider_descriptors_expose_transport_and_adapter_capabilities() -> None:
    assert (
        get_provider_descriptor("claude").transport
        == ProviderTransport.ANTHROPIC_NATIVE
    )
    for name in ["openai", "deepseek", "qwen", "bailian", "glm", "gemini"]:
        descriptor = get_provider_descriptor(name)
        assert descriptor.transport == ProviderTransport.OPENAI_COMPATIBLE
        assert descriptor.capabilities.text_generation is True
        assert descriptor.capabilities.structured_output_mode == "validated_json"


def test_cost_quality_defaults_use_current_stable_model_ids() -> None:
    assert get_provider_descriptor("openai").default_model == "gpt-5.4-mini"
    assert get_provider_descriptor("claude").default_model == "claude-sonnet-5"
    assert get_provider_descriptor("deepseek").default_model == "deepseek-v4-pro"


def test_gemini_uses_official_openai_compatibility_endpoint() -> None:
    with patch("workpilot.providers.openai_provider.OpenAI") as client_constructor:
        provider = get_provider("gemini", api_key="gemini-test")

    assert isinstance(provider, OpenAIProvider)
    assert provider.provider_name == "gemini"
    assert provider.model == "gemini-3.5-flash"
    call_kwargs = client_constructor.call_args.kwargs
    assert call_kwargs["api_key"] == "gemini-test"
    assert call_kwargs["base_url"] == (
        "https://generativelanguage.googleapis.com/v1beta/openai/"
    )


def test_custom_provider_requires_explicit_model_and_base_url() -> None:
    settings = SimpleNamespace(
        workpilot_model="",
        workpilot_base_url="",
        api_key_for=lambda name: "",
    )
    with patch("workpilot.providers.registry.Settings", return_value=settings):
        with pytest.raises(ValueError, match="missing_model"):
            get_provider("custom", api_key="test")
        with pytest.raises(ValueError, match="missing_base_url"):
            get_provider("custom", api_key="test", model="private-model")


def test_custom_provider_forwards_explicit_gateway_configuration() -> None:
    with patch("workpilot.providers.openai_provider.OpenAI") as client_constructor:
        provider = get_provider(
            "custom",
            api_key="private-key",
            model="private-model",
            base_url="https://gateway.example/v1",
        )

    assert provider.provider_name == "custom"
    assert provider.model == "private-model"
    call_kwargs = client_constructor.call_args.kwargs
    assert call_kwargs["base_url"] == "https://gateway.example/v1"


def test_settings_maps_gemini_and_custom_keys() -> None:
    settings = Settings(
        _env_file=None,
        gemini_api_key="gemini-key",
        custom_api_key="custom-key",
    )

    assert settings.api_key_for("gemini") == "gemini-key"
    assert settings.api_key_for("custom") == "custom-key"
