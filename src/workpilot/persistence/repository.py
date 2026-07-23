"""Repository contract and deterministic in-memory implementation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from threading import RLock
from typing import Protocol, runtime_checkable

from workpilot.persistence.models import (
    ArtifactMetadataRecord,
    CheckpointMetadataRecord,
    CreateRunResult,
    PlanStepRecord,
    RunLease,
    RunRecord,
    ToolCallRecord,
    utc_now,
)


class RunRepositoryError(RuntimeError):
    """Base class for stable, content-free repository failures."""


class RunAlreadyExistsError(RunRepositoryError):
    """Raised when a run ID already exists."""


class RunNotFoundError(RunRepositoryError):
    """Raised when updating a run that does not exist."""


class RepositoryUnavailableError(RunRepositoryError):
    """Raised when the backing store cannot complete an operation."""


class IdempotencyConflictError(RunRepositoryError):
    """Raised when one idempotency key is reused for different parameters."""


class ExecutionRecordConflictError(RunRepositoryError):
    """Raised when a durable execution identity has conflicting content."""


class PlanStepNotFoundError(RunRepositoryError):
    """Raised when a ToolCall references a missing PlanStep."""


class ToolCallNotFoundError(RunRepositoryError):
    """Raised when completing a ToolCall that does not exist."""


class CheckpointSequenceError(RunRepositoryError):
    """Raised when a checkpoint does not immediately follow the committed one."""


class LeaseConflictError(RunRepositoryError):
    """Raised when a Run cannot be claimed because another lease is active."""


class LeaseLostError(RunRepositoryError):
    """Raised when a Worker can no longer prove ownership of a Run."""


@runtime_checkable
class RunRepository(Protocol):
    """Minimal durable boundary used by the API run lifecycle."""

    def create(self, record: RunRecord) -> RunRecord: ...

    def create_or_get(
        self,
        record: RunRecord,
        *,
        key_hash: str,
        request_fingerprint: str,
    ) -> CreateRunResult: ...

    def get(self, run_id: str) -> RunRecord | None: ...

    def list(self, *, limit: int | None = None) -> list[RunRecord]: ...

    def update_state(
        self,
        run_id: str,
        state: str,
        failure_reason: str | None = None,
        *,
        lease: RunLease | None = None,
    ) -> RunRecord: ...

    def claim_run(
        self, run_id: str, owner_token: str, *, ttl_seconds: int
    ) -> RunLease: ...

    def renew_lease(self, lease: RunLease, *, ttl_seconds: int) -> RunLease: ...

    def assert_lease(self, lease: RunLease) -> RunLease: ...

    def commit_terminal(
        self,
        lease: RunLease,
        state: str,
        failure_reason: str | None = None,
    ) -> RunRecord: ...

    def close(self) -> None: ...


@runtime_checkable
class ExecutionRepository(Protocol):
    """Durable control-plane ledger for plans, calls and artifacts."""

    def create_plan_steps(
        self,
        run_id: str,
        records: list[PlanStepRecord],
        *,
        lease: RunLease | None = None,
    ) -> None: ...

    def list_plan_steps(self, run_id: str) -> list[PlanStepRecord]: ...

    def update_plan_step_status(
        self,
        run_id: str,
        step_id: str,
        *,
        status: str,
        error_type: str | None = None,
        lease: RunLease | None = None,
    ) -> PlanStepRecord: ...

    def start_tool_call(
        self,
        record: ToolCallRecord,
        *,
        lease: RunLease | None = None,
    ) -> ToolCallRecord: ...

    def complete_tool_call(
        self,
        tool_call_id: str,
        *,
        output_summary: dict,
        evidence_ids: tuple[str, ...],
        success_evaluation: dict | None = None,
        lease: RunLease | None = None,
    ) -> ToolCallRecord: ...

    def complete_tool_call_with_checkpoint(
        self,
        tool_call_id: str,
        *,
        checkpoint: CheckpointMetadataRecord,
        output_summary: dict,
        evidence_ids: tuple[str, ...],
        success_evaluation: dict | None = None,
        lease: RunLease | None = None,
    ) -> ToolCallRecord: ...

    def fail_tool_call(
        self,
        tool_call_id: str,
        *,
        error_type: str,
        success_evaluation: dict | None = None,
        lease: RunLease | None = None,
    ) -> ToolCallRecord: ...

    def list_tool_calls(self, run_id: str) -> list[ToolCallRecord]: ...

    def record_artifact(
        self,
        record: ArtifactMetadataRecord,
        *,
        lease: RunLease | None = None,
    ) -> None: ...

    def list_artifacts(self, run_id: str) -> list[ArtifactMetadataRecord]: ...

    def list_checkpoints(self, run_id: str) -> list[CheckpointMetadataRecord]: ...

    def get_latest_checkpoint(
        self, run_id: str
    ) -> CheckpointMetadataRecord | None: ...


class InMemoryRunRepository:
    """Thread-safe development and test implementation of RunRepository."""

    _TERMINAL_STATES = {"passed", "failed", "cancelled", "recovery_failed"}

    def __init__(self, *, clock: Callable = utc_now) -> None:
        self._records: dict[str, RunRecord] = {}
        self._idempotency_index: dict[str, str] = {}
        self._plan_steps: dict[tuple[str, str], PlanStepRecord] = {}
        self._tool_calls: dict[str, ToolCallRecord] = {}
        self._tool_call_index: dict[tuple[str, str, int], str] = {}
        self._artifacts: dict[tuple[str, str, int], ArtifactMetadataRecord] = {}
        self._checkpoints: dict[tuple[str, int], CheckpointMetadataRecord] = {}
        self._lock = RLock()
        self._clock = clock

    def create(self, record: RunRecord) -> RunRecord:
        with self._lock:
            if record.run_id in self._records:
                raise RunAlreadyExistsError(f"run '{record.run_id}' already exists")
            if record.idempotency_key_hash is not None:
                existing = self._idempotency_index.get(record.idempotency_key_hash)
                if existing is not None:
                    raise IdempotencyConflictError("idempotency key already exists")
            self._records[record.run_id] = record
            if record.idempotency_key_hash is not None:
                self._idempotency_index[record.idempotency_key_hash] = record.run_id
            return record

    def create_or_get(
        self,
        record: RunRecord,
        *,
        key_hash: str,
        request_fingerprint: str,
    ) -> CreateRunResult:
        with self._lock:
            existing_id = self._idempotency_index.get(key_hash)
            if existing_id is not None:
                existing = self._records[existing_id]
                if existing.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflictError(
                        "idempotency key reused for a different request"
                    )
                return CreateRunResult(record=existing, created=False)
            persisted = record.with_idempotency(key_hash, request_fingerprint)
            if persisted.run_id in self._records:
                raise RunAlreadyExistsError(
                    f"run '{persisted.run_id}' already exists"
                )
            self._records[persisted.run_id] = persisted
            self._idempotency_index[key_hash] = persisted.run_id
            return CreateRunResult(record=persisted, created=True)

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._records.get(run_id)

    def list(self, *, limit: int | None = None) -> list[RunRecord]:
        with self._lock:
            records = sorted(
                self._records.values(),
                key=lambda item: (item.created_at, item.run_id),
                reverse=True,
            )
            return records if limit is None else records[:limit]

    def update_state(
        self,
        run_id: str,
        state: str,
        failure_reason: str | None = None,
        *,
        lease: RunLease | None = None,
    ) -> RunRecord:
        with self._lock:
            current = self._fence_write_unlocked(run_id, lease)
            updated = current.with_state(state, failure_reason)
            if state in self._TERMINAL_STATES:
                updated = replace(
                    updated,
                    lease_owner=None,
                    lease_expires_at=None,
                )
            self._records[run_id] = updated
            return updated

    def claim_run(
        self,
        run_id: str,
        owner_token: str,
        *,
        ttl_seconds: int,
    ) -> RunLease:
        if not owner_token:
            raise ValueError("owner_token must not be empty")
        self._validate_ttl(ttl_seconds)
        with self._lock:
            current = self._records.get(run_id)
            if current is None:
                raise RunNotFoundError(f"run '{run_id}' not found")
            if current.state in self._TERMINAL_STATES:
                raise LeaseConflictError("terminal Run cannot be claimed")
            now = self._clock()
            if (
                current.lease_owner is not None
                and current.lease_expires_at is not None
                and current.lease_expires_at > now
            ):
                raise LeaseConflictError("Run already has an active lease")
            expires_at = now + timedelta(seconds=ttl_seconds)
            state = "running" if current.state == "pending" else "recovering"
            updated = replace(
                current,
                state=state,
                lease_owner=owner_token,
                lease_expires_at=expires_at,
                execution_attempt=current.execution_attempt + 1,
                updated_at=now,
                version=current.version + 1,
            )
            self._records[run_id] = updated
            return self._lease_from_record(updated)

    def renew_lease(
        self,
        lease: RunLease,
        *,
        ttl_seconds: int,
    ) -> RunLease:
        self._validate_ttl(ttl_seconds)
        with self._lock:
            current = self._require_lease_unlocked(lease)
            now = self._clock()
            updated = replace(
                current,
                lease_expires_at=now + timedelta(seconds=ttl_seconds),
                updated_at=now,
                version=current.version + 1,
            )
            self._records[lease.run_id] = updated
            return self._lease_from_record(updated)

    def assert_lease(self, lease: RunLease) -> RunLease:
        with self._lock:
            return self._lease_from_record(self._require_lease_unlocked(lease))

    def commit_terminal(
        self,
        lease: RunLease,
        state: str,
        failure_reason: str | None = None,
    ) -> RunRecord:
        if state not in self._TERMINAL_STATES:
            raise ValueError("state must be terminal")
        with self._lock:
            current = self._require_lease_unlocked(lease)
            updated = replace(
                current,
                state=state,
                failure_reason=failure_reason,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=self._clock(),
                version=current.version + 1,
            )
            self._records[lease.run_id] = updated
            return updated

    @staticmethod
    def _validate_ttl(ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")

    @staticmethod
    def _lease_from_record(record: RunRecord) -> RunLease:
        if record.lease_owner is None or record.lease_expires_at is None:
            raise LeaseLostError("Run has no active lease")
        return RunLease(
            run_id=record.run_id,
            owner_token=record.lease_owner,
            execution_attempt=record.execution_attempt,
            expires_at=record.lease_expires_at,
        )

    def _require_lease_unlocked(self, lease: RunLease) -> RunRecord:
        current = self._records.get(lease.run_id)
        if current is None:
            raise RunNotFoundError(f"run '{lease.run_id}' not found")
        if current.state in self._TERMINAL_STATES:
            raise LeaseLostError("Run is already terminal")
        if (
            current.lease_owner != lease.owner_token
            or current.execution_attempt != lease.execution_attempt
        ):
            raise LeaseLostError("lease owner or execution attempt does not match")
        if current.lease_expires_at is None or current.lease_expires_at <= self._clock():
            raise LeaseLostError("lease has expired")
        return current

    def _fence_write_unlocked(
        self,
        run_id: str,
        lease: RunLease | None,
    ) -> RunRecord:
        current = self._records.get(run_id)
        if current is None:
            raise RunNotFoundError(f"run '{run_id}' not found")
        if current.lease_owner is None:
            if lease is not None:
                raise LeaseLostError("Run has no active lease")
            return current
        if lease is None:
            raise LeaseLostError("active Run lease is required for this write")
        if lease.run_id != run_id:
            raise LeaseLostError("lease does not belong to the target Run")
        return self._require_lease_unlocked(lease)

    def close(self) -> None:
        """Match persistent adapters; no resources are held here."""

    def create_plan_steps(
        self,
        run_id: str,
        records: list[PlanStepRecord],
        *,
        lease: RunLease | None = None,
    ) -> None:
        with self._lock:
            self._fence_write_unlocked(run_id, lease)
            for record in records:
                if record.run_id != run_id:
                    raise ValueError("all PlanSteps must belong to the requested Run")
                existing = self._plan_steps.get((run_id, record.step_id))
                if existing is not None and existing != record:
                    raise ExecutionRecordConflictError(
                        f"plan step '{record.step_id}' has conflicting content"
                    )
            for record in records:
                self._plan_steps.setdefault((run_id, record.step_id), record)

    def list_plan_steps(self, run_id: str) -> list[PlanStepRecord]:
        with self._lock:
            return sorted(
                [
                    record
                    for (record_run_id, _), record in self._plan_steps.items()
                    if record_run_id == run_id
                ],
                key=lambda record: (record.created_at, record.step_id),
            )

    def update_plan_step_status(
        self,
        run_id: str,
        step_id: str,
        *,
        status: str,
        error_type: str | None = None,
        lease: RunLease | None = None,
    ) -> PlanStepRecord:
        with self._lock:
            self._fence_write_unlocked(run_id, lease)
            key = (run_id, step_id)
            step = self._plan_steps.get(key)
            if step is None:
                raise PlanStepNotFoundError(f"plan step '{step_id}' not found")
            updated = step.with_terminal(status, error_type=error_type)
            self._plan_steps[key] = updated
            return updated

    def start_tool_call(
        self,
        record: ToolCallRecord,
        *,
        lease: RunLease | None = None,
    ) -> ToolCallRecord:
        with self._lock:
            self._fence_write_unlocked(record.run_id, lease)
            step_key = (record.run_id, record.step_id)
            step = self._plan_steps.get(step_key)
            if step is None:
                raise PlanStepNotFoundError(
                    f"plan step '{record.step_id}' not found"
                )
            logical_key = (record.run_id, record.step_id, record.execution_no)
            if (
                record.tool_call_id in self._tool_calls
                or logical_key in self._tool_call_index
            ):
                raise ExecutionRecordConflictError("tool call already exists")
            updated_step = step.with_started(record.execution_no)
            started = record.with_started()
            self._plan_steps[step_key] = updated_step
            self._tool_calls[started.tool_call_id] = started
            self._tool_call_index[logical_key] = started.tool_call_id
            return started

    def complete_tool_call(
        self,
        tool_call_id: str,
        *,
        output_summary: dict,
        evidence_ids: tuple[str, ...],
        success_evaluation: dict | None = None,
        lease: RunLease | None = None,
    ) -> ToolCallRecord:
        with self._lock:
            call = self._tool_calls.get(tool_call_id)
            if call is None:
                raise ToolCallNotFoundError(f"tool call '{tool_call_id}' not found")
            self._fence_write_unlocked(call.run_id, lease)
            step_key = (call.run_id, call.step_id)
            step = self._plan_steps[step_key]
            completed = call.with_completed(
                output_summary=output_summary,
                evidence_ids=evidence_ids,
            )
            completed_step = step.with_terminal(
                "completed",
                success_evaluation=success_evaluation,
            )
            self._tool_calls[tool_call_id] = completed
            self._plan_steps[step_key] = completed_step
            return completed

    def complete_tool_call_with_checkpoint(
        self,
        tool_call_id: str,
        *,
        checkpoint: CheckpointMetadataRecord,
        output_summary: dict,
        evidence_ids: tuple[str, ...],
        success_evaluation: dict | None = None,
        lease: RunLease | None = None,
    ) -> ToolCallRecord:
        with self._lock:
            call = self._tool_calls.get(tool_call_id)
            if call is None:
                raise ToolCallNotFoundError(f"tool call '{tool_call_id}' not found")
            self._fence_write_unlocked(call.run_id, lease)
            step_key = (call.run_id, call.step_id)
            step = self._plan_steps[step_key]
            run = self._records[call.run_id]
            if checkpoint.run_id != call.run_id or checkpoint.step_id != call.step_id:
                raise ExecutionRecordConflictError(
                    "checkpoint identity does not match the ToolCall"
                )
            checkpoint_key = (checkpoint.run_id, checkpoint.sequence)
            existing = self._checkpoints.get(checkpoint_key)
            if call.state == "completed":
                if (
                    existing != checkpoint
                    or step.checkpoint_sequence != checkpoint.sequence
                    or call.output_summary != output_summary
                    or call.evidence_ids != evidence_ids
                    or step.success_evaluation != success_evaluation
                ):
                    raise ExecutionRecordConflictError(
                        "completed ToolCall has conflicting checkpoint content"
                    )
                return call
            expected = run.checkpoint_sequence + 1
            if checkpoint.sequence != expected:
                raise CheckpointSequenceError(
                    f"checkpoint sequence expected {expected}, got {checkpoint.sequence}"
                )
            if existing is not None:
                raise ExecutionRecordConflictError("checkpoint sequence already exists")

            completed = call.with_completed(
                output_summary=output_summary,
                evidence_ids=evidence_ids,
            )
            completed_step = step.with_checkpoint_completed(
                checkpoint.sequence,
                success_evaluation=success_evaluation,
            )
            advanced_run = run.with_checkpoint_sequence(checkpoint.sequence)
            self._checkpoints[checkpoint_key] = checkpoint
            self._tool_calls[tool_call_id] = completed
            self._plan_steps[step_key] = completed_step
            self._records[call.run_id] = advanced_run
            return completed

    def fail_tool_call(
        self,
        tool_call_id: str,
        *,
        error_type: str,
        success_evaluation: dict | None = None,
        lease: RunLease | None = None,
    ) -> ToolCallRecord:
        with self._lock:
            call = self._tool_calls.get(tool_call_id)
            if call is None:
                raise ToolCallNotFoundError(f"tool call '{tool_call_id}' not found")
            self._fence_write_unlocked(call.run_id, lease)
            step_key = (call.run_id, call.step_id)
            step = self._plan_steps[step_key]
            failed = call.with_failed(error_type)
            failed_step = step.with_terminal(
                "failed",
                error_type=error_type,
                success_evaluation=success_evaluation,
            )
            self._tool_calls[tool_call_id] = failed
            self._plan_steps[step_key] = failed_step
            return failed

    def list_tool_calls(self, run_id: str) -> list[ToolCallRecord]:
        with self._lock:
            return sorted(
                [call for call in self._tool_calls.values() if call.run_id == run_id],
                key=lambda call: (call.started_at or call.finished_at, call.tool_call_id),
            )

    def record_artifact(
        self,
        record: ArtifactMetadataRecord,
        *,
        lease: RunLease | None = None,
    ) -> None:
        with self._lock:
            self._fence_write_unlocked(record.run_id, lease)
            key = (record.run_id, record.name, record.version)
            existing = self._artifacts.get(key)
            if existing is not None and existing != record:
                raise ExecutionRecordConflictError(
                    f"artifact '{record.name}' version {record.version} conflicts"
                )
            self._artifacts.setdefault(key, record)

    def list_artifacts(self, run_id: str) -> list[ArtifactMetadataRecord]:
        with self._lock:
            return sorted(
                [
                    artifact
                    for artifact in self._artifacts.values()
                    if artifact.run_id == run_id
                ],
                key=lambda artifact: (artifact.name, artifact.version),
            )

    def list_checkpoints(self, run_id: str) -> list[CheckpointMetadataRecord]:
        with self._lock:
            return sorted(
                [
                    checkpoint
                    for (record_run_id, _), checkpoint in self._checkpoints.items()
                    if record_run_id == run_id
                ],
                key=lambda checkpoint: checkpoint.sequence,
            )

    def get_latest_checkpoint(
        self,
        run_id: str,
    ) -> CheckpointMetadataRecord | None:
        checkpoints = self.list_checkpoints(run_id)
        return checkpoints[-1] if checkpoints else None
