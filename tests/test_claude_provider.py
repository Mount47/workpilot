"""Tests for observable Claude provider results using a mocked client."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pydantic import BaseModel

from workpilot.providers.claude_provider import ClaudeProvider
from workpilot.providers.errors import ProviderCallError, ProviderErrorType


def _response(content: str):
    return SimpleNamespace(
        id="msg-1",
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=content)],
        usage=SimpleNamespace(input_tokens=7, output_tokens=3),
    )


def test_claude_generate_text_returns_usage() -> None:
    with patch("workpilot.providers.claude_provider.Anthropic") as anthropic:
        client = MagicMock()
        client.messages.create.return_value = _response("hello")
        anthropic.return_value = client
        provider = ClaudeProvider(api_key="test", model="claude-test")

        result = provider.generate_text("say hello")

    assert result.content == "hello"
    assert result.input_tokens == 7
    assert result.output_tokens == 3
    assert result.request_id == "msg-1"
    assert result.finish_reason == "end_turn"


def test_claude_generate_structured_returns_value_and_call() -> None:
    class ResultModel(BaseModel):
        value: int

    with patch("workpilot.providers.claude_provider.Anthropic") as anthropic:
        client = MagicMock()
        client.messages.create.return_value = _response(json.dumps({"value": 9}))
        anthropic.return_value = client
        provider = ClaudeProvider(api_key="test", model="claude-test")

        result = provider.generate_structured("return value", ResultModel)

    assert isinstance(result.value, ResultModel)
    assert result.value.value == 9
    assert len(result.generations) == 1


def test_claude_wraps_authentication_error() -> None:
    class AuthenticationFailure(Exception):
        status_code = 401

    with patch("workpilot.providers.claude_provider.Anthropic") as anthropic:
        client = MagicMock()
        client.messages.create.side_effect = AuthenticationFailure()
        anthropic.return_value = client
        provider = ClaudeProvider(api_key="test")

        try:
            provider.generate_text("hello")
        except ProviderCallError as error:
            assert error.error_type == ProviderErrorType.AUTHENTICATION
            assert error.retryable is False
        else:
            raise AssertionError("ProviderCallError was not raised")
