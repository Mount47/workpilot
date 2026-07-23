from pathlib import Path

import pytest

from workpilot.persistence import InMemoryRunRepository, RunRecord
from workpilot.persistence.execution_observer import RepositoryExecutionObserver
from workpilot.planning import PlanStepStatus, create_default_registry
from workpilot.providers.registry import get_provider
from workpilot.runtime.checkpoint import CheckpointValidationError
from workpilot.runtime.checkpoint_restore import CheckpointRestorer
from workpilot.runtime.checkpoint_store import CheckpointFileRecord, CheckpointStore
from workpilot.runtime.runner import Runtime


def _completed_runtime(
    basic_workspace: Path,
    tmp_path: Path,
) -> tuple[Runtime, InMemoryRunRepository, CheckpointStore]:
    output_dir = tmp_path / "restorable"
    repository = InMemoryRunRepository()
    repository.create(
        RunRecord(
            run_id="run_restorable",
            goal="generate report",
            workspace="basic_project",
            provider="stub",
            output_dir=output_dir,
        )
    )
    runtime = Runtime(
        workspace_root=basic_workspace,
        goal="generate report",
        output_dir=output_dir,
        provider=get_provider("stub"),
        run_id="run_restorable",
        execution_observer=RepositoryExecutionObserver(
            repository,
            run_id="run_restorable",
            output_dir=output_dir,
        ),
    )
    assert runtime.execute().state.value == "passed"
    return runtime, repository, CheckpointStore(output_dir)


def _file_record(metadata) -> CheckpointFileRecord:
    return CheckpointFileRecord(
        run_id=metadata.run_id,
        sequence=metadata.sequence,
        schema_version=metadata.schema_version,
        relative_path=metadata.relative_path,
        sha256=metadata.sha256,
        byte_size=metadata.byte_size,
    )


def test_every_builtin_step_checkpoint_reconstructs_required_state(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    runtime, repository, store = _completed_runtime(basic_workspace, tmp_path)
    restorer = CheckpointRestorer(
        contract=runtime.contract,
        registry=create_default_registry(),
    )

    for sequence, metadata in enumerate(
        repository.list_checkpoints("run_restorable"),
        start=1,
    ):
        state = restorer.restore(store.load(_file_record(metadata)))
        statuses = [step.status for step in state.plan.steps]
        assert statuses.count(PlanStepStatus.COMPLETED) == sequence
        assert len(state.result_store.checkpoint_items()) == sequence
        assert state.budget.step_count == state.payload.runtime_state.budget.steps_used
        assert state.budget.input_tokens == state.payload.runtime_state.budget.input_tokens
        assert state.evidence_store.run_id == "run_restorable"
        assert state.working_memory.plan is state.plan

        if sequence == 1:
            assert state.evidence_store.count() == 0
        if sequence >= 2:
            assert state.evidence_store.count() > 0
        if sequence >= 3:
            assert state.project_snapshot is not None
            assert state.result_store.get("build_claims").output == state.project_snapshot
        if sequence >= 4:
            assert "weekly_report.md" in state.rendered_artifacts
        if sequence >= 5:
            assert state.verification_results
            assert state.result_store.get("verify").output[0]
        if sequence == 6:
            assert state.result_store.get("finalize").output is None


def test_restore_latest_uses_only_repository_committed_metadata(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    runtime, repository, store = _completed_runtime(basic_workspace, tmp_path)
    restorer = CheckpointRestorer(runtime.contract, create_default_registry())

    state = restorer.restore_latest(repository, store, "run_restorable")

    assert state.payload.sequence == 6
    assert all(step.status == PlanStepStatus.COMPLETED for step in state.plan.steps)


def test_restore_resets_uncommitted_running_step_to_pending(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    runtime, repository, store = _completed_runtime(basic_workspace, tmp_path)
    first = repository.list_checkpoints("run_restorable")[0]
    payload = store.load(_file_record(first))
    plan = payload.plan.model_copy(deep=True)
    pending = plan.get_step("extract_evidence")
    pending.status = PlanStepStatus.RUNNING
    pending.attempts = 1
    changed = payload.model_copy(update={"plan": plan})

    state = CheckpointRestorer(
        runtime.contract,
        create_default_registry(),
    ).restore(changed)

    restored = state.plan.get_step("extract_evidence")
    assert restored.status == PlanStepStatus.PENDING
    assert restored.attempts == 1
    assert restored.last_error_type is None


def test_restore_rejects_incompatible_tool_version(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    runtime, repository, store = _completed_runtime(basic_workspace, tmp_path)
    first = repository.list_checkpoints("run_restorable")[0]
    payload = store.load(_file_record(first))
    result = payload.step_results["scan_workspace"].model_copy(
        update={"tool_version": "9.9"}
    )
    changed = payload.model_copy(
        update={"step_results": {"scan_workspace": result}}
    )

    with pytest.raises(CheckpointValidationError, match="tool version"):
        CheckpointRestorer(
            runtime.contract,
            create_default_registry(),
        ).restore(changed)


def test_restore_rejects_semantically_inconsistent_runtime_state(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    runtime, repository, store = _completed_runtime(basic_workspace, tmp_path)
    fourth = repository.list_checkpoints("run_restorable")[3]
    payload = store.load(_file_record(fourth))
    runtime_state = payload.runtime_state.model_copy(
        update={"rendered_artifacts": {}}
    )
    changed = payload.model_copy(update={"runtime_state": runtime_state})

    with pytest.raises(CheckpointValidationError, match="rendered artifacts"):
        CheckpointRestorer(
            runtime.contract,
            create_default_registry(),
        ).restore(changed)
