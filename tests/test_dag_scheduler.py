"""Tests for deterministic serial DAG scheduling and result propagation."""

import pytest

from workpilot.planning import (
    CallableToolHandler,
    Plan,
    PlanExecutor,
    PlanStep,
    PlanStepStatus,
    SchedulerDeadlockError,
    SerialDAGScheduler,
    StepResultStore,
    ToolInput,
    ToolResult,
    ToolSpec,
)
from workpilot.planning.registry import ToolRegistry


def _step(step_id: str, dependencies: list[str] | None = None) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        objective=f"Execute {step_id}.",
        tool="test.execute",
        dependencies=dependencies or [],
        expected_output=f"Output from {step_id}.",
        success_criteria=[f"{step_id} completes."],
    )


def _scheduler(
    plan: Plan,
    callback,
    *,
    store: StepResultStore | None = None,
    events: list[tuple[str, dict]] | None = None,
) -> SerialDAGScheduler:
    registry = ToolRegistry()
    spec = ToolSpec(name="test.execute", description="Execute a test step.")
    registry.register(spec)
    registry.bind(
        CallableToolHandler(
            spec=spec,
            input_model=ToolInput,
            callback=callback,
            summarize=lambda output: {"value": str(output)},
        )
    )
    plan.validated = True
    return SerialDAGScheduler(
        PlanExecutor(plan, registry),
        result_store=store,
        on_event=(lambda event, payload: events.append((event, payload)))
        if events is not None
        else None,
    )


def test_scheduler_executes_diamond_dag_in_stable_plan_order() -> None:
    order: list[str] = []
    events: list[tuple[str, dict]] = []
    plan = Plan(
        plan_id="diamond",
        goal="Execute a diamond DAG.",
        created_by="test",
        steps=[
            _step("root"),
            _step("left", ["root"]),
            _step("right", ["root"]),
            _step("finish", ["left", "right"]),
        ],
    )
    scheduler = _scheduler(
        plan,
        lambda _, step: order.append(step.step_id) or step.step_id,
        events=events,
    )

    result = scheduler.run()

    assert order == ["root", "left", "right", "finish"]
    assert result.succeeded is True
    assert result.completed_step_ids == order
    assert scheduler.result_store.get("finish").output == "finish"
    assert events[0][0] == "scheduler_started"
    assert events[-1] == (
        "scheduler_completed",
        {
            "completed_count": 4,
            "failed_count": 0,
            "blocked_count": 0,
            "skipped_count": 0,
            "succeeded": True,
        },
    )


def test_scheduler_blocks_failed_descendants_but_runs_independent_branch() -> None:
    executed: list[str] = []
    plan = Plan(
        plan_id="failure",
        goal="Propagate one branch failure.",
        created_by="test",
        steps=[
            _step("failing"),
            _step("independent"),
            _step("blocked_child", ["failing"]),
            _step("blocked_grandchild", ["blocked_child"]),
        ],
    )

    def execute(_, step):
        executed.append(step.step_id)
        if step.step_id == "failing":
            raise RuntimeError("expected failure")
        return step.step_id

    result = _scheduler(plan, execute).run()

    assert executed == ["failing", "independent"]
    assert result.succeeded is False
    assert result.failed_step_ids == ["failing"]
    assert result.blocked_step_ids == ["blocked_child", "blocked_grandchild"]
    assert plan.get_step("blocked_child").last_error_type == "dependency_terminal"
    assert plan.get_step("independent").status == PlanStepStatus.COMPLETED


def test_scheduler_explicit_skip_blocks_dependent_step() -> None:
    plan = Plan(
        plan_id="skip",
        goal="Skip one step.",
        created_by="test",
        steps=[_step("optional"), _step("dependent", ["optional"])],
    )
    scheduler = _scheduler(plan, lambda *_: None)

    scheduler.skip_step("optional", reason="not_applicable")
    result = scheduler.run()

    assert result.skipped_step_ids == ["optional"]
    assert result.blocked_step_ids == ["dependent"]
    assert plan.get_step("optional").last_error_type == "not_applicable"


def test_scheduler_reports_deadlock_for_unvalidated_runtime_cycle() -> None:
    plan = Plan(
        plan_id="deadlock",
        goal="Detect a runtime deadlock.",
        created_by="test",
        steps=[_step("first", ["second"]), _step("second", ["first"])],
    )
    scheduler = _scheduler(plan, lambda *_: None)

    with pytest.raises(SchedulerDeadlockError) as exc_info:
        scheduler.run()

    assert exc_info.value.pending_step_ids == ["first", "second"]


def test_result_store_supports_dependency_output_without_trace_leakage() -> None:
    store = StepResultStore()
    plan = Plan(
        plan_id="results",
        goal="Pass internal results between steps.",
        created_by="test",
        steps=[_step("source"), _step("consumer", ["source"])],
    )

    def execute(_, step):
        if step.step_id == "source":
            return ToolResult(
                output="sensitive-body",
                output_summary={"item_count": 1},
                evidence_ids=["E-0001"],
            )
        assert store.get("source").output == "sensitive-body"
        return "consumed"

    scheduler = _scheduler(plan, execute, store=store)
    scheduler.run()

    snapshot = store.safe_snapshot()
    assert snapshot["source"] == {
        "status": "completed",
        "output_summary": {"item_count": 1},
        "evidence_ids": ["E-0001"],
    }
    assert "sensitive-body" not in str(snapshot)


def test_result_store_requires_explicit_overwrite_for_reentry() -> None:
    store = StepResultStore()
    store.put("step", ToolResult(output="first"))

    with pytest.raises(ValueError, match="already has a stored result"):
        store.put("step", ToolResult(output="second"))

    store.put("step", ToolResult(output="second"), allow_overwrite=True)
    assert store.get("step").output == "second"
