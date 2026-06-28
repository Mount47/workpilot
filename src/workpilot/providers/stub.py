"""Stub provider for testing — returns fixed fixture data."""

from typing import Any

from pydantic import BaseModel

from workpilot.providers.base import EvidenceCandidate, LLMProvider


class StubProvider(LLMProvider):
    """Returns deterministic fixture data. No network calls."""

    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> BaseModel:
        """Not used in Phase 1 — stub synthesizer handles generation."""
        raise NotImplementedError("StubProvider.generate_structured not used in Phase 1")

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> str:
        return "Stub response: no real LLM invoked."

    def extract_evidence(
        self,
        files: list[str],
        workspace: Any,
    ) -> list[EvidenceCandidate]:
        """Return fixture evidence based on available files."""
        evidences = []
        for i, file_path in enumerate(files):
            content = workspace.read_file(file_path)
            # Take first non-empty line as quote
            lines = content.strip().splitlines()
            quote = lines[0] if lines else f"Content from {file_path}"
            evidences.append(
                EvidenceCandidate(
                    evidence_id=f"E-{i + 1:04d}",
                    source_file=file_path,
                    quote=quote,
                    start_line=1,
                    end_line=min(3, len(lines)),
                    evidence_type="context",
                )
            )
        return evidences
