"""Background Run lease renewal and execution ownership checks."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from threading import Event, Lock, Thread, current_thread

from workpilot.persistence.models import RunLease
from workpilot.persistence.repository import RunRepository


LeaseEventHandler = Callable[[str, dict], None]
Waiter = Callable[[float], bool]


class LeaseHeartbeat:
    """Renew one Run lease and surface ownership loss to Runtime promptly."""

    def __init__(
        self,
        repository: RunRepository,
        lease: RunLease,
        *,
        ttl_seconds: int,
        heartbeat_seconds: int,
        on_event: LeaseEventHandler | None = None,
        waiter: Waiter | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be positive")
        if heartbeat_seconds * 3 > ttl_seconds:
            raise ValueError("heartbeat_seconds must not exceed one third of TTL")
        self._repository = repository
        self._lease = lease
        self._ttl_seconds = ttl_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._on_event = on_event
        self._stop = Event()
        self._waiter = waiter or self._stop.wait
        self._lock = Lock()
        self._failure: Exception | None = None
        self._thread: Thread | None = None
        self._owner_fingerprint = hashlib.sha256(
            lease.owner_token.encode("utf-8")
        ).hexdigest()[:12]

    @property
    def lease(self) -> RunLease:
        with self._lock:
            return self._lease

    @property
    def failure(self) -> Exception | None:
        with self._lock:
            return self._failure

    def start(self) -> "LeaseHeartbeat":
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("LeaseHeartbeat has already been started")
            self._thread = Thread(
                target=self._run,
                name=f"lease-heartbeat-{self._lease.run_id}",
                daemon=True,
            )
            thread = self._thread
        self._emit("lease_heartbeat_started")
        thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout=1.0)
        self._emit("lease_heartbeat_stopped")

    def assert_execution_allowed(self) -> None:
        """Synchronously prove the current fence before sensitive execution."""
        failure = self.failure
        if failure is not None:
            raise failure
        try:
            current = self._repository.assert_lease(self.lease)
        except Exception as exc:
            self._record_failure(exc, "lease_assertion_failed")
            raise
        with self._lock:
            self._lease = current

    def _run(self) -> None:
        while not self._waiter(self._heartbeat_seconds):
            try:
                renewed = self._repository.renew_lease(
                    self.lease,
                    ttl_seconds=self._ttl_seconds,
                )
            except Exception as exc:
                self._record_failure(exc, "lease_renewal_failed")
                self._stop.set()
                return
            with self._lock:
                self._lease = renewed
            self._emit("lease_renewed")

    def _record_failure(self, exc: Exception, event_type: str) -> None:
        with self._lock:
            if self._failure is None:
                self._failure = exc
                should_emit = True
            else:
                should_emit = False
        if should_emit:
            self._emit(event_type, failure_type=type(exc).__name__)

    def _emit(self, event_type: str, **data) -> None:
        if self._on_event is None:
            return
        self._on_event(
            event_type,
            {
                "run_id": self._lease.run_id,
                "execution_attempt": self._lease.execution_attempt,
                "owner_fingerprint": self._owner_fingerprint,
                **data,
            },
        )
