"""Run-scoped working memory with a deliberately safe export boundary."""

from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from workpilot.contracts import MissionContract
from workpilot.domain import Evidence, ProjectSnapshot
from workpilot.evidence.store import EvidenceStore
from workpilot.planning.models import Plan
from workpilot.verification.base import VerifyResult


class MemoryStepStatus(str, Enum):
    """Lifecycle status of a logical Runtime step."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MemoryStep(BaseModel):
    """Current-state representation of one logical Runtime step."""

    step_id: str
    name: str
    sequence: int = Field(ge=1)
    status: MemoryStepStatus
    started_at: datetime
    finished_at: datetime | None = None
    error_type: str | None = None


class VerificationMemory(BaseModel):
    """Safe summary of the latest verification pass."""

    total_checks: int = 0
    error_count: int = 0
    warning_count: int = 0
    issues: list[dict[str, str]] = Field(default_factory=list)


class PlanStepMemory(BaseModel):
    """Safe execution state of a PlanStep without business inputs."""

    step_id: str
    tool: str
    dependencies: list[str]
    status: str
    attempts: int
    last_error_type: str | None
    success_rule_ids: list[str]
    success_criteria_passed: bool | None
    failed_success_rule_ids: list[str]


class PlanMemory(BaseModel):
    """Safe summary of the current structured plan."""

    plan_id: str
    created_by: str
    validated: bool
    steps: list[PlanStepMemory]


class WorkingMemorySnapshot(BaseModel):
    """JSON-safe, content-minimized view of a RunContext."""

    run_id: str
    goal: str
    run_status: str
    contract_summary: dict[str, Any]
    steps: list[MemoryStep]
    plan: PlanMemory | None = None
    evidence_ids: list[str]
    evidence_count: int
    project_snapshot_id: str | None
    claim_ids: list[str]
    verification: VerificationMemory
    revision_count: int
    has_revision_feedback: bool
    artifact_names: list[str]
    budget: dict[str, Any]
    last_error_type: str | None


class WorkingMemory:
    """Aggregate mutable state for exactly one agent run.

    WorkingMemory owns no Provider or connector and performs no I/O. Runtime is
    the sole writer; consumers such as a future Planner receive stable reads.
    """

    def __init__(
        self,
        contract: MissionContract,
        evidence_store: EvidenceStore | None = None,
    ) -> None:
        self.contract = contract
        self.run_id = contract.run_id
        self.goal = contract.goal
        self.run_status = "pending"
        self.evidence_store = evidence_store or EvidenceStore(run_id=contract.run_id)
        if self.evidence_store.run_id != contract.run_id:
            raise ValueError("EvidenceStore and MissionContract run_id must match")
        self._steps: dict[str, MemoryStep] = {}
        self._plan: Plan | None = None
        self._project_snapshot: ProjectSnapshot | None = None
        self._verification = VerificationMemory()
        self._revision_feedback: str | None = None
        self._revision_count = 0
        self._artifact_names: set[str] = set()
        self._budget: dict[str, Any] = {}
        self._last_error_type: str | None = None

    def start_step(self, step_id: str, name: str, sequence: int) -> None:
        if step_id in self._steps:
            raise ValueError(f"Working Memory already contains step {step_id}")
        self._steps[step_id] = MemoryStep(
            step_id=step_id,
            name=name,
            sequence=sequence,
            status=MemoryStepStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )

    def complete_step(self, step_id: str) -> None:
        step = self._running_step(step_id)
        step.status = MemoryStepStatus.COMPLETED
        step.finished_at = datetime.now(timezone.utc)

    def fail_step(self, step_id: str, error_type: str) -> None:
        step = self._running_step(step_id)
        step.status = MemoryStepStatus.FAILED
        step.finished_at = datetime.now(timezone.utc)
        step.error_type = error_type
        self._last_error_type = error_type

    def add_evidence(self, evidence: Evidence) -> None:
        self.evidence_store.insert(evidence)

    def set_plan(self, plan: Plan) -> None:
        self._plan = plan

    @property
    def plan(self) -> Plan | None:
        return self._plan

    def set_project_snapshot(self, snapshot: ProjectSnapshot) -> None:
        self._project_snapshot = snapshot

    @property
    def project_snapshot(self) -> ProjectSnapshot | None:
        return self._project_snapshot

    def record_verification(self, results: list[VerifyResult]) -> None:
        issues = [
            {
                "check_id": result.check_id,
                "severity": result.severity,
                "location": result.location,
            }
            for result in results
            if result.status == "failed" or result.severity == "warning"
        ]
        self._verification = VerificationMemory(
            total_checks=len(results),
            error_count=sum(
                result.status == "failed" and result.severity == "error"
                for result in results
            ),
            warning_count=sum(result.severity == "warning" for result in results),
            issues=issues,
        )
        self._revision_feedback = None

    def request_revision(self, feedback: str) -> None:
        if not feedback.strip():
            raise ValueError("revision feedback cannot be empty")
        self._revision_feedback = feedback
        self._revision_count += 1

    @property
    def revision_feedback(self) -> str | None:
        return self._revision_feedback

    def record_artifacts(self, artifact_names: list[str]) -> None:
        self._artifact_names.update(artifact_names)

    def update_budget(self, budget_snapshot: dict[str, Any]) -> None:
        self._budget = deepcopy(budget_snapshot)

    def set_run_status(self, status: str, error_type: str | None = None) -> None:
        self.run_status = status
        if error_type:
            self._last_error_type = error_type

    def restore_checkpoint_state(
        self,
        *,
        plan: Plan,
        project_snapshot: ProjectSnapshot | None,
        verification_results: list[VerifyResult],
        revision_attempt: int,
        revision_feedback: list[str],
        artifact_names: list[str],
        budget: dict[str, Any],
    ) -> None:
        """Restore only fields covered by the versioned checkpoint contract."""
        if plan.goal != self.goal:
            raise ValueError("checkpoint Plan goal does not match Working Memory")
        if revision_attempt < 1:
            raise ValueError("revision_attempt must be at least 1")
        self._plan = plan
        self._project_snapshot = project_snapshot
        self.record_verification(verification_results)
        self._revision_count = revision_attempt - 1
        self._revision_feedback = (
            revision_feedback[-1] if revision_feedback else None
        )
        self._artifact_names = set(artifact_names)
        self._budget = deepcopy(budget)
        self.run_status = "running"

    def export(self) -> WorkingMemorySnapshot:
        evidence_ids = [
            evidence.evidence_id for evidence in self.evidence_store.list_all()
        ]
        snapshot_id = (
            self._project_snapshot.snapshot_id if self._project_snapshot else None
        )
        claim_ids = (
            [claim.claim_id for claim in self._project_snapshot.claims]
            if self._project_snapshot
            else []
        )
        plan_memory = None
        if self._plan is not None:
            plan_memory = PlanMemory(
                plan_id=self._plan.plan_id,
                created_by=self._plan.created_by,
                validated=self._plan.validated,
                steps=[
                    PlanStepMemory(
                        step_id=step.step_id,
                        tool=step.tool,
                        dependencies=list(step.dependencies),
                        status=step.status.value,
                        attempts=step.attempts,
                        last_error_type=step.last_error_type,
                        success_rule_ids=list(step.success_rule_ids),
                        success_criteria_passed=(
                            step.success_evaluation.passed
                            if step.success_evaluation is not None
                            else None
                        ),
                        failed_success_rule_ids=(
                            [
                                check.rule_id
                                for check in step.success_evaluation.checks
                                if not check.passed
                            ]
                            if step.success_evaluation is not None
                            else []
                        ),
                    )
                    for step in self._plan.steps
                ],
            )
        return WorkingMemorySnapshot(
            run_id=self.run_id,
            goal=self.goal,
            run_status=self.run_status,
            contract_summary={
                "workspace_root": str(self.contract.workspace_root),
                "allowed_tools": list(self.contract.allowed_tools),
                "max_steps": self.contract.max_steps,
                "token_budget": self.contract.token_budget,
                "time_budget_seconds": self.contract.time_budget_seconds,
                "allowed_artifact_types": list(self.contract.allowed_artifact_types),
            },
            steps=list(self._steps.values()),
            plan=plan_memory,
            evidence_ids=evidence_ids,
            evidence_count=len(evidence_ids),
            project_snapshot_id=snapshot_id,
            claim_ids=claim_ids,
            verification=self._verification,
            revision_count=self._revision_count,
            has_revision_feedback=self._revision_feedback is not None,
            artifact_names=sorted(self._artifact_names),
            budget=deepcopy(self._budget),
            last_error_type=self._last_error_type,
        )

    def _running_step(self, step_id: str) -> MemoryStep:
        step = self._steps.get(step_id)
        if step is None:
            raise KeyError(f"Working Memory does not contain step {step_id}")
        if step.status != MemoryStepStatus.RUNNING:
            raise ValueError(f"Step {step_id} is already {step.status.value}")
        return step
