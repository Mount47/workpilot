"""Tests for stable Provider error classification."""

from unittest.mock import MagicMock, patch

import pytest

from workpilot.providers.errors import (
    ProviderCallError,
    ProviderErrorType,
    classify_provider_exception,
)
from workpilot.providers.openai_provider import OpenAIProvider


class HttpError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class RequestTimeout(Exception):
    pass


class NetworkConnectionError(Exception):
    pass


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (HttpError(401), ProviderErrorType.AUTHENTICATION),
        (HttpError(429), ProviderErrorType.RATE_LIMIT),
        (HttpError(503), ProviderErrorType.SERVER),
        (RequestTimeout(), ProviderErrorType.TIMEOUT),
        (NetworkConnectionError(), ProviderErrorType.NETWORK),
        (ValueError(), ProviderErrorType.UNKNOWN),
    ],
)
def test_classify_provider_exception(exception: Exception, expected) -> None:
    assert classify_provider_exception(exception) == expected


def test_openai_adapter_wraps_vendor_error_without_response_body() -> None:
    with patch("workpilot.providers.openai_provider.OpenAI") as constructor:
        client = MagicMock()
        client.chat.completions.create.side_effect = HttpError(429)
        constructor.return_value = client
        provider = OpenAIProvider(api_key="test", provider_name="deepseek")

        with pytest.raises(ProviderCallError) as exc_info:
            provider.generate_text("hello")

    error = exc_info.value
    assert error.provider == "deepseek"
    assert error.error_type == ProviderErrorType.RATE_LIMIT
    assert error.retryable is True
    assert str(error) == "deepseek provider call failed: rate_limit"
