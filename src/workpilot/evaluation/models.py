"""Typed contracts for reproducible offline evaluation."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


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
