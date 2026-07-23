"""Content-minimized execution lifecycle observer contracts."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Protocol, runtime_checkable

from workpilot.planning.models import Plan, PlanStep
from workpilot.runtime.checkpoint_store import CheckpointFileRecord


def canonical_json_hash(value: object) -> str:
    """Hash a JSON value deterministically without retaining its content."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ToolCallContext:
    """Stable, safe identity shared by Trace and the durable ledger."""

    tool_call_id: str
    run_id: str
    plan_id: str
    step_id: str
    execution_no: int
    tool: str
    tool_version: str
    input_hash: str
    idempotency_key_hash: str


def build_tool_call_context(
    *,
    run_id: str,
    plan_id: str,
    step: PlanStep,
    execution_no: int,
    tool_version: str,
) -> ToolCallContext:
    """Derive one logical ToolCall identity from canonical system fields."""
    input_hash = canonical_json_hash(step.inputs)
    identity = canonical_json_hash(
        {
            "run_id": run_id,
            "plan_id": plan_id,
            "step_id": step.step_id,
            "execution_no": execution_no,
            "tool": step.tool,
            "tool_version": tool_version,
            "input_hash": input_hash,
        }
    )
    return ToolCallContext(
        tool_call_id=f"tc_{identity[:32]}",
        run_id=run_id,
        plan_id=plan_id,
        step_id=step.step_id,
        execution_no=execution_no,
        tool=step.tool,
        tool_version=tool_version,
        input_hash=input_hash,
        idempotency_key_hash=identity,
    )


@runtime_checkable
class ExecutionObserver(Protocol):
    """Observe safe execution state without receiving raw ToolResult output."""

    def plan_registered(self, plan: Plan, tool_versions: dict[str, str]) -> None: ...

    def tool_call_started(self, context: ToolCallContext) -> None: ...

    def next_checkpoint_sequence(self) -> int | None: ...

    def tool_call_completed(
        self,
        context: ToolCallContext,
        *,
        output_summary: dict,
        evidence_ids: tuple[str, ...],
        success_evaluation: dict | None,
        checkpoint: CheckpointFileRecord | None,
    ) -> None: ...

    def tool_call_failed(
        self,
        context: ToolCallContext,
        *,
        error_type: str,
        success_evaluation: dict | None,
    ) -> None: ...

    def scheduler_event(self, event_type: str, data: dict) -> None: ...

    def artifact_recorded(self, path: Path) -> None: ...


@runtime_checkable
class ExecutionGuard(Protocol):
    """Fail execution when the Worker can no longer prove Run ownership."""

    def assert_execution_allowed(self) -> None: ...


class NullExecutionGuard:
    """No-op guard for execution that does not claim a durable Run."""

    def assert_execution_allowed(self) -> None:
        pass


class NullExecutionObserver:
    """No-op observer used by CLI and unit tests without a Repository."""

    def plan_registered(self, plan: Plan, tool_versions: dict[str, str]) -> None:
        pass

    def tool_call_started(self, context: ToolCallContext) -> None:
        pass

    def next_checkpoint_sequence(self) -> int | None:
        return None

    def tool_call_completed(
        self,
        context: ToolCallContext,
        *,
        output_summary: dict,
        evidence_ids: tuple[str, ...],
        success_evaluation: dict | None,
        checkpoint: CheckpointFileRecord | None,
    ) -> None:
        pass

    def tool_call_failed(
        self,
        context: ToolCallContext,
        *,
        error_type: str,
        success_evaluation: dict | None,
    ) -> None:
        pass

    def scheduler_event(self, event_type: str, data: dict) -> None:
        pass

    def artifact_recorded(self, path: Path) -> None:
        pass
