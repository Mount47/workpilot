"""LLM Provider abstract base."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ValidationError
from json import JSONDecodeError


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


class GenerationResult(BaseModel):
    """Observable facts from one physical model API call."""

    content: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    request_id: str | None = None
    finish_reason: str | None = None
    estimated_cost: float | None = None
    task_type: str | None = None
    prompt_version: str | None = None
    schema_name: str | None = None
    schema_version: str | None = None
    route_target_index: int | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class StructuredGenerationResult:
    """Validated structured value plus every physical retry call."""

    value: BaseModel
    generations: tuple[GenerationResult, ...]


@dataclass(frozen=True)
class EvidenceExtractionResult:
    """Evidence candidates plus model calls and explicit failure semantics."""

    candidates: list[EvidenceCandidate]
    generations: tuple[GenerationResult, ...] = ()
    error_type: str | None = None


class ProviderResponseError(ValueError):
    """A model response could not be validated after bounded retries."""

    def __init__(
        self,
        message: str,
        generations: tuple[GenerationResult, ...],
    ) -> None:
        super().__init__(message)
        self.generations = generations
        self.error_type = "invalid_response"
        self.retryable = False


def structured_validation_feedback(
    error: JSONDecodeError | ValidationError,
) -> str:
    """Return actionable Schema feedback without copying business values."""
    if isinstance(error, JSONDecodeError):
        issue_summary = (
            f"json_invalid at line {error.lineno}, column {error.colno}"
        )
    else:
        issues = []
        for issue in error.errors(include_url=False, include_input=False)[:12]:
            location = ".".join(str(part) for part in issue["loc"]) or "root"
            issues.append(f"{location}: {issue['type']}")
        issue_summary = "; ".join(issues) or "schema_validation_failed"
    return (
        "The previous JSON response failed validation. Return the complete "
        "corrected JSON object only. Do not omit required nested objects. "
        f"Validation issues: {issue_summary}"
    )


class LLMProvider(ABC):
    """Abstract base class for all LLM providers."""

    @abstractmethod
    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> StructuredGenerationResult:
        """Generate structured output conforming to response_model schema."""
        ...

    @abstractmethod
    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> GenerationResult:
        """Generate free-form text."""
        ...

    @abstractmethod
    def extract_evidence_from_file(
        self,
        file_path: str,
        content: str,
        goal: str,
    ) -> EvidenceExtractionResult:
        """Extract evidence candidates from a single file's content."""
        ...
