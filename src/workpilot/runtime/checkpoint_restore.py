"""Validated reconstruction of in-process Runtime state from checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from workpilot.contracts import MissionContract
from workpilot.domain import ProjectSnapshot
from workpilot.evidence.store import EvidenceStore
from workpilot.memory import WorkingMemory
from workpilot.persistence.repository import ExecutionRepository
from workpilot.planning import (
    Plan,
    PlanStepStatus,
    PlanValidator,
    RuntimePlanPolicy,
    StepResultStore,
    ToolRegistry,
)
from workpilot.runtime.budget import ExecutionBudget
from workpilot.runtime.checkpoint import (
    CheckpointCodecRegistry,
    CheckpointPayload,
    CheckpointValidationError,
)
from workpilot.runtime.checkpoint_store import CheckpointFileRecord, CheckpointStore
from workpilot.verification.base import VerifyResult


@dataclass(frozen=True, slots=True)
class RestoredExecutionState:
    """All state needed by the fixed Runtime pipeline after a committed step."""

    payload: CheckpointPayload
    plan: Plan
    result_store: StepResultStore
    evidence_store: EvidenceStore
    project_snapshot: ProjectSnapshot | None
    rendered_artifacts: dict[str, Any]
    verification_results: list[VerifyResult]
    verification_errors: list[VerifyResult]
    revision_attempt: int
    revision_feedback: list[str]
    budget: ExecutionBudget
    working_memory: WorkingMemory


class CheckpointRestorer:
    """Fail closed while rebuilding state for the current Runtime version."""

    def __init__(self, contract: MissionContract, registry: ToolRegistry) -> None:
        self.contract = contract
        self.registry = registry

    def restore_latest(
        self,
        repository: ExecutionRepository,
        store: CheckpointStore,
        run_id: str,
    ) -> RestoredExecutionState:
        if run_id != self.contract.run_id:
            raise CheckpointValidationError(
                "requested run_id does not match the Mission Contract"
            )
        metadata = repository.get_latest_checkpoint(run_id)
        if metadata is None:
            raise CheckpointValidationError("Run has no committed checkpoint")
        payload = store.load(
            CheckpointFileRecord(
                run_id=metadata.run_id,
                sequence=metadata.sequence,
                schema_version=metadata.schema_version,
                relative_path=metadata.relative_path,
                sha256=metadata.sha256,
                byte_size=metadata.byte_size,
            )
        )
        return self.restore(payload)

    def restore(self, payload: CheckpointPayload) -> RestoredExecutionState:
        try:
            if payload.run_id != self.contract.run_id:
                raise CheckpointValidationError(
                    "checkpoint run_id does not match the Mission Contract"
                )
            if payload.plan.goal != self.contract.goal:
                raise CheckpointValidationError(
                    "checkpoint Plan goal does not match the Mission Contract"
                )
            plan = payload.plan.model_copy(deep=True)
            for step in plan.steps:
                if step.status == PlanStepStatus.RUNNING:
                    step.status = PlanStepStatus.PENDING
                    step.last_error_type = None
                    step.success_evaluation = None
            PlanValidator(self.registry).validate(
                plan,
                self.contract,
                reserved_runtime_steps=1,
            )
            RuntimePlanPolicy().validate(plan)

            codec = CheckpointCodecRegistry()
            decoded_results = {}
            for step_id, encoded in payload.step_results.items():
                step = plan.get_step(step_id)
                if encoded.tool != step.tool:
                    raise CheckpointValidationError(
                        f"checkpoint tool does not match Plan step {step_id}"
                    )
                spec = self.registry.get(step.tool)
                if spec is None or encoded.tool_version != spec.version:
                    raise CheckpointValidationError(
                        f"checkpoint tool version is incompatible for {step.tool}"
                    )
                decoded_results[step_id] = codec.decode(encoded)

            missing_results = [
                step.step_id
                for step in plan.steps
                if step.status == PlanStepStatus.COMPLETED
                and step.step_id not in decoded_results
            ]
            if missing_results:
                raise CheckpointValidationError(
                    "completed Plan steps have no checkpoint result: "
                    + ", ".join(missing_results)
                )
            result_store = StepResultStore()
            result_store.restore(decoded_results)
            evidence_store = EvidenceStore.from_records(
                payload.run_id,
                payload.evidence,
            )
            runtime_state = payload.runtime_state
            claims_result = decoded_results.get("build_claims")
            if claims_result is not None and (
                claims_result.output != runtime_state.project_snapshot
            ):
                raise CheckpointValidationError(
                    "checkpoint ProjectSnapshot does not match claims result"
                )
            render_result = decoded_results.get("render_artifacts")
            if render_result is not None and (
                render_result.output != runtime_state.rendered_artifacts
            ):
                raise CheckpointValidationError(
                    "checkpoint rendered artifacts do not match tool result"
                )
            verification_results = [
                result.to_runtime() for result in runtime_state.verification_results
            ]
            verification_errors = [
                result.to_runtime() for result in runtime_state.verification_errors
            ]
            verify_result = decoded_results.get("verify")
            if verify_result is not None:
                decoded_checks, decoded_errors = verify_result.output
                if (
                    [item.to_dict() for item in decoded_checks]
                    != [item.to_dict() for item in verification_results]
                    or [item.to_dict() for item in decoded_errors]
                    != [item.to_dict() for item in verification_errors]
                ):
                    raise CheckpointValidationError(
                        "checkpoint verification state does not match tool result"
                    )
            budget_state = runtime_state.budget
            budget = ExecutionBudget.restore(
                max_steps=budget_state.steps_limit,
                token_budget=budget_state.token_limit,
                time_budget_seconds=budget_state.time_limit_seconds,
                step_count=budget_state.steps_used,
                input_tokens=budget_state.input_tokens,
                output_tokens=budget_state.output_tokens,
                elapsed_seconds=budget_state.elapsed_seconds,
            )
            memory = WorkingMemory(
                contract=self.contract,
                evidence_store=evidence_store,
            )
            memory.restore_checkpoint_state(
                plan=plan,
                project_snapshot=runtime_state.project_snapshot,
                verification_results=verification_results,
                revision_attempt=runtime_state.revision_attempt,
                revision_feedback=list(runtime_state.revision_feedback),
                artifact_names=sorted(runtime_state.rendered_artifacts),
                budget=budget.snapshot(),
            )
            return RestoredExecutionState(
                payload=payload,
                plan=plan,
                result_store=result_store,
                evidence_store=evidence_store,
                project_snapshot=runtime_state.project_snapshot,
                rendered_artifacts=dict(runtime_state.rendered_artifacts),
                verification_results=verification_results,
                verification_errors=verification_errors,
                revision_attempt=runtime_state.revision_attempt,
                revision_feedback=list(runtime_state.revision_feedback),
                budget=budget,
                working_memory=memory,
            )
        except CheckpointValidationError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise CheckpointValidationError(
                "checkpoint state cannot be reconstructed"
            ) from exc
