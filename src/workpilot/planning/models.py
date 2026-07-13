"""Typed contracts for plans, steps and registered tools."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PlanStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class SuccessRuleCheck(BaseModel):
    """One deterministic, safe success-rule result."""

    rule_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    passed: bool
    message: str = Field(min_length=1)


class SuccessCriteriaEvaluation(BaseModel):
    """Aggregate result for the system-owned rules on one PlanStep attempt."""

    passed: bool
    checks: list[SuccessRuleCheck] = Field(min_length=1)


class ToolSpec(BaseModel):
    """A tool capability that a Plan is allowed to reference."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    description: str = Field(min_length=1)
    version: str = Field(default="1.0", pattern=r"^[0-9]+\.[0-9]+$")
    reentrant: bool = False
    evidence_required: bool = False
    success_rule_ids: list[str] = Field(
        default_factory=lambda: ["tool_result.completed"],
        min_length=1,
    )


class PlanStep(BaseModel):
    """One validated unit in a structured execution plan."""

    model_config = ConfigDict(validate_assignment=True)

    step_id: str = Field(pattern=r"^[a-z][a-z0-9_-]+$")
    objective: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    expected_output: str = Field(min_length=1)
    success_criteria: list[str] = Field(min_length=1)
    success_rule_ids: list[str] = Field(
        default_factory=lambda: ["tool_result.completed"],
        min_length=1,
    )
    evidence_required: bool = False
    status: PlanStepStatus = PlanStepStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    last_error_type: str | None = None
    success_evaluation: SuccessCriteriaEvaluation | None = None


class PlanStepDraft(BaseModel):
    """Model-authored fields for one step; execution state is system-owned."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(pattern=r"^[a-z][a-z0-9_-]+$")
    objective: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    expected_output: str = Field(min_length=1)
    success_criteria: list[str] = Field(min_length=1)
    evidence_required: bool = False

    def to_plan_step(
        self,
        *,
        success_rule_ids: list[str] | None = None,
    ) -> PlanStep:
        """Create a fresh pending PlanStep without model-controlled state."""
        return PlanStep(
            **self.model_dump(),
            success_rule_ids=success_rule_ids or ["tool_result.completed"],
        )


class PlanDraft(BaseModel):
    """Restricted model response that cannot override goal or run identity."""

    model_config = ConfigDict(extra="forbid")

    steps: list[PlanStepDraft] = Field(min_length=1)


class Plan(BaseModel):
    """A complete plan that must be validated before execution."""

    model_config = ConfigDict(validate_assignment=True)

    plan_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    created_by: str
    steps: list[PlanStep] = Field(min_length=1)
    validated: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def get_step(self, step_id: str) -> PlanStep:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        raise KeyError(f"Plan does not contain step {step_id}")
