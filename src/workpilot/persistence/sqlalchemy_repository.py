"""SQLAlchemy-backed RunRepository for PostgreSQL deployments."""

from __future__ import annotations

from datetime import timedelta, timezone
from pathlib import Path

from sqlalchemy import desc, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from workpilot.persistence.database import (
    ArtifactMetadataRow,
    CheckpointRow,
    PlanStepRow,
    RunRow,
    ToolCallRow,
    create_database_engine,
    create_session_factory,
)
from workpilot.persistence.models import (
    ArtifactMetadataRecord,
    CheckpointMetadataRecord,
    CreateRunResult,
    PlanStepRecord,
    RunLease,
    RunRecord,
    ToolCallRecord,
)
from workpilot.persistence.repository import (
    CheckpointSequenceError,
    ExecutionRecordConflictError,
    IdempotencyConflictError,
    LeaseConflictError,
    LeaseLostError,
    PlanStepNotFoundError,
    RepositoryUnavailableError,
    RunAlreadyExistsError,
    RunNotFoundError,
    ToolCallNotFoundError,
)


class SQLAlchemyRunRepository:
    """Persist Run lifecycle metadata with one transaction per operation."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        engine: Engine | None = None,
    ) -> None:
        self._sessions = sessions
        self._engine = engine

    @classmethod
    def from_url(cls, database_url: str) -> "SQLAlchemyRunRepository":
        try:
            engine = create_database_engine(database_url)
            return cls(create_session_factory(engine), engine=engine)
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "run repository configuration failed"
            ) from exc

    @staticmethod
    def _aware(value):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @staticmethod
    def _record(row: RunRow) -> RunRecord:
        return RunRecord(
            run_id=row.run_id,
            goal=row.goal,
            workspace=row.workspace,
            provider=row.provider,
            output_dir=Path(row.output_dir),
            state=row.state,
            failure_reason=row.failure_reason,
            idempotency_key_hash=row.idempotency_key_hash,
            request_fingerprint=row.request_fingerprint,
            checkpoint_sequence=row.checkpoint_sequence,
            lease_owner=row.lease_owner,
            lease_expires_at=SQLAlchemyRunRepository._aware(
                row.lease_expires_at
            ),
            execution_attempt=row.execution_attempt,
            created_at=SQLAlchemyRunRepository._aware(row.created_at),
            updated_at=SQLAlchemyRunRepository._aware(row.updated_at),
            version=row.version,
        )

    @staticmethod
    def _plan_step_record(row: PlanStepRow) -> PlanStepRecord:
        return PlanStepRecord(
            run_id=row.run_id,
            plan_id=row.plan_id,
            step_id=row.step_id,
            objective=row.objective,
            tool=row.tool,
            tool_version=row.tool_version,
            dependencies=tuple(row.dependencies),
            expected_output=row.expected_output,
            success_rule_ids=tuple(row.success_rule_ids),
            input_hash=row.input_hash,
            status=row.status,
            attempts=row.attempts,
            last_error_type=row.last_error_type,
            success_evaluation=row.success_evaluation,
            checkpoint_sequence=row.checkpoint_sequence,
            created_at=SQLAlchemyRunRepository._aware(row.created_at),
            updated_at=SQLAlchemyRunRepository._aware(row.updated_at),
            version=row.version,
        )

    @staticmethod
    def _tool_call_record(row: ToolCallRow) -> ToolCallRecord:
        return ToolCallRecord(
            tool_call_id=row.tool_call_id,
            run_id=row.run_id,
            step_id=row.step_id,
            execution_no=row.execution_no,
            idempotency_key_hash=row.idempotency_key_hash,
            tool=row.tool,
            tool_version=row.tool_version,
            input_hash=row.input_hash,
            state=row.state,
            physical_attempts=row.physical_attempts,
            output_summary=dict(row.output_summary),
            evidence_ids=tuple(row.evidence_ids),
            error_type=row.error_type,
            started_at=SQLAlchemyRunRepository._aware(row.started_at),
            finished_at=SQLAlchemyRunRepository._aware(row.finished_at),
            version=row.version,
        )

    @staticmethod
    def _artifact_record(row: ArtifactMetadataRow) -> ArtifactMetadataRecord:
        return ArtifactMetadataRecord(
            run_id=row.run_id,
            name=row.name,
            version=row.version,
            relative_path=Path(row.relative_path),
            sha256=row.sha256,
            byte_size=row.byte_size,
            media_type=row.media_type,
            state=row.state,
            created_at=SQLAlchemyRunRepository._aware(row.created_at),
        )

    @staticmethod
    def _checkpoint_record(row: CheckpointRow) -> CheckpointMetadataRecord:
        return CheckpointMetadataRecord(
            run_id=row.run_id,
            sequence=row.sequence,
            step_id=row.step_id,
            schema_version=row.schema_version,
            relative_path=Path(row.relative_path),
            sha256=row.sha256,
            byte_size=row.byte_size,
            created_at=SQLAlchemyRunRepository._aware(row.created_at),
        )

    @staticmethod
    def _plan_step_row(record: PlanStepRecord) -> PlanStepRow:
        return PlanStepRow(
            run_id=record.run_id,
            plan_id=record.plan_id,
            step_id=record.step_id,
            objective=record.objective,
            tool=record.tool,
            tool_version=record.tool_version,
            dependencies=list(record.dependencies),
            expected_output=record.expected_output,
            success_rule_ids=list(record.success_rule_ids),
            input_hash=record.input_hash,
            status=record.status,
            attempts=record.attempts,
            last_error_type=record.last_error_type,
            success_evaluation=record.success_evaluation,
            checkpoint_sequence=record.checkpoint_sequence,
            created_at=record.created_at,
            updated_at=record.updated_at,
            version=record.version,
        )

    @staticmethod
    def _tool_call_row(record: ToolCallRecord) -> ToolCallRow:
        return ToolCallRow(
            tool_call_id=record.tool_call_id,
            run_id=record.run_id,
            step_id=record.step_id,
            execution_no=record.execution_no,
            idempotency_key_hash=record.idempotency_key_hash,
            tool=record.tool,
            tool_version=record.tool_version,
            input_hash=record.input_hash,
            state=record.state,
            physical_attempts=record.physical_attempts,
            output_summary=dict(record.output_summary),
            evidence_ids=list(record.evidence_ids),
            error_type=record.error_type,
            started_at=record.started_at,
            finished_at=record.finished_at,
            version=record.version,
        )

    @staticmethod
    def _apply_plan_step(row: PlanStepRow, record: PlanStepRecord) -> None:
        row.status = record.status
        row.attempts = record.attempts
        row.last_error_type = record.last_error_type
        row.success_evaluation = record.success_evaluation
        row.checkpoint_sequence = record.checkpoint_sequence
        row.updated_at = record.updated_at
        row.version = record.version

    def create(self, record: RunRecord) -> RunRecord:
        row = RunRow(
            run_id=record.run_id,
            goal=record.goal,
            workspace=record.workspace,
            provider=record.provider,
            output_dir=str(record.output_dir),
            state=record.state,
            failure_reason=record.failure_reason,
            idempotency_key_hash=record.idempotency_key_hash,
            request_fingerprint=record.request_fingerprint,
            checkpoint_sequence=record.checkpoint_sequence,
            lease_owner=record.lease_owner,
            lease_expires_at=record.lease_expires_at,
            execution_attempt=record.execution_attempt,
            created_at=record.created_at,
            updated_at=record.updated_at,
            version=record.version,
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
        except IntegrityError as exc:
            raise RunAlreadyExistsError(
                f"run '{record.run_id}' already exists"
            ) from exc
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError("run repository create failed") from exc
        return record

    def create_or_get(
        self,
        record: RunRecord,
        *,
        key_hash: str,
        request_fingerprint: str,
    ) -> CreateRunResult:
        persisted = record.with_idempotency(key_hash, request_fingerprint)
        row = RunRow(
            run_id=persisted.run_id,
            goal=persisted.goal,
            workspace=persisted.workspace,
            provider=persisted.provider,
            output_dir=str(persisted.output_dir),
            state=persisted.state,
            failure_reason=persisted.failure_reason,
            idempotency_key_hash=persisted.idempotency_key_hash,
            request_fingerprint=persisted.request_fingerprint,
            checkpoint_sequence=persisted.checkpoint_sequence,
            lease_owner=persisted.lease_owner,
            lease_expires_at=persisted.lease_expires_at,
            execution_attempt=persisted.execution_attempt,
            created_at=persisted.created_at,
            updated_at=persisted.updated_at,
            version=persisted.version,
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
            return CreateRunResult(record=persisted, created=True)
        except IntegrityError:
            try:
                with self._sessions() as session:
                    existing = session.scalar(
                        select(RunRow).where(
                            RunRow.idempotency_key_hash == key_hash
                        )
                    )
                    if existing is None:
                        raise RunAlreadyExistsError(
                            f"run '{record.run_id}' already exists"
                        )
                    existing_record = self._record(existing)
                    if existing_record.request_fingerprint != request_fingerprint:
                        raise IdempotencyConflictError(
                            "idempotency key reused for a different request"
                        )
                    return CreateRunResult(record=existing_record, created=False)
            except (IdempotencyConflictError, RunAlreadyExistsError):
                raise
            except SQLAlchemyError as exc:
                raise RepositoryUnavailableError(
                    "run repository idempotency lookup failed"
                ) from exc
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "run repository idempotent create failed"
            ) from exc

    def get(self, run_id: str) -> RunRecord | None:
        try:
            with self._sessions() as session:
                row = session.get(RunRow, run_id)
                return None if row is None else self._record(row)
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError("run repository read failed") from exc

    def list(self, *, limit: int | None = None) -> list[RunRecord]:
        statement = select(RunRow).order_by(
            desc(RunRow.created_at), desc(RunRow.run_id)
        )
        if limit is not None:
            statement = statement.limit(limit)
        try:
            with self._sessions() as session:
                return [self._record(row) for row in session.scalars(statement)]
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError("run repository list failed") from exc

    def update_state(
        self,
        run_id: str,
        state: str,
        failure_reason: str | None = None,
        *,
        lease: RunLease | None = None,
    ) -> RunRecord:
        try:
            with self._sessions.begin() as session:
                now = self._database_now(session)
                row = self._locked_run(session, run_id)
                self._fence_write_row(row, lease, now)
                row.state = state
                row.failure_reason = failure_reason
                if state in self._TERMINAL_STATES:
                    row.lease_owner = None
                    row.lease_expires_at = None
                row.updated_at = now
                row.version += 1
                session.flush()
                return self._record(row)
        except RunNotFoundError:
            raise
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError("run repository update failed") from exc

    @staticmethod
    def _validate_ttl(ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")

    def _database_now(self, session: Session):
        value = session.scalar(select(func.now()))
        if value is None:
            raise RepositoryUnavailableError("database clock is unavailable")
        return self._aware(value)

    @staticmethod
    def _lease_from_row(row: RunRow) -> RunLease:
        expires_at = SQLAlchemyRunRepository._aware(row.lease_expires_at)
        if row.lease_owner is None or expires_at is None:
            raise LeaseLostError("Run has no active lease")
        return RunLease(
            run_id=row.run_id,
            owner_token=row.lease_owner,
            execution_attempt=row.execution_attempt,
            expires_at=expires_at,
        )

    @staticmethod
    def _locked_run(session: Session, run_id: str) -> RunRow:
        row = session.scalar(
            select(RunRow)
            .where(RunRow.run_id == run_id)
            .with_for_update()
        )
        if row is None:
            raise RunNotFoundError(f"run '{run_id}' not found")
        return row

    def _require_lease_row(
        self,
        row: RunRow,
        lease: RunLease,
        now,
    ) -> None:
        if row.state in self._TERMINAL_STATES:
            raise LeaseLostError("Run is already terminal")
        if (
            row.lease_owner != lease.owner_token
            or row.execution_attempt != lease.execution_attempt
        ):
            raise LeaseLostError("lease owner or execution attempt does not match")
        expires_at = self._aware(row.lease_expires_at)
        if expires_at is None or expires_at <= now:
            raise LeaseLostError("lease has expired")

    def _fence_write_row(
        self,
        row: RunRow,
        lease: RunLease | None,
        now,
    ) -> None:
        if row.lease_owner is None:
            if lease is not None:
                raise LeaseLostError("Run has no active lease")
            return
        if lease is None:
            raise LeaseLostError("active Run lease is required for this write")
        if lease.run_id != row.run_id:
            raise LeaseLostError("lease does not belong to the target Run")
        self._require_lease_row(row, lease, now)

    def _locked_call_and_run(
        self,
        session: Session,
        tool_call_id: str,
    ) -> tuple[ToolCallRow, RunRow]:
        probe = session.get(ToolCallRow, tool_call_id)
        if probe is None:
            raise ToolCallNotFoundError(f"tool call '{tool_call_id}' not found")
        run = self._locked_run(session, probe.run_id)
        call = session.scalar(
            select(ToolCallRow)
            .where(ToolCallRow.tool_call_id == tool_call_id)
            .with_for_update()
        )
        if call is None:
            raise ToolCallNotFoundError(f"tool call '{tool_call_id}' not found")
        return call, run

    _TERMINAL_STATES = {"passed", "failed", "cancelled", "recovery_failed"}

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
        try:
            with self._sessions.begin() as session:
                now = self._database_now(session)
                row = self._locked_run(session, run_id)
                if row.state in self._TERMINAL_STATES:
                    raise LeaseConflictError("terminal Run cannot be claimed")
                expires_at = self._aware(row.lease_expires_at)
                if row.lease_owner is not None and expires_at is not None:
                    if expires_at > now:
                        raise LeaseConflictError("Run already has an active lease")
                row.state = "running" if row.state == "pending" else "recovering"
                row.lease_owner = owner_token
                row.lease_expires_at = now + timedelta(seconds=ttl_seconds)
                row.execution_attempt += 1
                row.updated_at = now
                row.version += 1
                session.flush()
                return self._lease_from_row(row)
        except (LeaseConflictError, RunNotFoundError):
            raise
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError("run lease claim failed") from exc

    def renew_lease(
        self,
        lease: RunLease,
        *,
        ttl_seconds: int,
    ) -> RunLease:
        self._validate_ttl(ttl_seconds)
        try:
            with self._sessions.begin() as session:
                now = self._database_now(session)
                row = self._locked_run(session, lease.run_id)
                self._require_lease_row(row, lease, now)
                row.lease_expires_at = now + timedelta(seconds=ttl_seconds)
                row.updated_at = now
                row.version += 1
                session.flush()
                return self._lease_from_row(row)
        except (LeaseLostError, RunNotFoundError):
            raise
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError("run lease renewal failed") from exc

    def assert_lease(self, lease: RunLease) -> RunLease:
        try:
            with self._sessions.begin() as session:
                now = self._database_now(session)
                row = self._locked_run(session, lease.run_id)
                self._require_lease_row(row, lease, now)
                return self._lease_from_row(row)
        except (LeaseLostError, RunNotFoundError):
            raise
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError("run lease assertion failed") from exc

    def commit_terminal(
        self,
        lease: RunLease,
        state: str,
        failure_reason: str | None = None,
    ) -> RunRecord:
        if state not in self._TERMINAL_STATES:
            raise ValueError("state must be terminal")
        try:
            with self._sessions.begin() as session:
                now = self._database_now(session)
                row = self._locked_run(session, lease.run_id)
                self._require_lease_row(row, lease, now)
                row.state = state
                row.failure_reason = failure_reason
                row.lease_owner = None
                row.lease_expires_at = None
                row.updated_at = now
                row.version += 1
                session.flush()
                return self._record(row)
        except (LeaseLostError, RunNotFoundError):
            raise
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError("terminal Run commit failed") from exc

    def create_plan_steps(
        self,
        run_id: str,
        records: list[PlanStepRecord],
        *,
        lease: RunLease | None = None,
    ) -> None:
        try:
            with self._sessions.begin() as session:
                now = self._database_now(session)
                run = self._locked_run(session, run_id)
                self._fence_write_row(run, lease, now)
                for record in records:
                    if record.run_id != run_id:
                        raise ValueError(
                            "all PlanSteps must belong to the requested Run"
                        )
                    existing = session.get(
                        PlanStepRow,
                        (run_id, record.step_id),
                    )
                    if existing is None:
                        session.add(self._plan_step_row(record))
                    elif self._plan_step_record(existing) != record:
                        raise ExecutionRecordConflictError(
                            f"plan step '{record.step_id}' has conflicting content"
                        )
        except (RunNotFoundError, ExecutionRecordConflictError, ValueError):
            raise
        except IntegrityError as exc:
            raise ExecutionRecordConflictError(
                "plan step already exists with conflicting content"
            ) from exc
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "execution repository plan create failed"
            ) from exc

    def list_plan_steps(self, run_id: str) -> list[PlanStepRecord]:
        statement = (
            select(PlanStepRow)
            .where(PlanStepRow.run_id == run_id)
            .order_by(PlanStepRow.created_at, PlanStepRow.step_id)
        )
        try:
            with self._sessions() as session:
                return [self._plan_step_record(row) for row in session.scalars(statement)]
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "execution repository plan list failed"
            ) from exc

    def update_plan_step_status(
        self,
        run_id: str,
        step_id: str,
        *,
        status: str,
        error_type: str | None = None,
        lease: RunLease | None = None,
    ) -> PlanStepRecord:
        try:
            with self._sessions.begin() as session:
                now = self._database_now(session)
                run = self._locked_run(session, run_id)
                self._fence_write_row(run, lease, now)
                row = session.scalar(
                    select(PlanStepRow)
                    .where(
                        PlanStepRow.run_id == run_id,
                        PlanStepRow.step_id == step_id,
                    )
                    .with_for_update()
                )
                if row is None:
                    raise PlanStepNotFoundError(
                        f"plan step '{step_id}' not found"
                    )
                updated = self._plan_step_record(row).with_terminal(
                    status,
                    error_type=error_type,
                )
                self._apply_plan_step(row, updated)
                session.flush()
                return updated
        except PlanStepNotFoundError:
            raise
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "execution repository plan update failed"
            ) from exc

    def start_tool_call(
        self,
        record: ToolCallRecord,
        *,
        lease: RunLease | None = None,
    ) -> ToolCallRecord:
        try:
            with self._sessions.begin() as session:
                now = self._database_now(session)
                run = self._locked_run(session, record.run_id)
                self._fence_write_row(run, lease, now)
                step = session.scalar(
                    select(PlanStepRow)
                    .where(
                        PlanStepRow.run_id == record.run_id,
                        PlanStepRow.step_id == record.step_id,
                    )
                    .with_for_update()
                )
                if step is None:
                    raise PlanStepNotFoundError(
                        f"plan step '{record.step_id}' not found"
                    )
                existing = session.scalar(
                    select(ToolCallRow).where(
                        ToolCallRow.run_id == record.run_id,
                        ToolCallRow.step_id == record.step_id,
                        ToolCallRow.execution_no == record.execution_no,
                    )
                )
                if existing is not None or session.get(
                    ToolCallRow, record.tool_call_id
                ) is not None:
                    raise ExecutionRecordConflictError("tool call already exists")
                updated_step = self._plan_step_record(step).with_started(
                    record.execution_no
                )
                started = record.with_started()
                self._apply_plan_step(step, updated_step)
                session.add(self._tool_call_row(started))
                session.flush()
                return started
        except (PlanStepNotFoundError, ExecutionRecordConflictError, ValueError):
            raise
        except IntegrityError as exc:
            raise ExecutionRecordConflictError("tool call already exists") from exc
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "execution repository tool start failed"
            ) from exc

    def complete_tool_call(
        self,
        tool_call_id: str,
        *,
        output_summary: dict,
        evidence_ids: tuple[str, ...],
        success_evaluation: dict | None = None,
        lease: RunLease | None = None,
    ) -> ToolCallRecord:
        try:
            with self._sessions.begin() as session:
                now = self._database_now(session)
                call, run = self._locked_call_and_run(session, tool_call_id)
                self._fence_write_row(run, lease, now)
                step = session.scalar(
                    select(PlanStepRow)
                    .where(
                        PlanStepRow.run_id == call.run_id,
                        PlanStepRow.step_id == call.step_id,
                    )
                    .with_for_update()
                )
                if step is None:
                    raise PlanStepNotFoundError(
                        f"plan step '{call.step_id}' not found"
                    )
                completed = self._tool_call_record(call).with_completed(
                    output_summary=output_summary,
                    evidence_ids=evidence_ids,
                )
                completed_step = self._plan_step_record(step).with_terminal(
                    "completed",
                    success_evaluation=success_evaluation,
                )
                call.state = completed.state
                call.output_summary = completed.output_summary
                call.evidence_ids = list(completed.evidence_ids)
                call.error_type = completed.error_type
                call.finished_at = completed.finished_at
                call.version = completed.version
                self._apply_plan_step(step, completed_step)
                session.flush()
                return completed
        except (ToolCallNotFoundError, PlanStepNotFoundError):
            raise
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "execution repository tool completion failed"
            ) from exc

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
        try:
            with self._sessions.begin() as session:
                now = self._database_now(session)
                run = self._locked_run(session, checkpoint.run_id)
                self._fence_write_row(run, lease, now)
                call = session.scalar(
                    select(ToolCallRow)
                    .where(ToolCallRow.tool_call_id == tool_call_id)
                    .with_for_update()
                )
                if call is None:
                    raise ToolCallNotFoundError(
                        f"tool call '{tool_call_id}' not found"
                    )
                step = session.scalar(
                    select(PlanStepRow)
                    .where(
                        PlanStepRow.run_id == call.run_id,
                        PlanStepRow.step_id == call.step_id,
                    )
                    .with_for_update()
                )
                if step is None:
                    raise PlanStepNotFoundError(
                        f"plan step '{call.step_id}' not found"
                    )
                if (
                    checkpoint.run_id != call.run_id
                    or checkpoint.step_id != call.step_id
                ):
                    raise ExecutionRecordConflictError(
                        "checkpoint identity does not match the ToolCall"
                    )
                existing = session.get(
                    CheckpointRow,
                    (checkpoint.run_id, checkpoint.sequence),
                )
                if call.state == "completed":
                    if (
                        existing is None
                        or self._checkpoint_record(existing) != checkpoint
                        or step.checkpoint_sequence != checkpoint.sequence
                        or dict(call.output_summary) != output_summary
                        or tuple(call.evidence_ids) != evidence_ids
                        or step.success_evaluation != success_evaluation
                    ):
                        raise ExecutionRecordConflictError(
                            "completed ToolCall has conflicting checkpoint content"
                        )
                    return self._tool_call_record(call)

                expected = run.checkpoint_sequence + 1
                if checkpoint.sequence != expected:
                    raise CheckpointSequenceError(
                        f"checkpoint sequence expected {expected}, "
                        f"got {checkpoint.sequence}"
                    )
                if existing is not None:
                    raise ExecutionRecordConflictError(
                        "checkpoint sequence already exists"
                    )

                completed = self._tool_call_record(call).with_completed(
                    output_summary=output_summary,
                    evidence_ids=evidence_ids,
                )
                completed_step = self._plan_step_record(
                    step
                ).with_checkpoint_completed(
                    checkpoint.sequence,
                    success_evaluation=success_evaluation,
                )
                advanced_run = self._record(run).with_checkpoint_sequence(
                    checkpoint.sequence
                )
                session.add(
                    CheckpointRow(
                        run_id=checkpoint.run_id,
                        sequence=checkpoint.sequence,
                        step_id=checkpoint.step_id,
                        schema_version=checkpoint.schema_version,
                        relative_path=str(checkpoint.relative_path),
                        sha256=checkpoint.sha256,
                        byte_size=checkpoint.byte_size,
                        created_at=checkpoint.created_at,
                    )
                )
                call.state = completed.state
                call.output_summary = completed.output_summary
                call.evidence_ids = list(completed.evidence_ids)
                call.error_type = completed.error_type
                call.finished_at = completed.finished_at
                call.version = completed.version
                self._apply_plan_step(step, completed_step)
                run.checkpoint_sequence = advanced_run.checkpoint_sequence
                run.updated_at = advanced_run.updated_at
                run.version = advanced_run.version
                session.flush()
                return completed
        except (
            CheckpointSequenceError,
            ExecutionRecordConflictError,
            PlanStepNotFoundError,
            RunNotFoundError,
            ToolCallNotFoundError,
            ValueError,
        ):
            raise
        except IntegrityError as exc:
            raise ExecutionRecordConflictError(
                "checkpoint metadata already exists with conflicting content"
            ) from exc
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "execution repository checkpoint completion failed"
            ) from exc

    def fail_tool_call(
        self,
        tool_call_id: str,
        *,
        error_type: str,
        success_evaluation: dict | None = None,
        lease: RunLease | None = None,
    ) -> ToolCallRecord:
        try:
            with self._sessions.begin() as session:
                now = self._database_now(session)
                call, run = self._locked_call_and_run(session, tool_call_id)
                self._fence_write_row(run, lease, now)
                step = session.scalar(
                    select(PlanStepRow)
                    .where(
                        PlanStepRow.run_id == call.run_id,
                        PlanStepRow.step_id == call.step_id,
                    )
                    .with_for_update()
                )
                if step is None:
                    raise PlanStepNotFoundError(
                        f"plan step '{call.step_id}' not found"
                    )
                failed = self._tool_call_record(call).with_failed(error_type)
                failed_step = self._plan_step_record(step).with_terminal(
                    "failed",
                    error_type=error_type,
                    success_evaluation=success_evaluation,
                )
                call.state = failed.state
                call.error_type = failed.error_type
                call.finished_at = failed.finished_at
                call.version = failed.version
                self._apply_plan_step(step, failed_step)
                session.flush()
                return failed
        except (ToolCallNotFoundError, PlanStepNotFoundError):
            raise
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "execution repository tool failure update failed"
            ) from exc

    def list_tool_calls(self, run_id: str) -> list[ToolCallRecord]:
        statement = (
            select(ToolCallRow)
            .where(ToolCallRow.run_id == run_id)
            .order_by(ToolCallRow.started_at, ToolCallRow.tool_call_id)
        )
        try:
            with self._sessions() as session:
                return [self._tool_call_record(row) for row in session.scalars(statement)]
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "execution repository tool list failed"
            ) from exc

    def record_artifact(
        self,
        record: ArtifactMetadataRecord,
        *,
        lease: RunLease | None = None,
    ) -> None:
        try:
            with self._sessions.begin() as session:
                now = self._database_now(session)
                run = self._locked_run(session, record.run_id)
                self._fence_write_row(run, lease, now)
                existing = session.scalar(
                    select(ArtifactMetadataRow).where(
                        ArtifactMetadataRow.run_id == record.run_id,
                        ArtifactMetadataRow.name == record.name,
                        ArtifactMetadataRow.version == record.version,
                    )
                )
                if existing is not None:
                    if self._artifact_record(existing) != record:
                        raise ExecutionRecordConflictError(
                            f"artifact '{record.name}' version {record.version} conflicts"
                        )
                    return
                session.add(
                    ArtifactMetadataRow(
                        run_id=record.run_id,
                        name=record.name,
                        version=record.version,
                        relative_path=str(record.relative_path),
                        sha256=record.sha256,
                        byte_size=record.byte_size,
                        media_type=record.media_type,
                        state=record.state,
                        created_at=record.created_at,
                    )
                )
        except (RunNotFoundError, ExecutionRecordConflictError):
            raise
        except IntegrityError as exc:
            raise ExecutionRecordConflictError(
                "artifact metadata already exists with conflicting content"
            ) from exc
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "execution repository artifact create failed"
            ) from exc

    def list_artifacts(self, run_id: str) -> list[ArtifactMetadataRecord]:
        statement = (
            select(ArtifactMetadataRow)
            .where(ArtifactMetadataRow.run_id == run_id)
            .order_by(ArtifactMetadataRow.name, ArtifactMetadataRow.version)
        )
        try:
            with self._sessions() as session:
                return [self._artifact_record(row) for row in session.scalars(statement)]
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "execution repository artifact list failed"
            ) from exc

    def list_checkpoints(self, run_id: str) -> list[CheckpointMetadataRecord]:
        statement = (
            select(CheckpointRow)
            .where(CheckpointRow.run_id == run_id)
            .order_by(CheckpointRow.sequence)
        )
        try:
            with self._sessions() as session:
                return [
                    self._checkpoint_record(row)
                    for row in session.scalars(statement)
                ]
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "execution repository checkpoint list failed"
            ) from exc

    def get_latest_checkpoint(
        self,
        run_id: str,
    ) -> CheckpointMetadataRecord | None:
        statement = (
            select(CheckpointRow)
            .where(CheckpointRow.run_id == run_id)
            .order_by(desc(CheckpointRow.sequence))
            .limit(1)
        )
        try:
            with self._sessions() as session:
                row = session.scalar(statement)
                return None if row is None else self._checkpoint_record(row)
        except SQLAlchemyError as exc:
            raise RepositoryUnavailableError(
                "execution repository latest checkpoint failed"
            ) from exc

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
