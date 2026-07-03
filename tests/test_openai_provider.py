"""Tests for OpenAI-compatible provider — uses mocked HTTP responses."""

import json
from unittest.mock import MagicMock, patch

import pytest

from workpilot.providers.openai_provider import OpenAIProvider


@pytest.fixture
def mock_openai_client():
    """Patch OpenAI client with a mock that returns controlled responses."""
    with patch("workpilot.providers.openai_provider.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client
        yield mock_client


def _make_chat_response(content: str):
    """Build a mock chat completion response."""
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


def test_generate_text(mock_openai_client) -> None:
    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        "Hello world"
    )
    provider = OpenAIProvider(api_key="test-key", model="test-model")
    result = provider.generate_text("Say hello")

    assert result == "Hello world"
    mock_openai_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
    assert call_kwargs["model"] == "test-model"


def test_generate_text_with_system_prompt(mock_openai_client) -> None:
    mock_openai_client.chat.completions.create.return_value = _make_chat_response("ok")
    provider = OpenAIProvider(api_key="test-key", model="m")
    provider.generate_text("prompt", system_prompt="you are helpful")

    messages = mock_openai_client.chat.completions.create.call_args[1]["messages"]
    assert messages[0]["role"] == "system"
    assert "you are helpful" in messages[0]["content"]


def test_extract_evidence_from_file_parses_json(mock_openai_client) -> None:
    evidence_json = json.dumps([
        {
            "quote": "QPS 从 1200 提升至 1380",
            "start_line": 7,
            "end_line": 7,
            "evidence_type": "progress",
        },
        {
            "quote": "支付重试逻辑被阻塞",
            "start_line": 12,
            "end_line": 12,
            "evidence_type": "risk",
        },
    ])
    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        evidence_json
    )

    provider = OpenAIProvider(api_key="test-key", model="m")
    candidates = provider.extract_evidence_from_file(
        file_path="notes.md",
        content="line1\nline2",
        goal="周报",
    )

    assert len(candidates) == 2
    assert candidates[0].quote == "QPS 从 1200 提升至 1380"
    assert candidates[0].evidence_type == "progress"
    assert candidates[0].start_line == 7
    assert candidates[1].evidence_type == "risk"


def test_extract_evidence_strips_markdown_fences(mock_openai_client) -> None:
    evidence_json = '```json\n[{"quote": "test", "start_line": 1, "end_line": 1, "evidence_type": "context"}]\n```'
    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        evidence_json
    )

    provider = OpenAIProvider(api_key="test-key", model="m")
    candidates = provider.extract_evidence_from_file("f.md", "test", "goal")

    assert len(candidates) == 1
    assert candidates[0].quote == "test"


def test_extract_evidence_handles_malformed_json(mock_openai_client) -> None:
    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        "sorry I cannot parse that"
    )

    provider = OpenAIProvider(api_key="test-key", model="m")
    candidates = provider.extract_evidence_from_file("f.md", "content", "goal")

    assert candidates == []


def test_extract_evidence_handles_empty_content(mock_openai_client) -> None:
    provider = OpenAIProvider(api_key="test-key", model="m")
    candidates = provider.extract_evidence_from_file("f.md", "  ", "goal")

    assert candidates == []
    mock_openai_client.chat.completions.create.assert_not_called()


def test_extract_evidence_invalid_type_defaults_to_context(mock_openai_client) -> None:
    evidence_json = json.dumps([
        {"quote": "test quote", "start_line": 1, "end_line": 1, "evidence_type": "banana"}
    ])
    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        evidence_json
    )

    provider = OpenAIProvider(api_key="test-key", model="m")
    candidates = provider.extract_evidence_from_file("f.md", "test quote", "goal")

    assert len(candidates) == 1
    assert candidates[0].evidence_type == "context"


def test_generate_structured(mock_openai_client) -> None:
    from pydantic import BaseModel

    class TestModel(BaseModel):
        name: str
        count: int

    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        json.dumps({"name": "hello", "count": 42})
    )

    provider = OpenAIProvider(api_key="test-key", model="m")
    result = provider.generate_structured("test prompt", TestModel)

    assert isinstance(result, TestModel)
    assert result.name == "hello"
    assert result.count == 42


def test_generate_structured_retries_on_invalid_json(mock_openai_client) -> None:
    from pydantic import BaseModel

    class TestModel(BaseModel):
        value: int

    mock_openai_client.chat.completions.create.side_effect = [
        _make_chat_response("not json"),
        _make_chat_response(json.dumps({"value": 7})),
    ]

    provider = OpenAIProvider(api_key="test-key", model="m", max_retries=1)
    result = provider.generate_structured("test", TestModel)

    assert result.value == 7
    assert mock_openai_client.chat.completions.create.call_count == 2
