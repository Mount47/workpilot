from pathlib import Path

import pytest

from workpilot.persistence import InMemoryRunRepository, RunRecord
from workpilot.persistence.execution_observer import RepositoryExecutionObserver
from workpilot.planning import PlanStepStatus, create_default_registry
from workpilot.providers.registry import get_provider
from workpilot.runtime.checkpoint import CheckpointValidationError
from workpilot.runtime.checkpoint_restore import CheckpointRestorer
from workpilot.runtime.checkpoint_store import CheckpointStore
from workpilot.runtime.runner import Runtime


class CrashAfterCommitRepository(InMemoryRunRepository):
    """Simulate losing the DB response after a transaction has committed."""

    def __init__(self, crash_sequence: int) -> None:
        super().__init__()
        self.crash_sequence = crash_sequence

    def complete_tool_call_with_checkpoint(self, tool_call_id, **kwargs):
        completed = super().complete_tool_call_with_checkpoint(tool_call_id, **kwargs)
        if kwargs["checkpoint"].sequence == self.crash_sequence:
            raise RuntimeError("injected crash after database commit")
        return completed


@pytest.mark.parametrize("crash_sequence", range(1, 7))
def test_crash_after_each_step_restores_last_database_commit(
    crash_sequence: int,
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    run_id = f"run_crash_{crash_sequence}"
    output_dir = tmp_path / run_id
    repository = CrashAfterCommitRepository(crash_sequence)
    repository.create(
        RunRecord(
            run_id=run_id,
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
        run_id=run_id,
        execution_observer=RepositoryExecutionObserver(
            repository,
            run_id=run_id,
            output_dir=output_dir,
        ),
    )

    result = runtime.execute()

    assert result.state.value == "failed"
    assert repository.get(run_id).checkpoint_sequence == crash_sequence
    assert len(repository.list_checkpoints(run_id)) == crash_sequence
    restored = CheckpointRestorer(
        runtime.contract,
        create_default_registry(),
    ).restore_latest(repository, CheckpointStore(output_dir), run_id)
    assert restored.payload.sequence == crash_sequence
    assert restored.payload.committed_step_id == runtime.plan.steps[
        crash_sequence - 1
    ].step_id
    assert sum(
        step.status == PlanStepStatus.COMPLETED for step in restored.plan.steps
    ) == crash_sequence
    calls = repository.list_tool_calls(run_id)
    assert calls[-1].state == "completed"


def test_corrupt_database_committed_checkpoint_fails_closed(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    run_id = "run_corrupt_committed"
    output_dir = tmp_path / run_id
    repository = CrashAfterCommitRepository(1)
    repository.create(
        RunRecord(
            run_id=run_id,
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
        run_id=run_id,
        execution_observer=RepositoryExecutionObserver(
            repository,
            run_id=run_id,
            output_dir=output_dir,
        ),
    )
    assert runtime.execute().state.value == "failed"
    metadata = repository.get_latest_checkpoint(run_id)
    assert metadata is not None
    path = output_dir / metadata.relative_path
    content = bytearray(path.read_bytes())
    content[-2] = ord(" ") if content[-2] != ord(" ") else ord("x")
    path.write_bytes(content)

    with pytest.raises(CheckpointValidationError, match="sha256"):
        CheckpointRestorer(
            runtime.contract,
            create_default_registry(),
        ).restore_latest(repository, CheckpointStore(output_dir), run_id)

