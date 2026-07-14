"""Evidence extractor — orchestrates per-file evidence extraction with validation."""

from collections.abc import Callable

from workpilot.domain import Evidence, SourceLocator
from workpilot.evidence.quality import SourceExtractionReport
from workpilot.providers.base import EvidenceCandidate, GenerationResult, LLMProvider
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
        on_model_call: Callable[[GenerationResult], None] | None = None,
        starting_index: int = 0,
    ) -> None:
        self.provider = provider
        self.workspace = workspace
        self.goal = goal
        self.on_model_call = on_model_call
        self._counter = starting_index
        self._discarded: list[dict] = []
        self._model_calls: list[GenerationResult] = []
        self._provider_errors: list[dict[str, str]] = []
        self._source_reports: list[SourceExtractionReport] = []

    def extract_all(self, files: list[str]) -> list[Evidence]:
        """Extract evidence from all files. Returns validated candidates."""
        all_evidence: list[Evidence] = []

        for file_path in files:
            try:
                content = self.workspace.read_file(file_path)
            except (PermissionError, FileNotFoundError) as exc:
                self._source_reports.append(
                    SourceExtractionReport(
                        source_id=file_path,
                        status="read_failed",
                        read_error_type=type(exc).__name__,
                    )
                )
                continue

            extraction = self.provider.extract_evidence_from_file(
                file_path=file_path,
                content=content,
                goal=self.goal,
            )
            self._model_calls.extend(extraction.generations)
            if extraction.error_type:
                self._provider_errors.append(
                    {"source_file": file_path, "error_type": extraction.error_type}
                )
            if self.on_model_call:
                for generation in extraction.generations:
                    self.on_model_call(generation)

            accepted_count = 0
            discarded_before = len(self._discarded)
            for candidate in extraction.candidates:
                validated = self._validate_and_assign_id(candidate, content)
                if validated is not None:
                    all_evidence.append(validated)
                    accepted_count += 1
            self._source_reports.append(
                SourceExtractionReport(
                    source_id=file_path,
                    candidate_count=len(extraction.candidates),
                    accepted_count=accepted_count,
                    discarded_count=len(self._discarded) - discarded_before,
                    provider_error_type=extraction.error_type,
                )
            )

        return all_evidence

    def get_discarded(self) -> list[dict]:
        """Return list of discarded candidates with reasons."""
        return self._discarded

    def get_model_calls(self) -> list[GenerationResult]:
        """Return physical provider calls made during extraction."""
        return list(self._model_calls)

    def get_provider_errors(self) -> list[dict[str, str]]:
        """Return provider failures separately from valid empty extraction."""
        return list(self._provider_errors)

    def get_source_reports(self) -> list[SourceExtractionReport]:
        """Return content-free per-source outcomes for deterministic gates."""
        return list(self._source_reports)

    def _validate_and_assign_id(
        self, candidate: EvidenceCandidate, source_content: str
    ) -> Evidence | None:
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
        return Evidence(
            evidence_id=f"E-{self._counter:04d}",
            locator=SourceLocator.for_file_lines(
                source_file=candidate.source_file,
                start_line=candidate.start_line,
                end_line=candidate.end_line,
            ),
            quote=candidate.quote.strip(),
            evidence_type=candidate.evidence_type,
        )

    def _discard(self, candidate: EvidenceCandidate, reason: str) -> None:
        self._discarded.append({
            "source_file": candidate.source_file,
            "quote": candidate.quote[:80],
            "reason": reason,
        })
