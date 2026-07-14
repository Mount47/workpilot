"""Tests for OpenAI-compatible provider — uses mocked HTTP responses."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from workpilot.providers.base import ProviderResponseError
from workpilot.providers.openai_provider import OpenAIProvider


@pytest.fixture
def mock_openai_client():
    """Patch OpenAI client with a mock that returns controlled responses."""
    with patch("workpilot.providers.openai_provider.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client
        yield mock_client


def _make_chat_response(
    content: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
):
    """Build a mock chat completion response."""
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = "stop"
    response = MagicMock()
    response.choices = [choice]
    response.id = "request-1"
    response.usage = SimpleNamespace(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
    )
    return response


def test_generate_text(mock_openai_client) -> None:
    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        "Hello world", input_tokens=3, output_tokens=2
    )
    provider = OpenAIProvider(api_key="test-key", model="test-model")
    result = provider.generate_text("Say hello")

    assert result.content == "Hello world"
    assert result.input_tokens == 3
    assert result.output_tokens == 2
    assert result.total_tokens == 5
    assert result.request_id == "request-1"
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
    extraction = provider.extract_evidence_from_file(
        file_path="notes.md",
        content="line1\nline2",
        goal="周报",
    )

    candidates = extraction.candidates
    assert len(candidates) == 2
    assert candidates[0].quote == "QPS 从 1200 提升至 1380"
    assert candidates[0].evidence_type == "progress"
    assert candidates[0].start_line == 7
    assert candidates[1].evidence_type == "risk"
    assert len(extraction.generations) == 1
    messages = mock_openai_client.chat.completions.create.call_args[1]["messages"]
    assert "1 | line1" in messages[1]["content"]
    assert "2 | line2" in messages[1]["content"]
    assert "N |` is not part" in messages[1]["content"]


def test_extract_evidence_strips_markdown_fences(mock_openai_client) -> None:
    evidence_json = '```json\n[{"quote": "test", "start_line": 1, "end_line": 1, "evidence_type": "context"}]\n```'
    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        evidence_json
    )

    provider = OpenAIProvider(api_key="test-key", model="m")
    extraction = provider.extract_evidence_from_file("f.md", "test", "goal")

    assert len(extraction.candidates) == 1
    assert extraction.candidates[0].quote == "test"


def test_extract_evidence_handles_malformed_json(mock_openai_client) -> None:
    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        "sorry I cannot parse that"
    )

    provider = OpenAIProvider(api_key="test-key", model="m")
    extraction = provider.extract_evidence_from_file("f.md", "content", "goal")

    assert extraction.candidates == []
    assert extraction.error_type == "malformed_response"
    assert len(extraction.generations) == 2


def test_extract_evidence_handles_empty_content(mock_openai_client) -> None:
    provider = OpenAIProvider(api_key="test-key", model="m")
    extraction = provider.extract_evidence_from_file("f.md", "  ", "goal")

    assert extraction.candidates == []
    assert extraction.generations == ()
    mock_openai_client.chat.completions.create.assert_not_called()


def test_extract_evidence_invalid_type_defaults_to_context(mock_openai_client) -> None:
    evidence_json = json.dumps([
        {"quote": "test quote", "start_line": 1, "end_line": 1, "evidence_type": "banana"}
    ])
    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        evidence_json
    )

    provider = OpenAIProvider(api_key="test-key", model="m")
    extraction = provider.extract_evidence_from_file("f.md", "test quote", "goal")

    assert len(extraction.candidates) == 1
    assert extraction.candidates[0].evidence_type == "context"


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

    assert isinstance(result.value, TestModel)
    assert result.value.name == "hello"
    assert result.value.count == 42
    assert len(result.generations) == 1


def test_generate_structured_retries_on_invalid_json(mock_openai_client) -> None:
    class TestModel(BaseModel):
        value: int

    mock_openai_client.chat.completions.create.side_effect = [
        _make_chat_response("not json"),
        _make_chat_response(json.dumps({"value": 7})),
    ]

    provider = OpenAIProvider(api_key="test-key", model="m", max_retries=1)
    result = provider.generate_structured("test", TestModel)

    assert result.value.value == 7
    assert len(result.generations) == 2
    assert mock_openai_client.chat.completions.create.call_count == 2
    retry_messages = mock_openai_client.chat.completions.create.call_args_list[1][1][
        "messages"
    ]
    assert retry_messages[-2] == {"role": "assistant", "content": "not json"}
    assert "json_invalid" in retry_messages[-1]["content"]


def test_structured_retry_feedback_contains_schema_location(
    mock_openai_client,
) -> None:
    class TestModel(BaseModel):
        value: int

    mock_openai_client.chat.completions.create.side_effect = [
        _make_chat_response(json.dumps({"value": "secret-text"})),
        _make_chat_response(json.dumps({"value": 7})),
    ]

    result = OpenAIProvider(
        api_key="test-key",
        model="m",
        max_retries=1,
    ).generate_structured("test", TestModel)

    assert result.value.value == 7
    retry_messages = mock_openai_client.chat.completions.create.call_args_list[1][1][
        "messages"
    ]
    assert "value: int_parsing" in retry_messages[-1]["content"]
    assert "secret-text" not in retry_messages[-1]["content"]


def test_structured_failure_does_not_expose_raw_response(
    mock_openai_client,
) -> None:
    class TestModel(BaseModel):
        value: int

    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        '{"customer_secret": "sensitive project text"}'
    )
    provider = OpenAIProvider(api_key="test-key", model="m", max_retries=1)

    with pytest.raises(ProviderResponseError) as captured:
        provider.generate_structured("test", TestModel)

    message = str(captured.value)
    assert "customer_secret" not in message
    assert "sensitive project text" not in message
    assert "value: missing" in message
