"""Atomic, digest-verified filesystem storage for Runtime checkpoints."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import tempfile

from workpilot.runtime.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointPayload,
    CheckpointValidationError,
    canonical_json_bytes,
    decode_checkpoint,
)


BeforeReplaceHook = Callable[[Path, Path], None]


@dataclass(frozen=True, slots=True)
class CheckpointFileRecord:
    """Content-free metadata returned only after file verification succeeds."""

    run_id: str
    sequence: int
    schema_version: int
    relative_path: Path
    sha256: str
    byte_size: int

    def __post_init__(self) -> None:
        relative_path = Path(self.relative_path)
        if (
            relative_path.is_absolute()
            or relative_path == Path(".")
            or ".." in relative_path.parts
        ):
            raise ValueError("relative_path must stay within the Run output directory")
        if not self.run_id:
            raise ValueError("run_id must not be empty")
        if self.sequence < 1:
            raise ValueError("sequence must be at least 1")
        if self.schema_version < 1:
            raise ValueError("schema_version must be at least 1")
        if self.byte_size < 0:
            raise ValueError("byte_size must not be negative")
        digest = self.sha256.lower()
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError("sha256 must be a 64-character hex digest")
        object.__setattr__(self, "relative_path", relative_path)
        object.__setattr__(self, "sha256", digest)


class CheckpointStore:
    """Write canonical checkpoints under one resolved Run output directory."""

    def __init__(
        self,
        output_dir: Path,
        *,
        before_replace: BeforeReplaceHook | None = None,
    ) -> None:
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.before_replace = before_replace

    def write(self, payload: CheckpointPayload) -> CheckpointFileRecord:
        """Atomically write, read back and verify one canonical payload."""
        data = canonical_json_bytes(payload)
        relative_path = Path("checkpoints") / f"checkpoint-{payload.sequence:06d}.json"
        final_path = self._resolve(relative_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{final_path.name}.",
            suffix=".tmp",
            dir=final_path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if self.before_replace is not None:
                self.before_replace(temporary_path, final_path)
            os.replace(temporary_path, final_path)
            self._fsync_directory(final_path.parent)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

        record = CheckpointFileRecord(
            run_id=payload.run_id,
            sequence=payload.sequence,
            schema_version=payload.schema_version,
            relative_path=relative_path,
            sha256=sha256(data).hexdigest(),
            byte_size=len(data),
        )
        restored = self.load(record)
        if restored != payload:
            raise CheckpointValidationError("checkpoint read-back changed the payload")
        return record

    def load(self, record: CheckpointFileRecord) -> CheckpointPayload:
        """Verify metadata, content digest and payload identity before returning."""
        if record.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointValidationError("unsupported checkpoint schema version")
        path = self._resolve(record.relative_path)
        if not path.is_file():
            raise CheckpointValidationError("checkpoint file is missing")
        data = path.read_bytes()
        if len(data) != record.byte_size:
            raise CheckpointValidationError("checkpoint byte size does not match metadata")
        if sha256(data).hexdigest() != record.sha256:
            raise CheckpointValidationError("checkpoint sha256 does not match metadata")
        payload = decode_checkpoint(data)
        if payload.schema_version != record.schema_version:
            raise CheckpointValidationError("checkpoint schema does not match metadata")
        if payload.run_id != record.run_id:
            raise CheckpointValidationError("checkpoint run_id does not match metadata")
        if payload.sequence != record.sequence:
            raise CheckpointValidationError("checkpoint sequence does not match metadata")
        return payload

    def _resolve(self, relative_path: Path) -> Path:
        path = Path(relative_path)
        if path.is_absolute() or path == Path(".") or ".." in path.parts:
            raise CheckpointValidationError(
                "checkpoint path resolves outside the Run output directory"
            )
        resolved = (self.output_dir / path).resolve()
        if not resolved.is_relative_to(self.output_dir):
            raise CheckpointValidationError(
                "checkpoint path resolves outside the Run output directory"
            )
        return resolved

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        """Durably record rename where directory handles support fsync."""
        if os.name == "nt":
            return
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

