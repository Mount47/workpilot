"""Typed contracts for reproducible offline evaluation."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from workpilot.planning import PlanDraft


class EvalCase(BaseModel):
    """A single workspace task with manually defined expected evidence."""

    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    workspace: Path
    goal: str = Field(min_length=1)
    expected_evidence_quotes: list[str] = Field(default_factory=list)
    expected_status: Literal["passed", "failed"] = "passed"


class EvalSuite(BaseModel):
    """A versioned collection of evaluation cases."""

    name: str = Field(min_length=1)
    version: str = Field(default="0.1", min_length=1)
    cases: list[EvalCase] = Field(min_length=1)


class EvalCaseResult(BaseModel):
    """Metrics and execution facts for one evaluation case."""

    case_id: str
    expected_status: str
    actual_status: str
    task_completed: bool
    evidence_precision: float
    evidence_recall: float
    citation_validity_rate: float
    claim_support_rate: float
    unsupported_claim_rate: float
    semantic_unverified_count: int = 0
    revision_count: int = 0
    error: str | None = None


class EvaluationSummary(BaseModel):
    """Macro-averaged metrics for an evaluation suite."""

    total_cases: int
    task_completion_rate: float
    evidence_precision: float
    evidence_recall: float
    citation_validity_rate: float
    claim_support_rate: float
    unsupported_claim_rate: float
    revision_recovery_rate: float | None
    average_revision_count: float


class EvaluationReport(BaseModel):
    """Machine-readable result of one suite execution."""

    suite_name: str
    suite_version: str
    provider: str
    summary: EvaluationSummary
    cases: list[EvalCaseResult]


class PlannerEvalCase(BaseModel):
    """One offline model-plan or Provider-failure contract case."""

    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    goal: str = Field(min_length=1)
    draft: PlanDraft | None = None
    provider_error: Literal[
        "rate_limit",
        "timeout",
        "network",
        "server",
        "authentication",
        "invalid_response",
    ] | None = None
    expected_selected: Literal["llm", "deterministic", "failed"]
    expected_fallback_reason: str | None = None
    input_tokens: int = Field(default=100, ge=0)
    output_tokens: int = Field(default=20, ge=0)
    latency_ms: float = Field(default=10.0, ge=0.0)

    @model_validator(mode="after")
    def validate_input_source(self) -> "PlannerEvalCase":
        if (self.draft is None) == (self.provider_error is None):
            raise ValueError("exactly one of draft or provider_error is required")
        return self


class PlannerEvalSuite(BaseModel):
    """Versioned offline Planner policy suite."""

    name: str = Field(min_length=1)
    version: str = Field(default="0.1", min_length=1)
    cases: list[PlannerEvalCase] = Field(min_length=1)


class PlannerEvalCaseResult(BaseModel):
    """Planner decision and usage facts for one case."""

    case_id: str
    expected_selected: str
    actual_selected: str
    decision_correct: bool
    plan_valid: bool
    fallback_used: bool
    unsafe_plan_accepted: bool
    fallback_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


class PlannerEvaluationSummary(BaseModel):
    """Macro metrics for Planner safety and routing decisions."""

    total_cases: int
    decision_accuracy: float
    plan_validity_rate: float
    llm_acceptance_rate: float
    fallback_rate: float
    failure_rate: float
    unsafe_acceptance_rate: float
    average_input_tokens: float
    average_output_tokens: float
    average_latency_ms: float


class PlannerEvaluationReport(BaseModel):
    """Machine-readable offline Planner evaluation report."""

    suite_name: str
    suite_version: str
    summary: PlannerEvaluationSummary
    cases: list[PlannerEvalCaseResult]
