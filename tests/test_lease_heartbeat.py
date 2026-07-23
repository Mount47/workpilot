"""Tests for lease renewal and Runtime ownership guards."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest

from workpilot.persistence import (
    InMemoryRunRepository,
    LeaseLostError,
    RepositoryUnavailableError,
    RunRecord,
)
from workpilot.providers.registry import get_provider
from workpilot.runtime.lease import LeaseHeartbeat
from workpilot.runtime.runner import Runtime


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 22, 8, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


def _claimed_repository(*, clock=None):
    repository = InMemoryRunRepository(clock=clock or FakeClock())
    repository.create(
        RunRecord(
            run_id="run_heartbeat",
            goal="report",
            workspace="basic_project",
            provider="stub",
            output_dir=Path("runs/run_heartbeat"),
        )
    )
    lease = repository.claim_run(
        "run_heartbeat", "owner-token-must-stay-secret", ttl_seconds=30
    )
    return repository, lease


@pytest.mark.parametrize(
    ("ttl", "interval"),
    [(0, 1), (30, 0), (30, 11)],
)
def test_heartbeat_rejects_unsafe_configuration(ttl: int, interval: int) -> None:
    repository, lease = _claimed_repository()
    with pytest.raises(ValueError):
        LeaseHeartbeat(
            repository,
            lease,
            ttl_seconds=ttl,
            heartbeat_seconds=interval,
        )


def test_heartbeat_renews_and_emits_only_safe_owner_fingerprint() -> None:
    repository, lease = _claimed_repository()
    renewed = Event()
    events: list[tuple[str, dict]] = []
    waits = 0

    def waiter(_: float) -> bool:
        nonlocal waits
        waits += 1
        return waits > 1

    def on_event(event_type: str, data: dict) -> None:
        events.append((event_type, data))
        if event_type == "lease_renewed":
            renewed.set()

    heartbeat = LeaseHeartbeat(
        repository,
        lease,
        ttl_seconds=30,
        heartbeat_seconds=10,
        on_event=on_event,
        waiter=waiter,
    ).start()
    assert renewed.wait(timeout=1)
    heartbeat.stop()

    assert repository.get("run_heartbeat").version == 3
    assert "lease_renewed" in [event_type for event_type, _ in events]
    assert "owner-token-must-stay-secret" not in repr(events)
    assert all(len(data["owner_fingerprint"]) == 12 for _, data in events)


def test_heartbeat_propagates_repository_outage() -> None:
    repository, lease = _claimed_repository()
    failed = Event()

    def broken_renew(*args, **kwargs):
        raise RepositoryUnavailableError("injected outage")

    repository.renew_lease = broken_renew
    heartbeat = LeaseHeartbeat(
        repository,
        lease,
        ttl_seconds=30,
        heartbeat_seconds=10,
        on_event=lambda event_type, _: (
            failed.set() if event_type == "lease_renewal_failed" else None
        ),
        waiter=lambda _: False,
    ).start()
    assert failed.wait(timeout=1)
    with pytest.raises(RepositoryUnavailableError):
        heartbeat.assert_execution_allowed()
    heartbeat.stop()


def test_synchronous_guard_detects_expiry_takeover() -> None:
    clock = FakeClock()
    repository, first = _claimed_repository(clock=clock)
    heartbeat = LeaseHeartbeat(
        repository,
        first,
        ttl_seconds=30,
        heartbeat_seconds=10,
    )
    clock.now += timedelta(seconds=31)
    repository.claim_run("run_heartbeat", "worker-b", ttl_seconds=30)

    with pytest.raises(LeaseLostError):
        heartbeat.assert_execution_allowed()
    assert isinstance(heartbeat.failure, LeaseLostError)


def test_runtime_checks_ownership_after_tool_returns(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    class LoseAfterTool:
        calls = 0

        def assert_execution_allowed(self) -> None:
            self.calls += 1
            if self.calls >= 2:
                raise LeaseLostError("lease lost after tool return")

    guard = LoseAfterTool()
    runtime = Runtime(
        workspace_root=basic_workspace,
        goal="report",
        output_dir=tmp_path / "lost-lease",
        provider=get_provider("stub"),
        execution_guard=guard,
    )

    result = runtime.execute()

    assert result.state.value == "failed"
    assert result.failure_reason == "lease lost after tool return"
    assert guard.calls == 2
