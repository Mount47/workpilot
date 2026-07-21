"""Evidence store — manages validated evidence records for a run."""

from workpilot.domain import Evidence


class EvidenceStore:
    """In-memory evidence store. Insert, get, list evidence records."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._records: dict[str, Evidence] = {}

    def insert(self, evidence: Evidence) -> None:
        """Insert an evidence record. Overwrites if same ID exists."""
        self._records[evidence.evidence_id] = evidence

    def get_by_id(self, evidence_id: str) -> Evidence | None:
        """Get evidence by ID, returns None if not found."""
        return self._records.get(evidence_id)

    def list_all(self) -> list[Evidence]:
        """List all evidence records."""
        return list(self._records.values())

    def count(self) -> int:
        return len(self._records)

    def export(self) -> list[dict]:
        """Export all evidence as list of dicts (for serialization)."""
        return [ev.model_dump(mode="json") for ev in self._records.values()]
