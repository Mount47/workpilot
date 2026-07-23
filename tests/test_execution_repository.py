"""Contract tests for the durable PlanStep/ToolCall/Artifact ledger."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from workpilot.persistence import (
    ArtifactMetadataRecord,
    CheckpointMetadataRecord,
    CheckpointSequenceError,
    ExecutionRecordConflictError,
    InMemoryRunRepository,
    LeaseLostError,
    PlanStepRecord,
    PlanStepNotFoundError,
    RunRecord,
    ToolCallRecord,
)


def _repository() -> InMemoryRunRepository:
    repository = InMemoryRunRepository()
    repository.create(
        RunRecord(
            run_id="run_ledger",
            goal="周报",
            workspace="basic_project",
            provider="stub",
            output_dir=Path("runs/run_ledger"),
        )
    )
    return repository


def _step(step_id: str = "scan_workspace") -> PlanStepRecord:
    return PlanStepRecord(
        run_id="run_ledger",
        plan_id="plan_ledger",
        step_id=step_id,
        objective="Scan the workspace",
        tool="workspace.scan",
        tool_version="1.0",
        dependencies=(),
        expected_output="Workspace files",
        success_rule_ids=("workspace.non_empty",),
        input_hash="input-hash",
    )


def _call(call_id: str = "call_1") -> ToolCallRecord:
    return ToolCallRecord(
        tool_call_id=call_id,
        run_id="run_ledger",
        step_id="scan_workspace",
        execution_no=1,
        idempotency_key_hash="call-key-hash",
        tool="workspace.scan",
        tool_version="1.0",
        input_hash="input-hash",
    )


def _checkpoint(sequence: int = 1) -> CheckpointMetadataRecord:
    return CheckpointMetadataRecord(
        run_id="run_ledger",
        sequence=sequence,
        step_id="scan_workspace",
        schema_version=1,
        relative_path=Path(f"checkpoints/checkpoint-{sequence:06d}.json"),
        sha256="c" * 64,
        byte_size=128,
    )


def test_plan_batch_is_idempotent_but_conflicting_definition_is_rejected() -> None:
    repository = _repository()
    step = _step()

    repository.create_plan_steps("run_ledger", [step])
    repository.create_plan_steps("run_ledger", [step])

    assert repository.list_plan_steps("run_ledger") == [step]
    with pytest.raises(ExecutionRecordConflictError):
        repository.create_plan_steps(
            "run_ledger",
            [replace(step, objective="Changed objective")],
        )


def test_tool_call_start_and_completion_update_step_atomically() -> None:
    repository = _repository()
    repository.create_plan_steps("run_ledger", [_step()])

    started = repository.start_tool_call(_call())
    running_step = repository.list_plan_steps("run_ledger")[0]
    assert started.state == "running"
    assert running_step.status == "running"
    assert running_step.attempts == 1

    completed = repository.complete_tool_call(
        "call_1",
        output_summary={"file_count": 2},
        evidence_ids=(),
        success_evaluation={"passed": True},
    )
    completed_step = repository.list_plan_steps("run_ledger")[0]
    assert completed.state == "completed"
    assert completed.output_summary == {"file_count": 2}
    assert completed_step.status == "completed"
    assert completed_step.success_evaluation == {"passed": True}


def test_checkpoint_completion_advances_all_records_atomically() -> None:
    repository = _repository()
    repository.create_plan_steps("run_ledger", [_step()])
    repository.start_tool_call(_call())
    checkpoint = _checkpoint()

    completed = repository.complete_tool_call_with_checkpoint(
        "call_1",
        checkpoint=checkpoint,
        output_summary={"file_count": 2},
        evidence_ids=(),
        success_evaluation={"passed": True},
    )

    assert completed.state == "completed"
    assert repository.get("run_ledger").checkpoint_sequence == 1
    step = repository.list_plan_steps("run_ledger")[0]
    assert step.status == "completed"
    assert step.checkpoint_sequence == 1
    assert repository.list_checkpoints("run_ledger") == [checkpoint]
    assert repository.get_latest_checkpoint("run_ledger") == checkpoint

    repeated = repository.complete_tool_call_with_checkpoint(
        "call_1",
        checkpoint=checkpoint,
        output_summary={"file_count": 2},
        evidence_ids=(),
        success_evaluation={"passed": True},
    )
    assert repeated == completed
    assert repository.get("run_ledger").checkpoint_sequence == 1


def test_checkpoint_sequence_gap_rolls_back_every_record() -> None:
    repository = _repository()
    repository.create_plan_steps("run_ledger", [_step()])
    repository.start_tool_call(_call())

    with pytest.raises(CheckpointSequenceError, match="expected 1"):
        repository.complete_tool_call_with_checkpoint(
            "call_1",
            checkpoint=_checkpoint(2),
            output_summary={"file_count": 2},
            evidence_ids=(),
        )

    assert repository.get("run_ledger").checkpoint_sequence == 0
    assert repository.list_tool_calls("run_ledger")[0].state == "running"
    assert repository.list_plan_steps("run_ledger")[0].status == "running"
    assert repository.list_checkpoints("run_ledger") == []


def test_tool_call_failure_updates_call_and_step() -> None:
    repository = _repository()
    repository.create_plan_steps("run_ledger", [_step()])
    repository.start_tool_call(_call())

    failed = repository.fail_tool_call("call_1", error_type="ProviderTimeout")

    assert failed.state == "failed"
    assert failed.error_type == "ProviderTimeout"
    step = repository.list_plan_steps("run_ledger")[0]
    assert step.status == "failed"
    assert step.last_error_type == "ProviderTimeout"


def test_duplicate_logical_tool_call_is_rejected() -> None:
    repository = _repository()
    repository.create_plan_steps("run_ledger", [_step()])
    repository.start_tool_call(_call("call_first"))

    with pytest.raises(ExecutionRecordConflictError):
        repository.start_tool_call(_call("call_second"))


def test_start_tool_call_requires_existing_step() -> None:
    repository = _repository()

    with pytest.raises(PlanStepNotFoundError):
        repository.start_tool_call(_call())


def test_scheduler_terminal_state_is_persisted_without_tool_call() -> None:
    repository = _repository()
    repository.create_plan_steps("run_ledger", [_step()])

    updated = repository.update_plan_step_status(
        "run_ledger",
        "scan_workspace",
        status="blocked",
        error_type="dependency_terminal",
    )

    assert updated.status == "blocked"
    assert updated.last_error_type == "dependency_terminal"
    assert updated.attempts == 0


def test_artifact_metadata_is_versioned_and_path_safe() -> None:
    repository = _repository()
    metadata = ArtifactMetadataRecord(
        run_id="run_ledger",
        name="trace",
        version=1,
        relative_path=Path("trace.json"),
        sha256="a" * 64,
        byte_size=42,
        media_type="application/json",
    )

    repository.record_artifact(metadata)
    repository.record_artifact(metadata)
    assert repository.list_artifacts("run_ledger") == [metadata]

    with pytest.raises(ValueError, match="relative_path"):
        ArtifactMetadataRecord(
            run_id="run_ledger",
            name="escape",
            version=1,
            relative_path=Path("../escape.json"),
            sha256="b" * 64,
            byte_size=1,
            media_type="application/json",
        )


def test_active_lease_fences_every_execution_write() -> None:
    now = datetime(2026, 7, 22, 8, 0, tzinfo=timezone.utc)
    clock = lambda: now
    repository = InMemoryRunRepository(clock=clock)
    repository.create(
        RunRecord(
            run_id="run_ledger",
            goal="report",
            workspace="basic_project",
            provider="stub",
            output_dir=Path("runs/run_ledger"),
        )
    )
    repository.create_plan_steps("run_ledger", [_step()])
    repository.start_tool_call(_call())
    lease = repository.claim_run("run_ledger", "worker-a", ttl_seconds=30)
    artifact = ArtifactMetadataRecord(
        run_id="run_ledger",
        name="trace",
        version=1,
        relative_path=Path("trace.json"),
        sha256="a" * 64,
        byte_size=42,
        media_type="application/json",
    )

    writes = (
        lambda: repository.update_state("run_ledger", "recovering"),
        lambda: repository.create_plan_steps("run_ledger", [_step("extra")]),
        lambda: repository.update_plan_step_status(
            "run_ledger", "scan_workspace", status="blocked"
        ),
        lambda: repository.start_tool_call(_call("call_2")),
        lambda: repository.complete_tool_call(
            "call_1", output_summary={}, evidence_ids=()
        ),
        lambda: repository.complete_tool_call_with_checkpoint(
            "call_1",
            checkpoint=_checkpoint(),
            output_summary={},
            evidence_ids=(),
        ),
        lambda: repository.fail_tool_call("call_1", error_type="timeout"),
        lambda: repository.record_artifact(artifact),
    )
    for write in writes:
        with pytest.raises(LeaseLostError):
            write()

    now += timedelta(seconds=31)
    takeover = repository.claim_run("run_ledger", "worker-b", ttl_seconds=30)
    with pytest.raises(LeaseLostError):
        repository.update_plan_step_status(
            "run_ledger",
            "scan_workspace",
            status="blocked",
            lease=lease,
        )
    updated = repository.update_plan_step_status(
        "run_ledger",
        "scan_workspace",
        status="blocked",
        lease=takeover,
    )
    assert updated.status == "blocked"
