"""Contract tests for deterministic RunRepository behavior."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from workpilot.persistence import (
    IdempotencyConflictError,
    InMemoryRunRepository,
    RunAlreadyExistsError,
    RunNotFoundError,
    RunRecord,
)


def _record(run_id: str, *, created_at: datetime | None = None) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        goal="生成周报",
        workspace="basic_project",
        provider="stub",
        output_dir=Path("runs") / run_id,
        created_at=created_at,
        updated_at=created_at,
    )


def test_create_get_and_update_run() -> None:
    repository = InMemoryRunRepository()
    created = repository.create(_record("run_1"))

    assert repository.get("run_1") == created

    updated = repository.update_state("run_1", "failed", "provider timeout")
    assert updated.state == "failed"
    assert updated.failure_reason == "provider timeout"
    assert updated.version == 2
    assert repository.get("run_1") == updated


def test_list_runs_newest_first_and_limit() -> None:
    repository = InMemoryRunRepository()
    now = datetime.now(timezone.utc)
    repository.create(_record("run_old", created_at=now - timedelta(minutes=1)))
    repository.create(_record("run_new", created_at=now))

    assert [record.run_id for record in repository.list()] == ["run_new", "run_old"]
    assert [record.run_id for record in repository.list(limit=1)] == ["run_new"]


def test_duplicate_run_is_rejected() -> None:
    repository = InMemoryRunRepository()
    repository.create(_record("run_duplicate"))

    with pytest.raises(RunAlreadyExistsError):
        repository.create(_record("run_duplicate"))


def test_update_missing_run_is_rejected() -> None:
    repository = InMemoryRunRepository()

    with pytest.raises(RunNotFoundError):
        repository.update_state("run_missing", "failed")


def test_idempotent_create_returns_existing_matching_run() -> None:
    repository = InMemoryRunRepository()

    first = repository.create_or_get(
        _record("run_first"),
        key_hash="hashed-key",
        request_fingerprint="same-request",
    )
    repeated = repository.create_or_get(
        _record("run_second"),
        key_hash="hashed-key",
        request_fingerprint="same-request",
    )

    assert first.created is True
    assert repeated.created is False
    assert repeated.record.run_id == "run_first"
    assert repeated.record.idempotency_key_hash == "hashed-key"
    assert repository.get("run_second") is None


def test_idempotent_create_rejects_key_reuse_for_different_request() -> None:
    repository = InMemoryRunRepository()
    repository.create_or_get(
        _record("run_first"),
        key_hash="hashed-key",
        request_fingerprint="first-request",
    )

    with pytest.raises(IdempotencyConflictError):
        repository.create_or_get(
            _record("run_second"),
            key_hash="hashed-key",
            request_fingerprint="different-request",
        )
