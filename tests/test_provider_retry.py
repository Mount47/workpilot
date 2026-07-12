"""Contract tests for bounded Provider transport retries."""

import pytest

from workpilot.providers.errors import ProviderCallError, ProviderErrorType
from workpilot.providers.retry import RetryEvent, RetryPolicy


def test_retry_policy_retries_transient_failure_with_exponential_backoff() -> None:
    attempts: list[int] = []
    events: list[RetryEvent] = []
    delays: list[float] = []

    def operation() -> str:
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise ProviderCallError("deepseek", ProviderErrorType.RATE_LIMIT)
        return "ok"

    result = RetryPolicy(
        max_attempts=3,
        base_delay_seconds=0.5,
        max_delay_seconds=2.0,
    ).execute(operation, on_retry=events.append, sleep=delays.append)

    assert result == "ok"
    assert attempts == [1, 2, 3]
    assert delays == [0.5, 1.0]
    assert [event.error_type for event in events] == ["rate_limit", "rate_limit"]
    assert events[-1].next_attempt == 3


def test_retry_policy_does_not_retry_non_retryable_error() -> None:
    attempts = 0

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise ProviderCallError("openai", ProviderErrorType.AUTHENTICATION)

    with pytest.raises(ProviderCallError) as exc_info:
        RetryPolicy(max_attempts=3).execute(operation, sleep=lambda _: None)

    assert exc_info.value.error_type == ProviderErrorType.AUTHENTICATION
    assert attempts == 1


def test_retry_policy_checks_budget_before_every_attempt() -> None:
    checked: list[int] = []

    def operation() -> None:
        raise ProviderCallError("gemini", ProviderErrorType.TIMEOUT)

    with pytest.raises(ProviderCallError):
        RetryPolicy(max_attempts=2).execute(
            operation,
            before_attempt=checked.append,
            sleep=lambda _: None,
        )

    assert checked == [1, 2]


def test_retry_policy_rejects_invalid_attempt_number() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        RetryPolicy().delay_for(0)

