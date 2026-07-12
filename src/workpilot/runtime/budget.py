"""Deterministic execution budget enforcement for one agent run."""

from collections.abc import Callable
from time import monotonic


class BudgetExceededError(RuntimeError):
    """A run attempted to cross a Mission Contract resource limit."""

    def __init__(self, budget_type: str, limit: int | float, consumed: int | float) -> None:
        self.budget_type = budget_type
        self.limit = limit
        self.consumed = consumed
        super().__init__(
            f"{budget_type} budget exceeded: limit={limit}, consumed={consumed}"
        )


class ExecutionBudget:
    """Track and enforce step, wall-clock and token budgets."""

    def __init__(
        self,
        *,
        max_steps: int,
        token_budget: int,
        time_budget_seconds: int,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.max_steps = max_steps
        self.token_budget = token_budget
        self.time_budget_seconds = time_budget_seconds
        self._clock = clock
        self._started_at: float | None = None
        self.step_count = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def start(self) -> None:
        """Start wall-clock accounting exactly once."""
        if self._started_at is None:
            self._started_at = self._clock()

    def consume_step(self, step_name: str) -> int:
        """Reserve one logical execution step and return its sequence."""
        del step_name  # Names are recorded by Trace; budget tracks the count.
        self.check_time()
        attempted = self.step_count + 1
        if attempted > self.max_steps:
            raise BudgetExceededError("step", self.max_steps, attempted)
        self.step_count = attempted
        return self.step_count

    def check_model_call_allowed(self) -> None:
        """Reject a new model operation when no time or token budget remains."""
        self.check_time()
        if self.total_tokens >= self.token_budget:
            raise BudgetExceededError("token", self.token_budget, self.total_tokens)

    def consume_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Account an observed physical model call and enforce the total."""
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token usage cannot be negative")
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        if self.total_tokens > self.token_budget:
            raise BudgetExceededError("token", self.token_budget, self.total_tokens)

    def check_time(self) -> None:
        """Fail when elapsed wall-clock time is greater than the limit."""
        if self._started_at is None:
            self.start()
        elapsed = self.elapsed_seconds
        if elapsed > self.time_budget_seconds:
            raise BudgetExceededError("time", self.time_budget_seconds, elapsed)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def elapsed_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        return max(0.0, self._clock() - self._started_at)

    def snapshot(self) -> dict:
        """Return a JSON-safe resource summary."""
        return {
            "steps": {"used": self.step_count, "limit": self.max_steps},
            "tokens": {
                "input": self.input_tokens,
                "output": self.output_tokens,
                "used": self.total_tokens,
                "limit": self.token_budget,
            },
            "time": {
                "elapsed_seconds": round(self.elapsed_seconds, 6),
                "limit_seconds": self.time_budget_seconds,
            },
        }
