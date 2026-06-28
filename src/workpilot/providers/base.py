"""LLM Provider abstract base."""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel


class EvidenceType(str, Enum):
    """Types of evidence a snippet can represent."""

    PROGRESS = "progress"
    DECISION = "decision"
    RISK = "risk"
    BLOCKER = "blocker"
    ACTION_ITEM = "action_item"
    CONTEXT = "context"
    REQUIREMENT_CHANGE = "requirement_change"


class EvidenceCandidate(BaseModel):
    """A candidate evidence snippet extracted from source."""

    evidence_id: str
    source_file: str
    quote: str
    start_line: int
    end_line: int
    evidence_type: str = EvidenceType.CONTEXT


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
    def extract_evidence_from_file(
        self,
        file_path: str,
        content: str,
        goal: str,
    ) -> list[EvidenceCandidate]:
        """Extract evidence candidates from a single file's content."""
        ...
