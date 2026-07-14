"""Tests for Evidence-to-Claim source coverage verification."""

from workpilot.domain import (
    Claim,
    ClaimCategory,
    ClaimType,
    Evidence,
    ProjectSnapshot,
    SourceLocator,
)
from workpilot.evidence.store import EvidenceStore
from workpilot.verification.source_coverage_verifier import SourceCoverageVerifier


def _store() -> EvidenceStore:
    store = EvidenceStore(run_id="run-1")
    for index, source in enumerate(["meeting.md", "tasks.csv"], start=1):
        store.insert(
            Evidence(
                evidence_id=f"E-{index:04d}",
                locator=SourceLocator.for_file_lines(source, 1, 1),
                quote=f"source-{index}",
            )
        )
    return store


def test_source_coverage_fails_when_claims_omit_an_evidence_source() -> None:
    snapshot = ProjectSnapshot(
        project_id="project-1",
        snapshot_id="snapshot-1",
        claims=[
            Claim(
                claim_id="C-0001",
                text="source-1",
                claim_type=ClaimType.EXPLICIT_FACT,
                category=ClaimCategory.CONTEXT,
                evidence_refs=["E-0001"],
            )
        ],
    )

    results = SourceCoverageVerifier(_store()).verify(snapshot)

    by_location = {result.location: result for result in results}
    assert by_location["sources.meeting.md"].status == "passed"
    assert by_location["sources.tasks.csv"].status == "failed"
    assert by_location["sources.tasks.csv"].severity == "error"


def test_source_coverage_passes_when_every_source_contributes() -> None:
    snapshot = ProjectSnapshot(
        project_id="project-1",
        snapshot_id="snapshot-1",
        claims=[
            Claim(
                claim_id=f"C-{index:04d}",
                text=f"source-{index}",
                claim_type=ClaimType.EXPLICIT_FACT,
                category=ClaimCategory.CONTEXT,
                evidence_refs=[f"E-{index:04d}"],
            )
            for index in (1, 2)
        ],
    )

    results = SourceCoverageVerifier(_store()).verify(snapshot)

    assert len(results) == 2
    assert all(result.status == "passed" for result in results)
