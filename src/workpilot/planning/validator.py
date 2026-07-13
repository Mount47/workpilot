"""Deterministic safety validation for structured plans."""

from workpilot.contracts import MissionContract
from workpilot.planning.models import Plan
from workpilot.planning.registry import ToolRegistry
from pydantic import ValidationError


class PlanValidationError(ValueError):
    """One or more deterministic plan checks failed."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("Plan validation failed: " + "; ".join(errors))


class PlanValidator:
    """Validate tool, dependency and budget boundaries before execution."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def validate(
        self,
        plan: Plan,
        contract: MissionContract,
        *,
        reserved_runtime_steps: int = 1,
    ) -> None:
        errors: list[str] = []
        step_ids = [step.step_id for step in plan.steps]
        known_ids = set(step_ids)

        if len(step_ids) != len(known_ids):
            errors.append("step IDs must be unique")

        required_steps = len(plan.steps) + reserved_runtime_steps
        if required_steps > contract.max_steps:
            errors.append(
                f"plan requires at least {required_steps} runtime steps but "
                f"max_steps is {contract.max_steps}"
            )

        for step in plan.steps:
            spec = self.registry.get(step.tool)
            if spec is None:
                errors.append(f"step {step.step_id} references unknown tool {step.tool}")
            elif not contract.is_tool_allowed(step.tool):
                errors.append(f"step {step.step_id} uses unauthorized tool {step.tool}")
            elif step.evidence_required and not spec.evidence_required:
                errors.append(
                    f"step {step.step_id} requires evidence but tool {step.tool} "
                    "does not declare that contract"
                )
            elif step.success_rule_ids != spec.success_rule_ids:
                errors.append(
                    f"step {step.step_id} success rules do not match tool {step.tool}"
                )
            else:
                try:
                    self.registry.validate_inputs(step.tool, step.inputs)
                except (ValidationError, KeyError) as exc:
                    errors.append(
                        f"step {step.step_id} has invalid inputs for {step.tool}: "
                        f"{type(exc).__name__}"
                    )
            for dependency in step.dependencies:
                if dependency not in known_ids:
                    errors.append(
                        f"step {step.step_id} depends on missing step {dependency}"
                    )

        errors.extend(self._cycle_errors(plan))
        if errors:
            raise PlanValidationError(errors)
        plan.validated = True

    @staticmethod
    def _cycle_errors(plan: Plan) -> list[str]:
        graph = {step.step_id: step.dependencies for step in plan.steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> bool:
            if step_id in visiting:
                return True
            if step_id in visited or step_id not in graph:
                return False
            visiting.add(step_id)
            has_cycle = any(visit(dependency) for dependency in graph[step_id])
            visiting.remove(step_id)
            visited.add(step_id)
            return has_cycle

        cyclic = [step_id for step_id in graph if visit(step_id)]
        return [f"plan contains a dependency cycle involving {cyclic[0]}"] if cyclic else []
