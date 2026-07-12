"""Domain models for traceable project facts and analysis results."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class LocatorType(str, Enum):
    """Supported ways to locate evidence inside a source."""

    FILE_LINE = "file_line"
    PDF_PAGE = "pdf_page"
    SPREADSHEET_RANGE = "spreadsheet_range"
    EXTERNAL_RECORD = "external_record"


class SourceLocator(BaseModel):
    """Provider-neutral pointer to an exact unit inside a source.

    ``coordinates`` is deliberately typed but extensible: current text files
    use ``start_line`` and ``end_line``; future connectors can use page, sheet,
    cell range, message ID, or task ID without changing Evidence itself.
    """

    source_id: str = Field(min_length=1)
    locator_type: LocatorType
    coordinates: dict[str, str | int] = Field(default_factory=dict)
    source_version: str | None = None

    @model_validator(mode="after")
    def validate_coordinates(self) -> "SourceLocator":
        if self.locator_type == LocatorType.FILE_LINE:
            start = self.coordinates.get("start_line")
            end = self.coordinates.get("end_line")
            if not isinstance(start, int) or not isinstance(end, int):
                raise ValueError("file_line locator requires integer start_line and end_line")
            if start < 1 or end < start:
                raise ValueError("invalid file line range")
        return self

    @classmethod
    def for_file_lines(
        cls,
        source_file: str,
        start_line: int,
        end_line: int,
        source_version: str | None = None,
    ) -> "SourceLocator":
        """Create a locator for the currently supported text-file source."""
        return cls(
            source_id=source_file,
            locator_type=LocatorType.FILE_LINE,
            coordinates={"start_line": start_line, "end_line": end_line},
            source_version=source_version,
        )

    def display(self) -> str:
        """Return a stable human-readable locator."""
        if self.locator_type == LocatorType.FILE_LINE:
            return (
                f"{self.source_id}:L{self.coordinates['start_line']}"
                f"-L{self.coordinates['end_line']}"
            )
        rendered = ",".join(f"{key}={value}" for key, value in self.coordinates.items())
        return f"{self.source_id}:{rendered}" if rendered else self.source_id


class Evidence(BaseModel):
    """A validated, citable record originating from an external source."""

    evidence_id: str = Field(pattern=r"^E-\d{4,}$")
    locator: SourceLocator
    quote: str = Field(min_length=1)
    evidence_type: str = "context"
    source_hash: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def source_file(self) -> str:
        """Compatibility accessor for the current file-based pipeline."""
        return self.locator.source_id

    @property
    def start_line(self) -> int:
        """Compatibility accessor for the current file-based pipeline."""
        value = self.locator.coordinates.get("start_line")
        if not isinstance(value, int):
            raise ValueError("evidence locator has no start_line")
        return value

    @property
    def end_line(self) -> int:
        """Compatibility accessor for the current file-based pipeline."""
        value = self.locator.coordinates.get("end_line")
        if not isinstance(value, int):
            raise ValueError("evidence locator has no end_line")
        return value


class ClaimType(str, Enum):
    """How a report claim was produced."""

    EXPLICIT_FACT = "explicit_fact"
    DERIVED_FACT = "derived_fact"
    ANALYTICAL_JUDGEMENT = "analytical_judgement"
    UNKNOWN = "unknown"


class ClaimCategory(str, Enum):
    """Business category used to place a claim in project artifacts."""

    PROGRESS = "progress"
    DECISION = "decision"
    RISK = "risk"
    BLOCKER = "blocker"
    ACTION_ITEM = "action_item"
    CONTEXT = "context"
    REQUIREMENT_CHANGE = "requirement_change"


class Claim(BaseModel):
    """The smallest independently verifiable statement in an artifact."""

    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    claim_type: ClaimType
    category: ClaimCategory
    evidence_refs: list[str] = Field(default_factory=list)
    derivation: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_support_contract(self) -> "Claim":
        if self.claim_type == ClaimType.UNKNOWN:
            if self.evidence_refs:
                raise ValueError("unknown claim cannot cite evidence as support")
            if self.derivation is not None:
                raise ValueError("unknown claim cannot have a derivation")
            return self

        if not self.evidence_refs:
            raise ValueError("non-unknown claim requires at least one evidence reference")
        if self.claim_type == ClaimType.DERIVED_FACT and not self.derivation:
            raise ValueError("derived fact requires a derivation")
        return self


class ProjectSnapshot(BaseModel):
    """Versioned project state assembled from validated claims."""

    project_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_ids: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
