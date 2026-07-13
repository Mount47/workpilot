"""Tests for offline Provider readiness and API key isolation."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from workpilot.cli import app
from workpilot.config import Settings
from workpilot.providers.claude_provider import ClaudeProvider
from workpilot.providers.openai_provider import OpenAIProvider
from workpilot.providers.preflight import (
    ProviderConfigurationError,
    inspect_provider_configuration,
)
from workpilot.providers.registry import get_provider


def _settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_preflight_reports_missing_key_without_exposing_other_provider_key() -> None:
    secret = "openai-secret-must-not-leak"
    settings = _settings(openai_api_key=secret, deepseek_api_key="")

    result = inspect_provider_configuration("deepseek", settings=settings)

    assert result.ready is False
    assert result.api_key_env == "DEEPSEEK_API_KEY"
    assert result.api_key_configured is False
    assert result.issues[0].code == "missing_api_key"
    assert secret not in str(result.model_dump())


def test_preflight_ready_result_never_stores_configured_key() -> None:
    secret = "deepseek-secret-must-not-leak"
    settings = _settings(deepseek_api_key=secret)

    result = inspect_provider_configuration("deepseek", settings=settings)

    assert result.ready is True
    assert result.api_key_configured is True
    assert secret not in str(result.model_dump())


def test_registry_rejects_missing_vendor_key_before_client_construction() -> None:
    settings = SimpleNamespace(
        workpilot_model="",
        workpilot_base_url="",
        api_key_for=lambda name: "openai-only-secret" if name == "openai" else "",
    )
    with (
        patch("workpilot.providers.registry.Settings", return_value=settings),
        patch("workpilot.providers.openai_provider.OpenAI") as constructor,
    ):
        with pytest.raises(ProviderConfigurationError) as exc_info:
            get_provider("deepseek")

    assert exc_info.value.code == "missing_api_key"
    constructor.assert_not_called()
    assert "openai-only-secret" not in str(exc_info.value)


def test_adapters_do_not_guess_keys_from_process_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-openai-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "wrong-deepseek-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "wrong-anthropic-key")

    with pytest.raises(ValueError, match="Provider Registry"):
        OpenAIProvider(api_key="")
    with pytest.raises(ValueError, match="Provider Registry"):
        ClaudeProvider(api_key="")


def test_preflight_rejects_and_sanitizes_credential_bearing_base_url() -> None:
    secret = "url-secret-must-not-leak"
    result = inspect_provider_configuration(
        "custom",
        model="private-model",
        base_url=f"https://user:{secret}@gateway.example/v1?token={secret}",
        api_key_configured=True,
        settings=_settings(),
    )

    assert result.ready is False
    assert result.issues[0].code == "unsafe_base_url"
    assert result.base_url == "https://gateway.example/v1"
    assert secret not in str(result.model_dump())


def test_local_http_gateway_is_allowed_but_remote_http_is_rejected() -> None:
    local = inspect_provider_configuration(
        "custom",
        model="local-model",
        base_url="http://localhost:8000/v1",
        api_key_configured=True,
        settings=_settings(),
    )
    remote = inspect_provider_configuration(
        "custom",
        model="remote-model",
        base_url="http://gateway.example/v1",
        api_key_configured=True,
        settings=_settings(),
    )

    assert local.ready is True
    assert remote.ready is False
    assert remote.issues[0].code == "unsafe_base_url"


def test_preflight_rejects_missing_required_adapter_capability() -> None:
    result = inspect_provider_configuration(
        "openai",
        api_key_configured=True,
        required_capabilities=["native_tools"],
        settings=_settings(),
    )

    assert result.ready is False
    assert result.issues[0].code == "missing_capability"


def test_doctor_is_offline_and_never_prints_key() -> None:
    secret = "doctor-secret-must-not-leak"
    result = CliRunner().invoke(
        app,
        ["doctor", "--provider", "deepseek"],
        env={"DEEPSEEK_API_KEY": secret},
    )

    assert result.exit_code == 0
    assert "Overall ready: yes" in result.stdout
    assert "Network request sent: no" in result.stdout
    assert "API key configured: yes" in result.stdout
    assert secret not in result.stdout


def test_doctor_stub_needs_no_key() -> None:
    result = CliRunner().invoke(app, ["doctor", "--provider", "stub"])

    assert result.exit_code == 0
    assert "API key env: <not required>" in result.stdout
    assert "Overall ready: yes" in result.stdout


def test_doctor_route_config_checks_all_targets_without_network() -> None:
    result = CliRunner().invoke(
        app,
        ["doctor", "--route-config", "examples/model_routes.json"],
        env={
            "OPENAI_API_KEY": "openai-route-secret",
            "ANTHROPIC_API_KEY": "claude-route-secret",
            "DEEPSEEK_API_KEY": "deepseek-route-secret",
        },
    )

    assert result.exit_code == 0
    assert "Target 1: evidence_extraction" in result.stdout
    assert "Overall ready: yes" in result.stdout
    assert "route-secret" not in result.stdout
