"""Tests for tool input schemas, handler binding and safe ToolResult output."""

import pytest
from pydantic import BaseModel, ConfigDict

from workpilot.planning import (
    CallableToolHandler,
    PlanStep,
    PlanValidationError,
    PlanValidator,
    ToolInput,
    ToolResult,
    ToolSpec,
    create_default_registry,
)
from workpilot.planning.registry import ToolRegistry
from workpilot.planning.planner import DeterministicPlanner
from workpilot.contracts import MissionContract


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str


def test_registry_validates_inputs_and_invokes_bound_handler(tmp_path) -> None:
    registry = ToolRegistry()
    spec = ToolSpec(name="search.query", description="Search authorized content.")
    registry.register(spec, SearchInput)
    handler = CallableToolHandler(
        spec=spec,
        input_model=SearchInput,
        callback=lambda tool_input, _: [tool_input.query, "secret-body"],
        summarize=lambda output: {"result_count": len(output)},
    )
    registry.bind(handler)
    step = PlanStep(
        step_id="search",
        objective="Search.",
        tool="search.query",
        inputs={"query": "risk"},
        expected_output="Matches.",
        success_criteria=["Search succeeds."],
    )

    result = registry.invoke(step.tool, step.inputs, step)

    assert result.output == ["risk", "secret-body"]
    assert result.output_summary == {"result_count": 2}
    dumped = result.model_dump()
    assert "output" not in dumped
    assert "secret-body" not in str(dumped)


def test_registry_rejects_unbound_tool_and_handler_contract_mismatch() -> None:
    registry = ToolRegistry()
    spec = ToolSpec(name="search.query", description="Search authorized content.")
    registry.register(spec, SearchInput)
    step = PlanStep(
        step_id="search",
        objective="Search.",
        tool="search.query",
        inputs={"query": "risk"},
        expected_output="Matches.",
        success_criteria=["Search succeeds."],
    )

    with pytest.raises(ValueError, match="no bound handler"):
        registry.invoke(step.tool, step.inputs, step)
    with pytest.raises(ValueError, match="input model does not match"):
        registry.bind(
            CallableToolHandler(
                spec=spec,
                input_model=ToolInput,
                callback=lambda *_: None,
            )
        )


def test_plan_validator_rejects_unknown_tool_input_fields(tmp_path) -> None:
    registry = create_default_registry()
    contract = MissionContract(
        run_id="tool-input",
        goal="生成报告",
        workspace_root=tmp_path,
    )
    plan = DeterministicPlanner().create_plan(contract)
    plan.get_step("scan_workspace").inputs = {"untrusted": "value"}

    with pytest.raises(PlanValidationError, match="invalid inputs"):
        PlanValidator(registry).validate(plan, contract)


def test_callable_handler_does_not_swallow_exception() -> None:
    spec = ToolSpec(name="task.fail", description="Fail for testing.")

    def fail(*_):
        raise RuntimeError("handler failed")

    handler = CallableToolHandler(spec=spec, callback=fail)
    step = PlanStep(
        step_id="fail",
        objective="Fail.",
        tool="task.fail",
        expected_output="Failure.",
        success_criteria=["Failure propagates."],
    )

    with pytest.raises(RuntimeError, match="handler failed"):
        handler.execute(ToolInput(), step)


def test_handler_can_return_explicit_tool_result() -> None:
    spec = ToolSpec(name="task.done", description="Return typed result.")
    expected = ToolResult(output="internal", output_summary={"count": 1})
    handler = CallableToolHandler(spec=spec, callback=lambda *_: expected)
    step = PlanStep(
        step_id="done",
        objective="Complete.",
        tool="task.done",
        expected_output="Result.",
        success_criteria=["Done."],
    )

    assert handler.execute(ToolInput(), step) is expected
