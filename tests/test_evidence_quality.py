"""Tests for deterministic source coverage and Evidence acceptance gates."""

from workpilot.evidence.quality import (
    EvidenceQualityPolicy,
    SourceExtractionReport,
)


def test_quality_gate_passes_processed_sources_with_evidence() -> None:
    report = EvidenceQualityPolicy().evaluate(
        scanned_source_ids=["meeting.md", "tasks.csv"],
        source_reports=[
            SourceExtractionReport(
                source_id="meeting.md",
                candidate_count=2,
                accepted_count=2,
            ),
            SourceExtractionReport(
                source_id="tasks.csv",
                candidate_count=1,
                accepted_count=1,
            ),
        ],
        accepted_evidence_count=3,
    )

    assert report.passed is True
    assert report.repair_source_ids == []


def test_quality_gate_requests_repair_when_all_candidates_are_discarded() -> None:
    report = EvidenceQualityPolicy().evaluate(
        scanned_source_ids=["meeting.md"],
        source_reports=[
            SourceExtractionReport(
                source_id="meeting.md",
                candidate_count=2,
                accepted_count=0,
                discarded_count=2,
            )
        ],
        accepted_evidence_count=0,
    )

    assert report.passed is False
    assert report.repair_source_ids == ["meeting.md"]
    assert report.locator_repaired_count == 0
    assert report.discard_reason_counts == {}
    assert {
        check.check_id for check in report.checks if check.status == "failed"
    } == {
        "evidence.acceptance.all_discarded",
        "evidence.acceptance.non_empty",
    }


def test_quality_gate_allows_irrelevant_source_with_empty_result() -> None:
    report = EvidenceQualityPolicy().evaluate(
        scanned_source_ids=["meeting.md", "archive.md"],
        source_reports=[
            SourceExtractionReport(
                source_id="meeting.md",
                candidate_count=1,
                accepted_count=1,
            ),
            SourceExtractionReport(source_id="archive.md"),
        ],
        accepted_evidence_count=1,
    )

    assert report.passed is True
    warning_ids = {
        check.check_id
        for check in report.checks
        if check.severity == "warning"
    }
    assert "evidence.acceptance.empty_source_result" in warning_ids


def test_quality_gate_fails_provider_error_even_with_candidates() -> None:
    report = EvidenceQualityPolicy().evaluate(
        scanned_source_ids=["meeting.md"],
        source_reports=[
            SourceExtractionReport(
                source_id="meeting.md",
                candidate_count=1,
                accepted_count=1,
                provider_error_type="invalid_response",
            )
        ],
        accepted_evidence_count=1,
    )

    assert report.passed is False
    assert report.repair_source_ids == ["meeting.md"]
