"""Bounded retry policy for transient Provider transport failures."""

from collections.abc import Callable
from dataclasses import dataclass
from time import sleep as default_sleep
from typing import TypeVar

from pydantic import BaseModel, Field

from workpilot.providers.errors import ProviderCallError


T = TypeVar("T")


class RetryPolicy(BaseModel):
    """Deterministic exponential backoff configuration."""

    max_attempts: int = Field(default=3, ge=1, le=10)
    base_delay_seconds: float = Field(default=0.25, ge=0.0)
    max_delay_seconds: float = Field(default=2.0, ge=0.0)

    def delay_for(self, failed_attempt: int) -> float:
        """Return the delay after a failed 1-indexed attempt."""
        if failed_attempt < 1:
            raise ValueError("failed_attempt must be at least 1")
        return min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2 ** (failed_attempt - 1)),
        )

    def execute(
        self,
        operation: Callable[[], T],
        *,
        before_attempt: Callable[[int], None] | None = None,
        on_retry: Callable[["RetryEvent"], None] | None = None,
        sleep: Callable[[float], None] = default_sleep,
    ) -> T:
        """Execute one operation with bounded transient-error retries."""
        for attempt in range(1, self.max_attempts + 1):
            if before_attempt is not None:
                before_attempt(attempt)
            try:
                return operation()
            except ProviderCallError as exc:
                if not exc.retryable or attempt >= self.max_attempts:
                    raise
                event = RetryEvent(
                    provider=exc.provider,
                    error_type=exc.error_type.value,
                    failed_attempt=attempt,
                    next_attempt=attempt + 1,
                    delay_seconds=self.delay_for(attempt),
                )
                if on_retry is not None:
                    on_retry(event)
                sleep(event.delay_seconds)
        raise RuntimeError("unreachable retry state")


@dataclass(frozen=True)
class RetryEvent:
    """Sanitized data emitted before a retry is attempted."""

    provider: str
    error_type: str
    failed_attempt: int
    next_attempt: int
    delay_seconds: float

