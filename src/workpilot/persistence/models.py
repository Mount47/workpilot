"""Persistence-facing records shared by API and repository adapters."""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    """Return an aware UTC timestamp for persisted lifecycle events."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Durable identity and lifecycle metadata for one API-triggered run."""

    run_id: str
    goal: str
    workspace: str
    provider: str
    output_dir: Path
    state: str = "pending"
    failure_reason: str | None = None
    idempotency_key_hash: str | None = None
    request_fingerprint: str | None = None
    checkpoint_sequence: int = 0
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    execution_attempt: int = 0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "output_dir", Path(self.output_dir))
        if self.checkpoint_sequence < 0:
            raise ValueError("checkpoint_sequence must not be negative")
        if self.execution_attempt < 0:
            raise ValueError("execution_attempt must not be negative")
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("lease owner and expiry must be set together")
        if self.lease_expires_at is not None and self.lease_expires_at.tzinfo is None:
            raise ValueError("lease_expires_at must be timezone-aware")

    def with_state(
        self,
        state: str,
        failure_reason: str | None = None,
    ) -> "RunRecord":
        """Return the next immutable lifecycle version of this record."""
        return replace(
            self,
            state=state,
            failure_reason=failure_reason,
            updated_at=utc_now(),
            version=self.version + 1,
        )

    def with_idempotency(
        self,
        key_hash: str,
        request_fingerprint: str,
    ) -> "RunRecord":
        """Attach content-free request identity before initial persistence."""
        return replace(
            self,
            idempotency_key_hash=key_hash,
            request_fingerprint=request_fingerprint,
        )

    def with_checkpoint_sequence(self, sequence: int) -> "RunRecord":
        """Advance the last committed checkpoint exactly once."""
        if sequence != self.checkpoint_sequence + 1:
            raise ValueError(
                f"checkpoint sequence expected {self.checkpoint_sequence + 1}, "
                f"got {sequence}"
            )
        return replace(
            self,
            checkpoint_sequence=sequence,
            updated_at=utc_now(),
            version=self.version + 1,
        )


@dataclass(frozen=True, slots=True)
class RunLease:
    """Worker-local capability used to fence all writes for one claim."""

    run_id: str
    owner_token: str
    execution_attempt: int
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.run_id or not self.owner_token:
            raise ValueError("lease run_id and owner_token must not be empty")
        if self.execution_attempt < 1:
            raise ValueError("lease execution_attempt must be at least 1")
        if self.expires_at.tzinfo is None:
            raise ValueError("lease expires_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class CreateRunResult:
    """Result of an atomic create-or-return-existing operation."""

    record: RunRecord
    created: bool


@dataclass(frozen=True, slots=True)
class PlanStepRecord:
    """Safe durable state for one validated PlanStep."""

    run_id: str
    plan_id: str
    step_id: str
    objective: str
    tool: str
    tool_version: str
    dependencies: tuple[str, ...]
    expected_output: str
    success_rule_ids: tuple[str, ...]
    input_hash: str
    status: str = "pending"
    attempts: int = 0
    last_error_type: str | None = None
    success_evaluation: dict[str, Any] | None = None
    checkpoint_sequence: int | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    version: int = 1

    def __post_init__(self) -> None:
        if self.checkpoint_sequence is not None and self.checkpoint_sequence < 1:
            raise ValueError("checkpoint_sequence must be at least 1")

    def with_started(self, execution_no: int) -> "PlanStepRecord":
        if execution_no != self.attempts + 1:
            raise ValueError("execution_no must follow the persisted attempt count")
        return replace(
            self,
            status="running",
            attempts=execution_no,
            last_error_type=None,
            success_evaluation=None,
            updated_at=utc_now(),
            version=self.version + 1,
        )

    def with_terminal(
        self,
        status: str,
        *,
        error_type: str | None = None,
        success_evaluation: dict[str, Any] | None = None,
    ) -> "PlanStepRecord":
        return replace(
            self,
            status=status,
            last_error_type=error_type,
            success_evaluation=success_evaluation,
            updated_at=utc_now(),
            version=self.version + 1,
        )

    def with_checkpoint_completed(
        self,
        sequence: int,
        *,
        success_evaluation: dict[str, Any] | None = None,
    ) -> "PlanStepRecord":
        if sequence < 1:
            raise ValueError("checkpoint sequence must be at least 1")
        return replace(
            self,
            status="completed",
            last_error_type=None,
            success_evaluation=success_evaluation,
            checkpoint_sequence=sequence,
            updated_at=utc_now(),
            version=self.version + 1,
        )


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """Content-minimized durable ledger entry for one logical ToolCall."""

    tool_call_id: str
    run_id: str
    step_id: str
    execution_no: int
    idempotency_key_hash: str
    tool: str
    tool_version: str
    input_hash: str
    state: str = "pending"
    physical_attempts: int = 0
    output_summary: dict[str, Any] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()
    error_type: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    version: int = 1

    def with_started(self) -> "ToolCallRecord":
        return replace(
            self,
            state="running",
            physical_attempts=self.physical_attempts + 1,
            started_at=utc_now(),
            finished_at=None,
            error_type=None,
            version=self.version + 1,
        )

    def with_completed(
        self,
        *,
        output_summary: dict[str, Any],
        evidence_ids: tuple[str, ...],
    ) -> "ToolCallRecord":
        return replace(
            self,
            state="completed",
            output_summary=dict(output_summary),
            evidence_ids=tuple(evidence_ids),
            finished_at=utc_now(),
            error_type=None,
            version=self.version + 1,
        )

    def with_failed(self, error_type: str) -> "ToolCallRecord":
        return replace(
            self,
            state="failed",
            error_type=error_type,
            finished_at=utc_now(),
            version=self.version + 1,
        )


@dataclass(frozen=True, slots=True)
class ArtifactMetadataRecord:
    """Versioned, content-free metadata for one file Artifact."""

    run_id: str
    name: str
    version: int
    relative_path: Path
    sha256: str
    byte_size: int
    media_type: str
    state: str = "committed"
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        path = Path(self.relative_path)
        if path.is_absolute() or ".." in path.parts or path == Path("."):
            raise ValueError("relative_path must stay within the Run output directory")
        if self.version < 1:
            raise ValueError("artifact version must be at least 1")
        if self.byte_size < 0:
            raise ValueError("artifact byte_size must not be negative")
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256.lower()
        ):
            raise ValueError("artifact sha256 must be a 64-character hex digest")
        object.__setattr__(self, "relative_path", path)


@dataclass(frozen=True, slots=True)
class CheckpointMetadataRecord:
    """Content-free metadata for one database-committed checkpoint file."""

    run_id: str
    sequence: int
    step_id: str
    schema_version: int
    relative_path: Path
    sha256: str
    byte_size: int
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        path = Path(self.relative_path)
        if path.is_absolute() or ".." in path.parts or path == Path("."):
            raise ValueError("relative_path must stay within the Run output directory")
        if not self.run_id or not self.step_id:
            raise ValueError("checkpoint run_id and step_id must not be empty")
        if self.sequence < 1:
            raise ValueError("checkpoint sequence must be at least 1")
        if self.schema_version < 1:
            raise ValueError("checkpoint schema_version must be at least 1")
        if self.byte_size < 0:
            raise ValueError("checkpoint byte_size must not be negative")
        digest = self.sha256.lower()
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError("checkpoint sha256 must be a 64-character hex digest")
        object.__setattr__(self, "relative_path", path)
        object.__setattr__(self, "sha256", digest)
