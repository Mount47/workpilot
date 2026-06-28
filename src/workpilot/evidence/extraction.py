"""Evidence extractor — orchestrates per-file evidence extraction with validation."""

from workpilot.providers.base import EvidenceCandidate, LLMProvider
from workpilot.workspace.tools import WorkspaceTools


class EvidenceExtractor:
    """Orchestrates evidence extraction across workspace files.

    Calls provider per-file, validates quotes against source content,
    assigns stable evidence IDs, and discards invalid candidates.
    """

    def __init__(
        self,
        provider: LLMProvider,
        workspace: WorkspaceTools,
        goal: str,
    ) -> None:
        self.provider = provider
        self.workspace = workspace
        self.goal = goal
        self._counter = 0
        self._discarded: list[dict] = []

    def extract_all(self, files: list[str]) -> list[EvidenceCandidate]:
        """Extract evidence from all files. Returns validated candidates."""
        all_evidence: list[EvidenceCandidate] = []

        for file_path in files:
            try:
                content = self.workspace.read_file(file_path)
            except (PermissionError, FileNotFoundError):
                continue

            candidates = self.provider.extract_evidence_from_file(
                file_path=file_path,
                content=content,
                goal=self.goal,
            )

            for candidate in candidates:
                validated = self._validate_and_assign_id(candidate, content)
                if validated is not None:
                    all_evidence.append(validated)

        return all_evidence

    def get_discarded(self) -> list[dict]:
        """Return list of discarded candidates with reasons."""
        return self._discarded

    def _validate_and_assign_id(
        self, candidate: EvidenceCandidate, source_content: str
    ) -> EvidenceCandidate | None:
        """Validate quote exists in source and assign a stable ID."""
        lines = source_content.splitlines()

        if not candidate.quote.strip():
            self._discard(candidate, "empty quote")
            return None

        if candidate.quote.strip() not in source_content:
            self._discard(candidate, "quote not found in source file")
            return None

        if candidate.start_line < 1 or candidate.end_line < candidate.start_line:
            self._discard(candidate, "invalid line range")
            return None

        if candidate.end_line > len(lines):
            self._discard(candidate, "end_line exceeds file length")
            return None

        line_window = "\n".join(lines[candidate.start_line - 1 : candidate.end_line])
        if candidate.quote.strip() not in line_window:
            self._discard(candidate, "quote not found within specified line range")
            return None

        self._counter += 1
        return EvidenceCandidate(
            evidence_id=f"E-{self._counter:04d}",
            source_file=candidate.source_file,
            quote=candidate.quote.strip(),
            start_line=candidate.start_line,
            end_line=candidate.end_line,
            evidence_type=candidate.evidence_type,
        )

    def _discard(self, candidate: EvidenceCandidate, reason: str) -> None:
        self._discarded.append({
            "source_file": candidate.source_file,
            "quote": candidate.quote[:80],
            "reason": reason,
        })
