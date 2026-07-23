from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from workpilot.planning import Plan, PlanStep
from workpilot.runtime.checkpoint import (
    BudgetCheckpoint,
    CheckpointPayload,
    CheckpointValidationError,
    RuntimeStateCheckpoint,
    canonical_json_bytes,
)
from workpilot.runtime.checkpoint_store import CheckpointFileRecord, CheckpointStore


def _payload(*, run_id: str = "run-1", sequence: int = 1) -> CheckpointPayload:
    plan = Plan(
        plan_id=f"plan_{run_id}",
        goal="summarize",
        created_by="test",
        validated=True,
        created_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        steps=[
            PlanStep(
                step_id="scan_workspace",
                objective="scan",
                tool="workspace.scan",
                expected_output="files",
                success_criteria=["done"],
            )
        ],
    )
    return CheckpointPayload(
        run_id=run_id,
        sequence=sequence,
        committed_step_id="scan_workspace",
        plan=plan,
        runtime_state=RuntimeStateCheckpoint(
            budget=BudgetCheckpoint(
                steps_used=1,
                steps_limit=30,
                input_tokens=0,
                output_tokens=0,
                token_limit=1000,
                elapsed_seconds=0.5,
                time_limit_seconds=300,
            )
        ),
    )


def test_write_is_atomic_and_returns_verified_metadata(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path)
    payload = _payload()

    record = store.write(payload)

    expected_path = tmp_path / "checkpoints" / "checkpoint-000001.json"
    expected_bytes = canonical_json_bytes(payload)
    assert record == CheckpointFileRecord(
        run_id="run-1",
        sequence=1,
        schema_version=1,
        relative_path=Path("checkpoints/checkpoint-000001.json"),
        sha256=sha256(expected_bytes).hexdigest(),
        byte_size=len(expected_bytes),
    )
    assert expected_path.read_bytes() == expected_bytes
    assert list(expected_path.parent.glob("*.tmp")) == []
    assert store.load(record) == payload


def test_load_rejects_missing_truncated_and_same_size_tampered_files(
    tmp_path: Path,
) -> None:
    store = CheckpointStore(tmp_path)
    record = store.write(_payload())
    path = tmp_path / record.relative_path
    original = path.read_bytes()

    path.unlink()
    with pytest.raises(CheckpointValidationError, match="missing"):
        store.load(record)

    path.write_bytes(original[:-1])
    with pytest.raises(CheckpointValidationError, match="size"):
        store.load(record)

    tampered = bytearray(original)
    tampered[-2] = ord(" ") if tampered[-2] != ord(" ") else ord("x")
    path.write_bytes(tampered)
    with pytest.raises(CheckpointValidationError, match="sha256"):
        store.load(record)


def test_load_rejects_run_sequence_and_schema_mismatch(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path)
    record = store.write(_payload())

    with pytest.raises(CheckpointValidationError, match="run_id"):
        store.load(replace(record, run_id="run-2"))
    with pytest.raises(CheckpointValidationError, match="sequence"):
        store.load(replace(record, sequence=2))
    with pytest.raises(CheckpointValidationError, match="schema"):
        store.load(replace(record, schema_version=2))


def test_record_and_store_reject_paths_outside_run_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="relative_path"):
        CheckpointFileRecord(
            run_id="run-1",
            sequence=1,
            schema_version=1,
            relative_path=Path("../escape.json"),
            sha256="0" * 64,
            byte_size=1,
        )

    store = CheckpointStore(tmp_path)
    record = store.write(_payload())
    unsafe = object.__new__(CheckpointFileRecord)
    object.__setattr__(unsafe, "run_id", record.run_id)
    object.__setattr__(unsafe, "sequence", record.sequence)
    object.__setattr__(unsafe, "schema_version", record.schema_version)
    object.__setattr__(unsafe, "relative_path", Path("../escape.json"))
    object.__setattr__(unsafe, "sha256", record.sha256)
    object.__setattr__(unsafe, "byte_size", record.byte_size)
    with pytest.raises(CheckpointValidationError, match="outside"):
        store.load(unsafe)


def test_failure_before_replace_leaves_no_final_or_temporary_file(
    tmp_path: Path,
) -> None:
    def fail_before_replace(_: Path, __: Path) -> None:
        raise RuntimeError("injected crash")

    store = CheckpointStore(tmp_path, before_replace=fail_before_replace)

    with pytest.raises(RuntimeError, match="injected crash"):
        store.write(_payload())

    checkpoint_dir = tmp_path / "checkpoints"
    assert not (checkpoint_dir / "checkpoint-000001.json").exists()
    assert list(checkpoint_dir.iterdir()) == []

