"""Trace journal — append-only run event log."""

from datetime import datetime, timezone
from typing import Any


class TraceJournal:
    """Append-only journal of trace events for a run."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._events: list[dict] = []
        self._sequence = 0

    def append(
        self,
        event_type: str,
        data: Any = None,
        *,
        step_id: str | None = None,
        parent_step_id: str | None = None,
    ) -> None:
        """Append a trace event."""
        self._sequence += 1
        event = {
            "event_id": f"evt_{self._sequence:04d}",
            "sequence": self._sequence,
            "run_id": self.run_id,
            "step_id": step_id,
            "parent_step_id": parent_step_id,
            "event_type": event_type,
            "data": data or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._events.append(event)

    def export(self) -> dict:
        """Export full trace as dict (for JSON serialization)."""
        return {
            "run_id": self.run_id,
            "event_count": len(self._events),
            "events": self._events,
        }
