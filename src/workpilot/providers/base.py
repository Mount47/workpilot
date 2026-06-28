"""LLM Provider abstract base."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class EvidenceCandidate(BaseModel):
    """A candidate evidence snippet extracted from source."""

    evidence_id: str
    source_file: str
    quote: str
    start_line: int
    end_line: int
    evidence_type: str = "context"


class LLMProvider(ABC):
    """Abstract base class for all LLM providers."""

    @abstractmethod
    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> BaseModel:
        """Generate structured output conforming to response_model schema."""
        ...

    @abstractmethod
    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> str:
        """Generate free-form text."""
        ...

    @abstractmethod
    def extract_evidence(
        self,
        files: list[str],
        workspace: Any,
    ) -> list[EvidenceCandidate]:
        """Extract evidence candidates from workspace files."""
        ...
