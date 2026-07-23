from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from workpilot.persistence import (
    InMemoryRunRepository,
    LeaseConflictError,
    LeaseLostError,
    RunLease,
    RunRecord,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 22, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def _repository(clock: FakeClock) -> InMemoryRunRepository:
    repository = InMemoryRunRepository(clock=clock)
    repository.create(
        RunRecord(
            run_id="run_lease",
            goal="report",
            workspace="project",
            provider="stub",
            output_dir=Path("runs/run_lease"),
        )
    )
    return repository


def test_pending_claim_and_renew_use_monotonic_attempt() -> None:
    clock = FakeClock()
    repository = _repository(clock)

    lease = repository.claim_run("run_lease", "owner-a", ttl_seconds=30)

    assert lease == RunLease(
        run_id="run_lease",
        owner_token="owner-a",
        execution_attempt=1,
        expires_at=clock.now + timedelta(seconds=30),
    )
    record = repository.get("run_lease")
    assert record.state == "running"
    assert record.lease_owner == "owner-a"
    assert record.execution_attempt == 1

    clock.advance(10)
    renewed = repository.renew_lease(lease, ttl_seconds=30)
    assert renewed.expires_at == clock.now + timedelta(seconds=30)
    assert renewed.execution_attempt == 1
    assert repository.assert_lease(renewed) == renewed


def test_active_lease_rejects_second_worker() -> None:
    clock = FakeClock()
    repository = _repository(clock)
    repository.claim_run("run_lease", "owner-a", ttl_seconds=30)

    with pytest.raises(LeaseConflictError, match="active lease"):
        repository.claim_run("run_lease", "owner-b", ttl_seconds=30)


def test_expired_lease_can_be_taken_over_and_fences_old_worker() -> None:
    clock = FakeClock()
    repository = _repository(clock)
    old = repository.claim_run("run_lease", "owner-a", ttl_seconds=30)
    clock.advance(31)

    current = repository.claim_run("run_lease", "owner-b", ttl_seconds=30)

    assert current.execution_attempt == 2
    assert repository.get("run_lease").state == "recovering"
    with pytest.raises(LeaseLostError, match="does not match"):
        repository.assert_lease(old)
    with pytest.raises(LeaseLostError):
        repository.renew_lease(old, ttl_seconds=30)


def test_wrong_owner_stale_attempt_and_expired_renew_fail_closed() -> None:
    clock = FakeClock()
    repository = _repository(clock)
    lease = repository.claim_run("run_lease", "owner-a", ttl_seconds=30)

    with pytest.raises(LeaseLostError):
        repository.assert_lease(
            RunLease(
                run_id=lease.run_id,
                owner_token="owner-b",
                execution_attempt=lease.execution_attempt,
                expires_at=lease.expires_at,
            )
        )
    with pytest.raises(LeaseLostError):
        repository.assert_lease(
            RunLease(
                run_id=lease.run_id,
                owner_token=lease.owner_token,
                execution_attempt=99,
                expires_at=lease.expires_at,
            )
        )

    clock.advance(30)
    with pytest.raises(LeaseLostError, match="expired"):
        repository.renew_lease(lease, ttl_seconds=30)


def test_terminal_commit_is_atomic_and_terminal_run_cannot_be_claimed() -> None:
    clock = FakeClock()
    repository = _repository(clock)
    lease = repository.claim_run("run_lease", "owner-a", ttl_seconds=30)

    terminal = repository.commit_terminal(lease, "passed")

    assert terminal.state == "passed"
    assert terminal.lease_owner is None
    assert terminal.lease_expires_at is None
    assert terminal.execution_attempt == 1
    with pytest.raises(LeaseLostError):
        repository.assert_lease(lease)
    with pytest.raises(LeaseConflictError, match="terminal"):
        repository.claim_run("run_lease", "owner-b", ttl_seconds=30)


@pytest.mark.parametrize("ttl_seconds", [0, -1])
def test_invalid_ttl_is_rejected(ttl_seconds: int) -> None:
    clock = FakeClock()
    repository = _repository(clock)

    with pytest.raises(ValueError, match="ttl_seconds"):
        repository.claim_run("run_lease", "owner-a", ttl_seconds=ttl_seconds)

