"""Tests for the shared WorkPilot domain models."""

import pytest
from pydantic import ValidationError

from workpilot.domain import (
    Claim,
    ClaimCategory,
    ClaimType,
    Evidence,
    LocatorType,
    ProjectSnapshot,
    SourceLocator,
)


def test_file_line_locator_has_stable_display() -> None:
    locator = SourceLocator.for_file_lines("meeting.md", 3, 5)

    assert locator.locator_type == LocatorType.FILE_LINE
    assert locator.display() == "meeting.md:L3-L5"


def test_file_line_locator_rejects_invalid_range() -> None:
    with pytest.raises(ValidationError):
        SourceLocator.for_file_lines("meeting.md", 5, 3)


def test_evidence_exposes_file_compatibility_properties() -> None:
    evidence = Evidence(
        evidence_id="E-0001",
        locator=SourceLocator.for_file_lines("meeting.md", 7, 8),
        quote="项目计划已经确认。",
        evidence_type="decision",
    )

    assert evidence.source_file == "meeting.md"
    assert evidence.start_line == 7
    assert evidence.end_line == 8


def test_supported_claim_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        Claim(
            claim_id="C-0001",
            text="里程碑已经完成。",
            claim_type=ClaimType.EXPLICIT_FACT,
            category=ClaimCategory.PROGRESS,
        )


def test_derived_claim_requires_derivation() -> None:
    with pytest.raises(ValidationError):
        Claim(
            claim_id="C-0001",
            text="里程碑延期七天。",
            claim_type=ClaimType.DERIVED_FACT,
            category=ClaimCategory.RISK,
            evidence_refs=["E-0001", "E-0002"],
        )


def test_project_snapshot_accepts_validated_claims() -> None:
    claim = Claim(
        claim_id="C-0001",
        text="负责人尚未明确。",
        claim_type=ClaimType.UNKNOWN,
        category=ClaimCategory.ACTION_ITEM,
    )
    snapshot = ProjectSnapshot(
        project_id="project-1",
        snapshot_id="snapshot-1",
        source_ids=["meeting.md"],
        claims=[claim],
    )

    assert snapshot.claims == [claim]
