"""Deterministic execution state machine for validated PlanSteps."""

from collections.abc import Callable
from typing import Any

from workpilot.planning.models import Plan, PlanStep, PlanStepStatus
from workpilot.planning.registry import ToolRegistry


class PlanExecutor:
    """Execute explicitly selected steps while enforcing plan dependencies."""

    def __init__(self, plan: Plan, registry: ToolRegistry) -> None:
        if not plan.validated:
            raise ValueError("Plan must be validated before execution")
        self.plan = plan
        self.registry = registry

    def execute_step(
        self,
        step_id: str,
        handler: Callable[[PlanStep], Any],
        *,
        allow_reentry: bool = False,
    ) -> Any:
        step = self.plan.get_step(step_id)
        spec = self.registry.get(step.tool)
        if spec is None:
            raise ValueError(f"Tool {step.tool} is no longer registered")
        if step.status == PlanStepStatus.COMPLETED:
            if not allow_reentry:
                raise ValueError(f"Plan step {step_id} is already completed")
            if not spec.reentrant:
                raise ValueError(f"Tool {step.tool} does not allow step reentry")
        elif allow_reentry and not spec.reentrant:
            raise ValueError(f"Tool {step.tool} does not allow step reentry")

        incomplete = [
            dependency
            for dependency in step.dependencies
            if self.plan.get_step(dependency).status != PlanStepStatus.COMPLETED
        ]
        if incomplete:
            raise ValueError(
                f"Plan step {step_id} has incomplete dependencies: "
                f"{', '.join(incomplete)}"
            )

        step.status = PlanStepStatus.RUNNING
        step.attempts += 1
        step.last_error_type = None
        try:
            result = handler(step)
        except Exception as exc:
            step.status = PlanStepStatus.FAILED
            declared_type = getattr(exc, "error_type", None)
            step.last_error_type = (
                getattr(declared_type, "value", declared_type)
                if declared_type is not None
                else type(exc).__name__
            )
            raise
        else:
            step.status = PlanStepStatus.COMPLETED
            return result
