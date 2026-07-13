"""Deterministic serial scheduling for validated plan DAGs."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from workpilot.planning.executor import PlanExecutor
from workpilot.planning.models import PlanStep, PlanStepStatus
from workpilot.planning.tools import ToolResult


SchedulerEventHandler = Callable[[str, dict[str, Any]], None]
SchedulerStepExecutor = Callable[[str], ToolResult]


class SchedulerDeadlockError(RuntimeError):
    """Raised when pending steps cannot become ready or terminal."""

    def __init__(self, pending_step_ids: list[str]) -> None:
        self.pending_step_ids = pending_step_ids
        super().__init__(
            "Scheduler deadlocked with pending steps: "
            + ", ".join(pending_step_ids)
        )


class StepResultStore:
    """Keep internal ToolResults scoped to one in-process plan execution."""

    def __init__(self) -> None:
        self._results: dict[str, ToolResult] = {}

    def put(
        self,
        step_id: str,
        result: ToolResult,
        *,
        allow_overwrite: bool = False,
    ) -> None:
        if step_id in self._results and not allow_overwrite:
            raise ValueError(f"Step {step_id} already has a stored result")
        self._results[step_id] = result

    def get(self, step_id: str) -> ToolResult:
        try:
            return self._results[step_id]
        except KeyError as exc:
            raise KeyError(f"Step {step_id} has no stored result") from exc

    def has(self, step_id: str) -> bool:
        return step_id in self._results

    def safe_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return summaries suitable for Trace; raw output stays excluded."""
        return {
            step_id: {
                "status": result.status,
                "output_summary": result.output_summary,
                "evidence_ids": result.evidence_ids,
            }
            for step_id, result in self._results.items()
        }


@dataclass(frozen=True)
class SchedulerRunResult:
    """Terminal scheduler state without exposing internal tool outputs."""

    completed_step_ids: list[str] = field(default_factory=list)
    failed_step_ids: list[str] = field(default_factory=list)
    blocked_step_ids: list[str] = field(default_factory=list)
    skipped_step_ids: list[str] = field(default_factory=list)
    pending_step_ids: list[str] = field(default_factory=list)
    paused: bool = False

    @property
    def succeeded(self) -> bool:
        return (
            not self.paused
            and not self.failed_step_ids
            and not self.blocked_step_ids
            and not self.pending_step_ids
        )


class SerialDAGScheduler:
    """Execute ready PlanSteps in stable plan order and propagate failures."""

    _BLOCKING_STATUSES = {
        PlanStepStatus.FAILED,
        PlanStepStatus.BLOCKED,
        PlanStepStatus.SKIPPED,
    }

    def __init__(
        self,
        executor: PlanExecutor,
        *,
        result_store: StepResultStore | None = None,
        on_event: SchedulerEventHandler | None = None,
        execute_step: SchedulerStepExecutor | None = None,
    ) -> None:
        self.executor = executor
        self.plan = executor.plan
        self.result_store = result_store or StepResultStore()
        self.on_event = on_event
        self.execute_step = execute_step or executor.execute_registered_step
        self._failure_causes: dict[str, Exception] = {}

    def ready_steps(self) -> list[PlanStep]:
        """Return pending steps whose dependencies have all completed."""
        return [
            step
            for step in self.plan.steps
            if step.status == PlanStepStatus.PENDING
            and all(
                self.plan.get_step(dependency).status
                == PlanStepStatus.COMPLETED
                for dependency in step.dependencies
            )
        ]

    def skip_step(self, step_id: str, *, reason: str = "scheduler_policy") -> None:
        """Explicitly skip a pending step before or during scheduling."""
        step = self.plan.get_step(step_id)
        if step.status != PlanStepStatus.PENDING:
            raise ValueError(
                f"Only pending steps can be skipped; {step_id} is {step.status.value}"
            )
        step.status = PlanStepStatus.SKIPPED
        step.last_error_type = reason
        self._emit("scheduler_step_skipped", step_id=step_id, reason=reason)

    def run(
        self,
        *,
        stop_before_step_ids: set[str] | None = None,
    ) -> SchedulerRunResult:
        """Run all reachable steps, continuing branches independent of failures."""
        stop_before = stop_before_step_ids or set()
        self._emit("scheduler_started", step_count=len(self.plan.steps))
        while True:
            changed = self._propagate_blocked_steps()
            ready = self.ready_steps()
            if ready:
                executable = [
                    step for step in ready if step.step_id not in stop_before
                ]
                if not executable:
                    result = self._build_result(paused=True)
                    self._emit(
                        "scheduler_paused",
                        pending_step_ids=result.pending_step_ids,
                        stop_before_step_ids=sorted(stop_before),
                    )
                    return result
                for step in executable:
                    self._emit(
                        "scheduler_step_ready",
                        step_id=step.step_id,
                        dependencies=step.dependencies,
                    )
                    try:
                        result = self.execute_step(step.step_id)
                    except Exception as exc:
                        self._failure_causes[step.step_id] = exc
                        self._emit(
                            "scheduler_step_failed",
                            step_id=step.step_id,
                            error_type=step.last_error_type or type(exc).__name__,
                        )
                        continue
                    self.result_store.put(step.step_id, result)
                    self._emit(
                        "scheduler_step_completed",
                        step_id=step.step_id,
                        output_summary=result.output_summary,
                        evidence_ids=result.evidence_ids,
                    )
                continue

            pending = [
                step.step_id
                for step in self.plan.steps
                if step.status == PlanStepStatus.PENDING
            ]
            if pending:
                if changed:
                    continue
                self._emit("scheduler_deadlocked", pending_step_ids=pending)
                raise SchedulerDeadlockError(pending)
            break

        result = self._build_result()
        self._emit(
            "scheduler_completed",
            completed_count=len(result.completed_step_ids),
            failed_count=len(result.failed_step_ids),
            blocked_count=len(result.blocked_step_ids),
            skipped_count=len(result.skipped_step_ids),
            succeeded=result.succeeded,
        )
        return result

    def raise_first_failure(self) -> None:
        """Re-raise the earliest plan-ordered tool failure, if one occurred."""
        for step in self.plan.steps:
            cause = self._failure_causes.get(step.step_id)
            if cause is not None:
                raise cause

    def _propagate_blocked_steps(self) -> bool:
        changed = False
        for step in self.plan.steps:
            if step.status != PlanStepStatus.PENDING:
                continue
            blocking_dependencies = [
                dependency
                for dependency in step.dependencies
                if self.plan.get_step(dependency).status
                in self._BLOCKING_STATUSES
            ]
            if not blocking_dependencies:
                continue
            step.status = PlanStepStatus.BLOCKED
            step.last_error_type = "dependency_terminal"
            changed = True
            self._emit(
                "scheduler_step_blocked",
                step_id=step.step_id,
                blocking_dependencies=blocking_dependencies,
            )
        return changed

    def _build_result(self, *, paused: bool = False) -> SchedulerRunResult:
        by_status = {
            status: [
                step.step_id for step in self.plan.steps if step.status == status
            ]
            for status in PlanStepStatus
        }
        return SchedulerRunResult(
            completed_step_ids=by_status[PlanStepStatus.COMPLETED],
            failed_step_ids=by_status[PlanStepStatus.FAILED],
            blocked_step_ids=by_status[PlanStepStatus.BLOCKED],
            skipped_step_ids=by_status[PlanStepStatus.SKIPPED],
            pending_step_ids=by_status[PlanStepStatus.PENDING],
            paused=paused,
        )

    def _emit(self, event_type: str, **payload: Any) -> None:
        if self.on_event is not None:
            self.on_event(event_type, payload)
