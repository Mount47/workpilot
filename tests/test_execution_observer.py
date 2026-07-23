"""Tests for Runtime-to-Repository execution ledger integration."""

from pathlib import Path
from hashlib import sha256

import pytest

from workpilot.persistence import (
    InMemoryRunRepository,
    RepositoryUnavailableError,
    RunRecord,
)
from workpilot.persistence.execution_observer import RepositoryExecutionObserver
from workpilot.planning import PlanStep
from workpilot.providers.registry import get_provider
from workpilot.runtime.observer import ToolCallContext, build_tool_call_context
from workpilot.runtime.checkpoint_store import CheckpointFileRecord, CheckpointStore
from workpilot.runtime.runner import Runtime


class RecordingObserver:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def plan_registered(self, plan, tool_versions) -> None:
        self.events.append(("plan_registered", plan.plan_id))

    def tool_call_started(self, context: ToolCallContext) -> None:
        self.events.append(("tool_call_started", context))

    def tool_call_completed(
        self,
        context: ToolCallContext,
        *,
        output_summary: dict,
        evidence_ids: tuple[str, ...],
        success_evaluation: dict | None,
        checkpoint: CheckpointFileRecord | None,
    ) -> None:
        self.events.append(("tool_call_completed", context))

    def next_checkpoint_sequence(self) -> int | None:
        return None

    def tool_call_failed(
        self,
        context: ToolCallContext,
        *,
        error_type: str,
        success_evaluation: dict | None,
    ) -> None:
        self.events.append(("tool_call_failed", context))

    def scheduler_event(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))

    def artifact_recorded(self, path: Path) -> None:
        self.events.append(("artifact_recorded", path.name))


class VerifyingRepository(InMemoryRunRepository):
    """Assert the file commit precedes the database completion transaction."""

    def __init__(self, output_dir: Path) -> None:
        super().__init__()
        self.output_dir = output_dir
        self.commit_order: list[int] = []

    def complete_tool_call_with_checkpoint(self, tool_call_id, **kwargs):
        checkpoint = kwargs["checkpoint"]
        path = self.output_dir / checkpoint.relative_path
        content = path.read_bytes()
        assert len(content) == checkpoint.byte_size
        assert sha256(content).hexdigest() == checkpoint.sha256
        self.commit_order.append(checkpoint.sequence)
        return super().complete_tool_call_with_checkpoint(tool_call_id, **kwargs)


class FailingCheckpointRepository(InMemoryRunRepository):
    def complete_tool_call_with_checkpoint(self, tool_call_id, **kwargs):
        raise RepositoryUnavailableError("injected checkpoint commit failure")


def test_tool_call_context_is_canonical_and_deterministic() -> None:
    first = PlanStep(
        step_id="scan",
        objective="Scan",
        tool="workspace.scan",
        inputs={"b": 2, "a": 1},
        expected_output="Files",
        success_criteria=["Non-empty"],
    )
    reordered = first.model_copy(update={"inputs": {"a": 1, "b": 2}})

    context = build_tool_call_context(
        run_id="run_observed",
        plan_id="plan_observed",
        step=first,
        execution_no=1,
        tool_version="1.0",
    )
    same = build_tool_call_context(
        run_id="run_observed",
        plan_id="plan_observed",
        step=reordered,
        execution_no=1,
        tool_version="1.0",
    )

    assert context == same
    assert context.tool_call_id.startswith("tc_")
    assert len(context.idempotency_key_hash) == 64
    assert len(context.input_hash) == 64


def test_runtime_emits_safe_execution_lifecycle(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    observer = RecordingObserver()
    runtime = Runtime(
        workspace_root=basic_workspace,
        goal="生成周报",
        output_dir=tmp_path / "observed",
        provider=get_provider("stub"),
        run_id="run_observed",
        execution_observer=observer,
    )

    result = runtime.execute()

    names = [name for name, _ in observer.events]
    assert result.state.value == "passed"
    assert names[0] == "plan_registered"
    assert names.count("tool_call_started") == 6
    assert names.count("tool_call_completed") == 6
    assert "tool_call_failed" not in names
    contexts = [
        value for name, value in observer.events if name == "tool_call_started"
    ]
    assert all(isinstance(context, ToolCallContext) for context in contexts)
    assert {context.execution_no for context in contexts} == {1}


def test_repository_observer_persists_runtime_ledger(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    repository = VerifyingRepository(tmp_path / "observed")
    repository.create(
        RunRecord(
            run_id="run_observed",
            goal="生成周报",
            workspace="basic_project",
            provider="stub",
            output_dir=tmp_path / "observed",
        )
    )
    observer = RepositoryExecutionObserver(
        repository,
        run_id="run_observed",
        output_dir=tmp_path / "observed",
    )
    runtime = Runtime(
        workspace_root=basic_workspace,
        goal="生成周报",
        output_dir=tmp_path / "observed",
        provider=get_provider("stub"),
        run_id="run_observed",
        execution_observer=observer,
    )

    result = runtime.execute()

    steps = repository.list_plan_steps("run_observed")
    calls = repository.list_tool_calls("run_observed")
    assert result.state.value == "passed"
    assert len(steps) == 6
    assert len(calls) == 6
    assert {step.status for step in steps} == {"completed"}
    assert {call.state for call in calls} == {"completed"}
    assert all("output" not in call.output_summary for call in calls)
    assert repository.commit_order == [1, 2, 3, 4, 5, 6]
    assert repository.get("run_observed").checkpoint_sequence == 6
    assert [
        checkpoint.sequence
        for checkpoint in repository.list_checkpoints("run_observed")
    ] == [1, 2, 3, 4, 5, 6]
    assert all(step.checkpoint_sequence is not None for step in steps)
    assert len(list((tmp_path / "observed" / "checkpoints").glob("*.json"))) == 6


def test_checkpoint_file_failure_does_not_commit_step(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "file-failure"
    repository = InMemoryRunRepository()
    repository.create(
        RunRecord(
            run_id="run_file_failure",
            goal="report",
            workspace="basic_project",
            provider="stub",
            output_dir=output_dir,
        )
    )
    observer = RepositoryExecutionObserver(
        repository,
        run_id="run_file_failure",
        output_dir=output_dir,
    )

    def fail_before_replace(_: Path, __: Path) -> None:
        raise RuntimeError("injected checkpoint file failure")

    runtime = Runtime(
        workspace_root=basic_workspace,
        goal="report",
        output_dir=output_dir,
        provider=get_provider("stub"),
        run_id="run_file_failure",
        execution_observer=observer,
        checkpoint_store=CheckpointStore(
            output_dir,
            before_replace=fail_before_replace,
        ),
    )

    result = runtime.execute()

    assert result.state.value == "failed"
    assert repository.list_checkpoints("run_file_failure") == []
    assert repository.list_tool_calls("run_file_failure")[0].state == "running"
    steps = {
        step.step_id: step
        for step in repository.list_plan_steps("run_file_failure")
    }
    assert steps["scan_workspace"].status == "running"


def test_database_failure_leaves_only_an_uncommitted_orphan_file(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "database-failure"
    repository = FailingCheckpointRepository()
    repository.create(
        RunRecord(
            run_id="run_database_failure",
            goal="report",
            workspace="basic_project",
            provider="stub",
            output_dir=output_dir,
        )
    )
    observer = RepositoryExecutionObserver(
        repository,
        run_id="run_database_failure",
        output_dir=output_dir,
    )
    runtime = Runtime(
        workspace_root=basic_workspace,
        goal="report",
        output_dir=output_dir,
        provider=get_provider("stub"),
        run_id="run_database_failure",
        execution_observer=observer,
    )

    result = runtime.execute()

    assert result.state.value == "failed"
    assert repository.list_checkpoints("run_database_failure") == []
    assert repository.list_tool_calls("run_database_failure")[0].state == "running"
    steps = {
        step.step_id: step
        for step in repository.list_plan_steps("run_database_failure")
    }
    assert steps["scan_workspace"].status == "running"
    assert (output_dir / "checkpoints" / "checkpoint-000001.json").is_file()
