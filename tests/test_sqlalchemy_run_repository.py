"""SQLAlchemy adapter tests independent of a running PostgreSQL service."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from pathlib import Path
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from workpilot.persistence import (
    ArtifactMetadataRecord,
    CheckpointMetadataRecord,
    CheckpointSequenceError,
    IdempotencyConflictError,
    LeaseConflictError,
    LeaseLostError,
    PlanStepRecord,
    RepositoryUnavailableError,
    RunAlreadyExistsError,
    RunNotFoundError,
    RunLease,
    RunRecord,
    ToolCallRecord,
)
from workpilot.persistence.database import Base, RunRow
from workpilot.persistence.models import utc_now
from workpilot.persistence.sqlalchemy_repository import SQLAlchemyRunRepository


@pytest.fixture
def repository() -> SQLAlchemyRunRepository:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    repository = SQLAlchemyRunRepository(
        sessionmaker(bind=engine, expire_on_commit=False),
        engine=engine,
    )
    yield repository
    repository.close()


def _record(run_id: str) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        goal="周报",
        workspace="basic_project",
        provider="stub",
        output_dir=Path("runs") / run_id,
    )


def _step(
    run_id: str,
    step_id: str = "scan_workspace",
) -> PlanStepRecord:
    return PlanStepRecord(
        run_id=run_id,
        plan_id=f"plan_{run_id}",
        step_id=step_id,
        objective="Scan workspace",
        tool="workspace.scan",
        tool_version="1.0",
        dependencies=(),
        expected_output="Files",
        success_rule_ids=("workspace.non_empty",),
        input_hash="a" * 64,
    )


def _call(
    run_id: str,
    call_id: str = "call_sql",
    step_id: str = "scan_workspace",
) -> ToolCallRecord:
    return ToolCallRecord(
        tool_call_id=call_id,
        run_id=run_id,
        step_id=step_id,
        execution_no=1,
        idempotency_key_hash="b" * 64,
        tool="workspace.scan",
        tool_version="1.0",
        input_hash="a" * 64,
    )


def _checkpoint(
    run_id: str,
    sequence: int = 1,
    step_id: str = "scan_workspace",
) -> CheckpointMetadataRecord:
    return CheckpointMetadataRecord(
        run_id=run_id,
        sequence=sequence,
        step_id=step_id,
        schema_version=1,
        relative_path=Path(f"checkpoints/checkpoint-{sequence:06d}.json"),
        sha256="c" * 64,
        byte_size=128,
    )


def test_sql_repository_round_trip(repository: SQLAlchemyRunRepository) -> None:
    created = repository.create(_record("run_sql"))
    assert repository.get("run_sql") == created

    updated = repository.update_state("run_sql", "passed")
    assert updated.state == "passed"
    assert updated.version == 2
    assert [record.run_id for record in repository.list()] == ["run_sql"]


def test_sql_repository_rejects_duplicate(repository: SQLAlchemyRunRepository) -> None:
    repository.create(_record("run_duplicate"))
    with pytest.raises(RunAlreadyExistsError):
        repository.create(_record("run_duplicate"))


def test_sql_repository_rejects_missing_update(
    repository: SQLAlchemyRunRepository,
) -> None:
    with pytest.raises(RunNotFoundError):
        repository.update_state("run_missing", "failed")


def test_sql_repository_idempotent_create(repository: SQLAlchemyRunRepository) -> None:
    first = repository.create_or_get(
        _record("run_first"),
        key_hash="c" * 64,
        request_fingerprint="d" * 64,
    )
    repeated = repository.create_or_get(
        _record("run_second"),
        key_hash="c" * 64,
        request_fingerprint="d" * 64,
    )

    assert first.created is True
    assert repeated.created is False
    assert repeated.record.run_id == "run_first"
    assert repository.get("run_first").idempotency_key_hash == "c" * 64

    with pytest.raises(IdempotencyConflictError):
        repository.create_or_get(
            _record("run_conflict"),
            key_hash="c" * 64,
            request_fingerprint="e" * 64,
        )


def test_sql_run_lease_claim_renew_takeover_and_terminal(
    repository: SQLAlchemyRunRepository,
) -> None:
    repository.create(_record("run_sql_lease"))

    first = repository.claim_run(
        "run_sql_lease", "owner-a", ttl_seconds=30
    )
    assert first.execution_attempt == 1
    assert repository.get("run_sql_lease").state == "running"
    with pytest.raises(LeaseConflictError):
        repository.claim_run("run_sql_lease", "owner-b", ttl_seconds=30)

    renewed = repository.renew_lease(first, ttl_seconds=60)
    assert renewed.expires_at > first.expires_at
    assert repository.assert_lease(renewed).owner_token == "owner-a"

    with repository._sessions.begin() as session:
        row = session.get(RunRow, "run_sql_lease")
        row.lease_expires_at = utc_now() - timedelta(seconds=1)

    second = repository.claim_run(
        "run_sql_lease", "owner-b", ttl_seconds=30
    )
    assert second.execution_attempt == 2
    assert repository.get("run_sql_lease").state == "recovering"
    with pytest.raises(LeaseLostError):
        repository.assert_lease(first)

    terminal = repository.commit_terminal(second, "passed")
    assert terminal.state == "passed"
    assert terminal.lease_owner is None
    with pytest.raises(LeaseConflictError):
        repository.claim_run("run_sql_lease", "owner-c", ttl_seconds=30)


def test_sql_active_lease_fences_execution_writes(
    repository: SQLAlchemyRunRepository,
) -> None:
    run_id = "run_sql_fence"
    repository.create(_record(run_id))
    repository.create_plan_steps(run_id, [_step(run_id)])
    repository.start_tool_call(_call(run_id, "call_sql_fence"))
    first = repository.claim_run(run_id, "owner-a", ttl_seconds=30)
    artifact = ArtifactMetadataRecord(
        run_id=run_id,
        name="trace",
        version=1,
        relative_path=Path("trace.json"),
        sha256="f" * 64,
        byte_size=42,
        media_type="application/json",
    )

    with pytest.raises(LeaseLostError):
        repository.update_state(run_id, "recovering")
    with pytest.raises(LeaseLostError):
        repository.update_plan_step_status(
            run_id, "scan_workspace", status="blocked"
        )
    with pytest.raises(LeaseLostError):
        repository.complete_tool_call(
            "call_sql_fence", output_summary={}, evidence_ids=()
        )
    with pytest.raises(LeaseLostError):
        repository.record_artifact(artifact)

    with repository._sessions.begin() as session:
        row = session.get(RunRow, run_id)
        row.lease_expires_at = utc_now() - timedelta(seconds=1)
    second = repository.claim_run(run_id, "owner-b", ttl_seconds=30)

    with pytest.raises(LeaseLostError):
        repository.fail_tool_call(
            "call_sql_fence", error_type="timeout", lease=first
        )
    failed = repository.fail_tool_call(
        "call_sql_fence", error_type="timeout", lease=second
    )
    assert failed.state == "failed"


def test_sql_execution_ledger_round_trip(
    repository: SQLAlchemyRunRepository,
) -> None:
    repository.create(_record("run_ledger"))
    step = _step("run_ledger")
    repository.create_plan_steps("run_ledger", [step])
    repository.create_plan_steps("run_ledger", [step])

    started = repository.start_tool_call(_call("run_ledger"))
    assert started.state == "running"
    assert repository.list_plan_steps("run_ledger")[0].status == "running"

    completed = repository.complete_tool_call(
        "call_sql",
        output_summary={"file_count": 2},
        evidence_ids=(),
        success_evaluation={"passed": True},
    )
    assert completed.state == "completed"
    assert repository.list_plan_steps("run_ledger")[0].status == "completed"
    assert repository.list_tool_calls("run_ledger") == [completed]

    artifact = ArtifactMetadataRecord(
        run_id="run_ledger",
        name="trace",
        version=1,
        relative_path=Path("trace.json"),
        sha256="f" * 64,
        byte_size=42,
        media_type="application/json",
    )
    repository.record_artifact(artifact)
    repository.record_artifact(artifact)
    assert repository.list_artifacts("run_ledger") == [artifact]


def test_sql_checkpoint_completion_is_atomic_and_idempotent(
    repository: SQLAlchemyRunRepository,
) -> None:
    repository.create(_record("run_checkpoint"))
    repository.create_plan_steps("run_checkpoint", [_step("run_checkpoint")])
    repository.start_tool_call(_call("run_checkpoint", "call_checkpoint"))
    checkpoint = _checkpoint("run_checkpoint")

    completed = repository.complete_tool_call_with_checkpoint(
        "call_checkpoint",
        checkpoint=checkpoint,
        output_summary={"file_count": 2},
        evidence_ids=(),
        success_evaluation={"passed": True},
    )
    repeated = repository.complete_tool_call_with_checkpoint(
        "call_checkpoint",
        checkpoint=checkpoint,
        output_summary={"file_count": 2},
        evidence_ids=(),
        success_evaluation={"passed": True},
    )

    assert repeated == completed
    assert repository.get("run_checkpoint").checkpoint_sequence == 1
    assert repository.list_plan_steps("run_checkpoint")[0].checkpoint_sequence == 1
    assert repository.get_latest_checkpoint("run_checkpoint") == checkpoint


def test_sql_checkpoint_failure_rolls_back_all_rows(
    repository: SQLAlchemyRunRepository,
) -> None:
    repository.create(_record("run_checkpoint_rollback"))
    repository.create_plan_steps(
        "run_checkpoint_rollback", [_step("run_checkpoint_rollback")]
    )
    repository.start_tool_call(
        _call("run_checkpoint_rollback", "call_checkpoint_rollback")
    )

    with pytest.raises(CheckpointSequenceError):
        repository.complete_tool_call_with_checkpoint(
            "call_checkpoint_rollback",
            checkpoint=_checkpoint("run_checkpoint_rollback", 2),
            output_summary={"file_count": 2},
            evidence_ids=(),
        )

    assert repository.get("run_checkpoint_rollback").checkpoint_sequence == 0
    assert repository.list_checkpoints("run_checkpoint_rollback") == []
    assert repository.list_tool_calls("run_checkpoint_rollback")[0].state == "running"


def test_sql_scheduler_terminal_state_without_tool_call(
    repository: SQLAlchemyRunRepository,
) -> None:
    repository.create(_record("run_blocked"))
    repository.create_plan_steps("run_blocked", [_step("run_blocked")])

    updated = repository.update_plan_step_status(
        "run_blocked",
        "scan_workspace",
        status="blocked",
        error_type="dependency_terminal",
    )

    assert updated.status == "blocked"
    assert updated.attempts == 0
    assert repository.list_plan_steps("run_blocked") == [updated]


def test_sql_tool_completion_rolls_back_step_and_call_together(
    repository: SQLAlchemyRunRepository,
) -> None:
    repository.create(_record("run_atomic"))
    repository.create_plan_steps("run_atomic", [_step("run_atomic")])
    repository.start_tool_call(_call("run_atomic", "call_atomic"))

    with pytest.raises(RepositoryUnavailableError):
        repository.complete_tool_call(
            "call_atomic",
            output_summary={"not_json": object()},
            evidence_ids=(),
        )

    assert repository.list_tool_calls("run_atomic")[0].state == "running"
    assert repository.list_plan_steps("run_atomic")[0].status == "running"


@pytest.mark.skipif(
    not os.environ.get("WORKPILOT_TEST_DATABASE_URL"),
    reason="dedicated PostgreSQL test database is not configured",
)
def test_real_postgresql_repository_round_trip() -> None:
    repository = SQLAlchemyRunRepository.from_url(
        os.environ["WORKPILOT_TEST_DATABASE_URL"]
    )
    run_id = f"test_{uuid.uuid4().hex}"
    try:
        repository.create(_record(run_id))
        assert repository.update_state(run_id, "passed").state == "passed"
        persisted = repository.get(run_id)
        assert persisted is not None
        assert persisted.state == "passed"
    finally:
        repository.close()


@pytest.mark.skipif(
    not os.environ.get("WORKPILOT_TEST_DATABASE_URL"),
    reason="dedicated PostgreSQL test database is not configured",
)
def test_real_postgresql_concurrent_idempotent_create() -> None:
    repository = SQLAlchemyRunRepository.from_url(
        os.environ["WORKPILOT_TEST_DATABASE_URL"]
    )
    suffix = uuid.uuid4().hex
    key_hash = uuid.uuid4().hex * 2
    fingerprint = uuid.uuid4().hex * 2

    def create(index: int):
        return repository.create_or_get(
            _record(f"test_concurrent_{index}_{suffix}"),
            key_hash=key_hash,
            request_fingerprint=fingerprint,
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, (1, 2)))
        assert {result.created for result in results} == {True, False}
        assert len({result.record.run_id for result in results}) == 1
    finally:
        repository.close()


@pytest.mark.skipif(
    not os.environ.get("WORKPILOT_TEST_DATABASE_URL"),
    reason="dedicated PostgreSQL test database is not configured",
)
def test_real_postgresql_serializes_competing_run_claims() -> None:
    repository = SQLAlchemyRunRepository.from_url(
        os.environ["WORKPILOT_TEST_DATABASE_URL"]
    )
    run_id = f"test_lease_claim_race_{uuid.uuid4().hex}"
    repository.create(_record(run_id))

    def claim(owner: str):
        try:
            return repository.claim_run(run_id, owner, ttl_seconds=30)
        except LeaseConflictError as exc:
            return exc

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ("worker-a", "worker-b")))
        assert sum(isinstance(result, RunLease) for result in results) == 1
        assert sum(isinstance(result, LeaseConflictError) for result in results) == 1
        persisted = repository.get(run_id)
        assert persisted.execution_attempt == 1
        assert persisted.state == "running"
    finally:
        repository.close()


@pytest.mark.skipif(
    not os.environ.get("WORKPILOT_TEST_DATABASE_URL"),
    reason="dedicated PostgreSQL test database is not configured",
)
def test_real_postgresql_takeover_fences_old_worker_writes() -> None:
    repository = SQLAlchemyRunRepository.from_url(
        os.environ["WORKPILOT_TEST_DATABASE_URL"]
    )
    run_id = f"test_lease_takeover_{uuid.uuid4().hex}"
    call_id = f"call_{uuid.uuid4().hex}"
    repository.create(_record(run_id))
    repository.create_plan_steps(run_id, [_step(run_id)])
    repository.start_tool_call(_call(run_id, call_id))
    first = repository.claim_run(run_id, "worker-a", ttl_seconds=30)
    with repository._sessions.begin() as session:
        row = session.get(RunRow, run_id)
        row.lease_expires_at = utc_now() - timedelta(seconds=1)
    second = repository.claim_run(run_id, "worker-b", ttl_seconds=30)

    try:
        with pytest.raises(LeaseLostError):
            repository.complete_tool_call_with_checkpoint(
                call_id,
                checkpoint=_checkpoint(run_id),
                output_summary={"file_count": 1},
                evidence_ids=(),
                lease=first,
            )
        with pytest.raises(LeaseLostError):
            repository.commit_terminal(first, "failed", "stale worker")

        completed = repository.complete_tool_call_with_checkpoint(
            call_id,
            checkpoint=_checkpoint(run_id),
            output_summary={"file_count": 1},
            evidence_ids=(),
            lease=second,
        )
        terminal = repository.commit_terminal(second, "passed")
        assert completed.state == "completed"
        assert terminal.state == "passed"
        assert terminal.execution_attempt == 2
        assert terminal.lease_owner is None
        assert repository.get_latest_checkpoint(run_id).sequence == 1
    finally:
        repository.close()


@pytest.mark.skipif(
    not os.environ.get("WORKPILOT_TEST_DATABASE_URL"),
    reason="dedicated PostgreSQL test database is not configured",
)
def test_real_postgresql_serializes_competing_checkpoint_sequences() -> None:
    repository = SQLAlchemyRunRepository.from_url(
        os.environ["WORKPILOT_TEST_DATABASE_URL"]
    )
    run_id = f"test_checkpoint_race_{uuid.uuid4().hex}"
    repository.create(_record(run_id))
    repository.create_plan_steps(
        run_id,
        [
            _step(run_id, "scan_workspace"),
            _step(run_id, "scan_workspace_second"),
        ],
    )
    first_call_id = f"call_first_{run_id}"
    second_call_id = f"call_second_{run_id}"
    repository.start_tool_call(
        _call(run_id, first_call_id, "scan_workspace")
    )
    repository.start_tool_call(
        _call(run_id, second_call_id, "scan_workspace_second")
    )

    def commit(step_id: str):
        call_id = (
            first_call_id
            if step_id == "scan_workspace"
            else second_call_id
        )
        try:
            return repository.complete_tool_call_with_checkpoint(
                call_id,
                checkpoint=_checkpoint(run_id, 1, step_id),
                output_summary={"file_count": 1},
                evidence_ids=(),
            )
        except CheckpointSequenceError as exc:
            return exc

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(commit, ("scan_workspace", "scan_workspace_second"))
            )
        assert sum(isinstance(result, ToolCallRecord) for result in results) == 1
        assert sum(isinstance(result, CheckpointSequenceError) for result in results) == 1
        assert repository.get(run_id).checkpoint_sequence == 1
        assert len(repository.list_checkpoints(run_id)) == 1
    finally:
        repository.close()
