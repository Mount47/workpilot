"""Tests for system-owned deterministic PlanStep success rules."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from workpilot.contracts import MissionContract
from workpilot.planning import (
    CallableToolHandler,
    DeterministicPlanner,
    Plan,
    PlanDraft,
    PlanExecutor,
    PlanStep,
    PlanStepStatus,
    PlanValidationError,
    PlanValidator,
    SerialDAGScheduler,
    SuccessCriteriaError,
    SuccessRule,
    ToolInput,
    ToolResult,
    ToolSpec,
    create_default_registry,
    create_default_success_rule_registry,
)
from workpilot.planning.registry import ToolRegistry


def _contract(tmp_path: Path) -> MissionContract:
    return MissionContract(
        run_id="success-rules",
        goal="Generate a verified report.",
        workspace_root=tmp_path,
    )


def _step(
    step_id: str,
    *,
    dependencies: list[str] | None = None,
    success_rule_ids: list[str] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        objective=f"Execute {step_id}.",
        tool="test.rule",
        dependencies=dependencies or [],
        expected_output="A safe summary.",
        success_criteria=["The tool result satisfies its system contract."],
        success_rule_ids=success_rule_ids or ["tool_result.completed"],
    )


def test_default_plan_uses_tool_owned_success_rules(tmp_path: Path) -> None:
    registry = create_default_registry()
    plan = DeterministicPlanner().create_plan(_contract(tmp_path))

    PlanValidator(registry).validate(plan, _contract(tmp_path))

    for step in plan.steps:
        spec = registry.get(step.tool)
        assert spec is not None
        assert step.success_rule_ids == spec.success_rule_ids
        assert len(step.success_rule_ids) == 2


def test_plan_draft_forbids_model_injected_success_rule_ids() -> None:
    with pytest.raises(ValidationError, match="success_rule_ids"):
        PlanDraft.model_validate(
            {
                "steps": [
                    {
                        "step_id": "scan",
                        "objective": "Scan.",
                        "tool": "workspace.scan",
                        "expected_output": "Files.",
                        "success_criteria": ["Scan safely."],
                        "success_rule_ids": ["attacker.always_pass"],
                    }
                ]
            }
        )


def test_tool_registration_and_plan_validation_reject_rule_mismatch(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="Unknown success rules"):
        registry.register(
            ToolSpec(
                name="test.unknown",
                description="Use an unknown success rule.",
                success_rule_ids=["unknown.rule"],
            )
        )

    default_registry = create_default_registry()
    plan = DeterministicPlanner().create_plan(_contract(tmp_path))
    plan.get_step("scan_workspace").success_rule_ids = ["tool_result.completed"]

    with pytest.raises(PlanValidationError, match="success rules do not match"):
        PlanValidator(default_registry).validate(plan, _contract(tmp_path))


def test_invalid_tool_summary_fails_step_and_blocks_descendant() -> None:
    rules = create_default_success_rule_registry()
    rules.register(
        SuccessRule(
            rule_id="test.summary_valid",
            description="Summary must declare ok.",
            evaluator=lambda result: (
                result.output_summary.get("ok") is True,
                "Summary declares ok."
                if result.output_summary.get("ok") is True
                else "Summary does not declare ok.",
            ),
        )
    )
    registry = ToolRegistry(success_rules=rules)
    spec = ToolSpec(
        name="test.rule",
        description="Return a test summary.",
        success_rule_ids=["tool_result.completed", "test.summary_valid"],
    )
    registry.register(spec)
    registry.bind(
        CallableToolHandler(
            spec=spec,
            input_model=ToolInput,
            callback=lambda *_: ToolResult(output_summary={"ok": False}),
        )
    )
    plan = Plan(
        plan_id="criteria-failure",
        goal="Fail a deterministic rule.",
        created_by="test",
        steps=[
            _step("root", success_rule_ids=spec.success_rule_ids),
            _step(
                "child",
                dependencies=["root"],
                success_rule_ids=spec.success_rule_ids,
            ),
        ],
        validated=True,
    )
    scheduler = SerialDAGScheduler(PlanExecutor(plan, registry))

    result = scheduler.run()

    root = plan.get_step("root")
    assert result.failed_step_ids == ["root"]
    assert result.blocked_step_ids == ["child"]
    assert root.last_error_type == "success_criteria_failed"
    assert root.success_evaluation is not None
    assert root.success_evaluation.passed is False
    assert root.success_evaluation.checks[-1].rule_id == "test.summary_valid"
    with pytest.raises(SuccessCriteriaError, match="test.summary_valid"):
        scheduler.raise_first_failure()


def test_verification_business_errors_still_satisfy_tool_contract() -> None:
    registry = create_default_registry()
    plan = Plan(
        plan_id="verification-business-error",
        goal="Keep business verification separate from tool execution.",
        created_by="test",
        steps=[
            PlanStep(
                step_id="verify",
                objective="Verify report.",
                tool="verification.run",
                expected_output="Checks.",
                success_criteria=["Errors pass or trigger revision."],
                success_rule_ids=registry.get(
                    "verification.run"
                ).success_rule_ids,
                evidence_required=True,
            )
        ],
        validated=True,
    )
    spec = registry.get("verification.run")
    assert spec is not None
    registry.bind(
        CallableToolHandler(
            spec=spec,
            input_model=ToolInput,
            callback=lambda *_: ToolResult(
                output=([], ["business-error"]),
                output_summary={"check_count": 3, "error_count": 1},
            ),
        )
    )

    result = PlanExecutor(plan, registry).execute_registered_step("verify")

    assert result.output_summary["error_count"] == 1
    assert plan.get_step("verify").status == PlanStepStatus.COMPLETED
    assert plan.get_step("verify").success_evaluation.passed is True


def test_reentry_replaces_previous_success_evaluation() -> None:
    rules = create_default_success_rule_registry()
    rules.register(
        SuccessRule(
            rule_id="test.summary_valid",
            description="Summary must declare ok.",
            evaluator=lambda result: (
                result.output_summary.get("ok") is True,
                "Summary check completed.",
            ),
        )
    )
    registry = ToolRegistry(success_rules=rules)
    spec = ToolSpec(
        name="test.rule",
        description="Retry a test rule.",
        reentrant=True,
        success_rule_ids=["tool_result.completed", "test.summary_valid"],
    )
    registry.register(spec)
    outputs = iter([False, True])
    registry.bind(
        CallableToolHandler(
            spec=spec,
            input_model=ToolInput,
            callback=lambda *_: ToolResult(
                output_summary={"ok": next(outputs)},
            ),
        )
    )
    plan = Plan(
        plan_id="criteria-reentry",
        goal="Retry a failed rule.",
        created_by="test",
        steps=[_step("retry", success_rule_ids=spec.success_rule_ids)],
        validated=True,
    )
    executor = PlanExecutor(plan, registry)

    with pytest.raises(SuccessCriteriaError):
        executor.execute_registered_step("retry")
    assert plan.get_step("retry").success_evaluation.passed is False

    executor.execute_registered_step("retry", allow_reentry=True)

    step = plan.get_step("retry")
    assert step.status == PlanStepStatus.COMPLETED
    assert step.attempts == 2
    assert step.success_evaluation.passed is True
