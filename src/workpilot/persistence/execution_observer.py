"""Adapt Runtime execution lifecycle events to the durable Repository."""

import hashlib
import mimetypes
from pathlib import Path

from workpilot.persistence.models import (
    ArtifactMetadataRecord,
    CheckpointMetadataRecord,
    PlanStepRecord,
    RunLease,
    ToolCallRecord,
)
from workpilot.persistence.repository import ExecutionRepository
from workpilot.planning.models import Plan
from workpilot.runtime.observer import (
    ToolCallContext,
    canonical_json_hash,
)
from workpilot.runtime.checkpoint_store import CheckpointFileRecord


class RepositoryExecutionObserver:
    """Persist content-minimized PlanStep and ToolCall state."""

    def __init__(
        self,
        repository: ExecutionRepository,
        *,
        run_id: str,
        output_dir: Path,
        lease: RunLease | None = None,
    ) -> None:
        self.repository = repository
        self.run_id = run_id
        self.output_dir = Path(output_dir).resolve()
        self.lease = lease

    def plan_registered(self, plan: Plan, tool_versions: dict[str, str]) -> None:
        records = [
            PlanStepRecord(
                run_id=self.run_id,
                plan_id=plan.plan_id,
                step_id=step.step_id,
                objective=step.objective,
                tool=step.tool,
                tool_version=tool_versions[step.tool],
                dependencies=tuple(step.dependencies),
                expected_output=step.expected_output,
                success_rule_ids=tuple(step.success_rule_ids),
                input_hash=canonical_json_hash(step.inputs),
            )
            for step in plan.steps
        ]
        self.repository.create_plan_steps(
            self.run_id,
            records,
            lease=self.lease,
        )

    def tool_call_started(self, context: ToolCallContext) -> None:
        self._validate_context(context)
        self.repository.start_tool_call(
            ToolCallRecord(
                tool_call_id=context.tool_call_id,
                run_id=context.run_id,
                step_id=context.step_id,
                execution_no=context.execution_no,
                idempotency_key_hash=context.idempotency_key_hash,
                tool=context.tool,
                tool_version=context.tool_version,
                input_hash=context.input_hash,
            ),
            lease=self.lease,
        )

    def next_checkpoint_sequence(self) -> int | None:
        latest = self.repository.get_latest_checkpoint(self.run_id)
        return 1 if latest is None else latest.sequence + 1

    def tool_call_completed(
        self,
        context: ToolCallContext,
        *,
        output_summary: dict,
        evidence_ids: tuple[str, ...],
        success_evaluation: dict | None,
        checkpoint: CheckpointFileRecord | None,
    ) -> None:
        self._validate_context(context)
        if checkpoint is None:
            raise ValueError("Repository execution requires a verified checkpoint")
        if checkpoint.run_id != self.run_id:
            raise ValueError("Checkpoint does not belong to observer Run")
        self.repository.complete_tool_call_with_checkpoint(
            context.tool_call_id,
            checkpoint=CheckpointMetadataRecord(
                run_id=checkpoint.run_id,
                sequence=checkpoint.sequence,
                step_id=context.step_id,
                schema_version=checkpoint.schema_version,
                relative_path=checkpoint.relative_path,
                sha256=checkpoint.sha256,
                byte_size=checkpoint.byte_size,
            ),
            output_summary=output_summary,
            evidence_ids=evidence_ids,
            success_evaluation=success_evaluation,
            lease=self.lease,
        )

    def tool_call_failed(
        self,
        context: ToolCallContext,
        *,
        error_type: str,
        success_evaluation: dict | None,
    ) -> None:
        self._validate_context(context)
        self.repository.fail_tool_call(
            context.tool_call_id,
            error_type=error_type,
            success_evaluation=success_evaluation,
            lease=self.lease,
        )

    def scheduler_event(self, event_type: str, data: dict) -> None:
        if event_type == "scheduler_step_blocked":
            self.repository.update_plan_step_status(
                self.run_id,
                data["step_id"],
                status="blocked",
                error_type="dependency_terminal",
                lease=self.lease,
            )
        elif event_type == "scheduler_step_skipped":
            self.repository.update_plan_step_status(
                self.run_id,
                data["step_id"],
                status="skipped",
                error_type=data.get("reason", "scheduler_policy"),
                lease=self.lease,
            )

    def artifact_recorded(self, path: Path) -> None:
        resolved = Path(path).resolve()
        if resolved == self.output_dir or self.output_dir not in resolved.parents:
            raise ValueError("Artifact is outside Run output directory")
        relative_path = resolved.relative_to(self.output_dir)
        relative_name = relative_path.as_posix()
        content = resolved.read_bytes()
        existing = self.repository.list_artifacts(self.run_id)
        version = 1 + max(
            (
                record.version
                for record in existing
                if record.name == relative_name
            ),
            default=0,
        )
        media_type = mimetypes.guess_type(resolved.name)[0]
        self.repository.record_artifact(
            ArtifactMetadataRecord(
                run_id=self.run_id,
                name=relative_name,
                version=version,
                relative_path=relative_path,
                sha256=hashlib.sha256(content).hexdigest(),
                byte_size=len(content),
                media_type=media_type or "application/octet-stream",
            ),
            lease=self.lease,
        )

    def _validate_context(self, context: ToolCallContext) -> None:
        if context.run_id != self.run_id:
            raise ValueError("ToolCall context does not belong to observer Run")
