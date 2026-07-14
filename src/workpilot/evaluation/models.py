"""Typed contracts for reproducible offline evaluation."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from workpilot.planning import PlanDraft


class ExpectedField(BaseModel):
    """A deliberately labelled field; explicit null is a negative label."""

    value: str | None

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("labelled field value cannot be blank")
        return normalized


class ExpectedActionItem(BaseModel):
    """Golden ActionItem anchored by its exact source-backed Claim text."""

    claim_text: str = Field(min_length=1)
    owner: ExpectedField | None = None
    due_date_text: ExpectedField | None = None


class ExpectedRisk(BaseModel):
    """Golden Risk anchored by its exact source-backed Claim text."""

    claim_text: str = Field(min_length=1)
    owner: ExpectedField | None = None
    severity: ExpectedField | None = None
    mitigation: ExpectedField | None = None

    @model_validator(mode="after")
    def validate_severity(self) -> "ExpectedRisk":
        if self.severity is not None and self.severity.value not in {
            None,
            "low",
            "medium",
            "high",
            "critical",
        }:
            raise ValueError("expected risk severity must use a normalized level")
        return self


class EvalCase(BaseModel):
    """A single workspace task with manually defined expected evidence."""

    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    workspace: Path
    goal: str = Field(min_length=1)
    expected_evidence_quotes: list[str] = Field(default_factory=list)
    expected_action_items: list[ExpectedActionItem] = Field(default_factory=list)
    expected_risks: list[ExpectedRisk] = Field(default_factory=list)
    expected_entities_exhaustive: bool = False
    expected_status: Literal["passed", "failed"] = "passed"
    bad_case_ids: list[str] = Field(default_factory=list)

    @field_validator("bad_case_ids")
    @classmethod
    def validate_bad_case_ids(cls, values: list[str]) -> list[str]:
        if any(
            len(value) != 6
            or not value.startswith("BC-")
            or not value[3:].isdigit()
            for value in values
        ):
            raise ValueError("bad_case_ids must use the BC-XXX format")
        if len(values) != len(set(values)):
            raise ValueError("bad_case_ids must not contain duplicates")
        return values

    @model_validator(mode="after")
    def validate_entity_anchors(self) -> "EvalCase":
        for name, entities in (
            ("expected_action_items", self.expected_action_items),
            ("expected_risks", self.expected_risks),
        ):
            anchors = [entity.claim_text for entity in entities]
            if len(anchors) != len(set(anchors)):
                raise ValueError(f"{name} claim_text anchors must be unique")
        return self


class EvalSuite(BaseModel):
    """A versioned collection of evaluation cases."""

    name: str = Field(min_length=1)
    version: str = Field(default="0.1", min_length=1)
    cases: list[EvalCase] = Field(min_length=1)


class PrecisionRecallMetric(BaseModel):
    """Auditable counts and rates for one Golden-labelled target."""

    precision: float | None = None
    recall: float | None = None
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0


class EntityAccuracyMetrics(BaseModel):
    """Entity detection and business-field Golden metrics."""

    action_item: PrecisionRecallMetric = Field(default_factory=PrecisionRecallMetric)
    risk: PrecisionRecallMetric = Field(default_factory=PrecisionRecallMetric)
    action_owner: PrecisionRecallMetric = Field(default_factory=PrecisionRecallMetric)
    action_due_date: PrecisionRecallMetric = Field(default_factory=PrecisionRecallMetric)
    risk_owner: PrecisionRecallMetric = Field(default_factory=PrecisionRecallMetric)
    risk_severity: PrecisionRecallMetric = Field(default_factory=PrecisionRecallMetric)
    risk_mitigation: PrecisionRecallMetric = Field(default_factory=PrecisionRecallMetric)


class EvalCaseResult(BaseModel):
    """Metrics and execution facts for one evaluation case."""

    case_id: str
    bad_case_ids: list[str] = Field(default_factory=list)
    expected_status: str
    actual_status: str
    task_completed: bool
    evidence_precision: float
    evidence_recall: float
    citation_validity_rate: float | None
    claim_support_rate: float | None
    unsupported_claim_rate: float | None
    entity_field_support_rate: float | None
    action_owner_population_rate: float | None
    action_due_date_population_rate: float | None
    risk_owner_population_rate: float | None
    risk_severity_population_rate: float | None
    risk_mitigation_population_rate: float | None
    entity_accuracy: EntityAccuracyMetrics = Field(
        default_factory=EntityAccuracyMetrics
    )
    source_coverage_rate: float
    evidence_acceptance_rate: float
    evidence_discard_rate: float
    locator_repair_count: int = 0
    claim_source_coverage_rate: float | None
    evidence_repair_trigger_count: int = 0
    evidence_repair_recovered: bool = False
    repair_model_call_count: int = 0
    repair_token_count: int = 0
    repair_estimated_cost: float | None = None
    semantic_unverified_count: int = 0
    revision_count: int = 0
    error: str | None = None


class EvaluationSummary(BaseModel):
    """Macro-averaged metrics for an evaluation suite."""

    total_cases: int
    task_completion_rate: float
    evidence_precision: float
    evidence_recall: float
    citation_validity_rate: float | None
    claim_support_rate: float | None
    unsupported_claim_rate: float | None
    entity_field_support_rate: float | None
    action_owner_population_rate: float | None
    action_due_date_population_rate: float | None
    risk_owner_population_rate: float | None
    risk_severity_population_rate: float | None
    risk_mitigation_population_rate: float | None
    entity_accuracy: EntityAccuracyMetrics = Field(
        default_factory=EntityAccuracyMetrics
    )
    source_coverage_rate: float
    evidence_acceptance_rate: float
    evidence_discard_rate: float
    average_locator_repair_count: float
    claim_source_coverage_rate: float | None
    evidence_repair_trigger_rate: float
    evidence_repair_recovery_rate: float | None
    average_repair_model_call_count: float
    average_repair_token_count: float
    average_repair_estimated_cost: float | None
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
