"""Runtime state definitions."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class RunState(str, Enum):
    """State machine states for a single run."""

    PENDING = "pending"
    PLANNING = "planning"
    RETRIEVING = "retrieving"
    SYNTHESIZING = "synthesizing"
    VERIFYING = "verifying"
    REVISING = "revising"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Run(BaseModel):
    """Represents a single agent execution."""

    run_id: str
    state: RunState = RunState.PENDING
    goal: str = ""
    workspace_root: str = ""
    output_dir: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_reason: str | None = None

    def transition(self, new_state: RunState) -> None:
        """Transition to a new state with basic validation."""
        terminal = {RunState.PASSED, RunState.FAILED, RunState.CANCELLED}
        if self.state in terminal:
            raise RuntimeError(
                f"Cannot transition from terminal state {self.state.value}"
            )
        self.state = new_state

        if new_state == RunState.PENDING:
            pass
        elif self.started_at is None:
            self.started_at = datetime.now(timezone.utc)

        if new_state in terminal:
            self.finished_at = datetime.now(timezone.utc)
