"""Tests for EvidenceExtractor — validation and extraction logic."""

from pathlib import Path

import pytest

from workpilot.evidence.extraction import EvidenceExtractor
from workpilot.providers.base import (
    EvidenceCandidate,
    EvidenceExtractionResult,
    EvidenceType,
)
from workpilot.providers.stub import StubProvider
from workpilot.workspace.tools import WorkspaceTools


RICH_WORKSPACE = Path(__file__).parent / "fixtures" / "workspaces" / "rich_project"


@pytest.fixture
def rich_workspace() -> Path:
    return RICH_WORKSPACE


@pytest.fixture
def extractor(rich_workspace: Path) -> EvidenceExtractor:
    provider = StubProvider()
    workspace = WorkspaceTools(workspace_root=rich_workspace)
    return EvidenceExtractor(
        provider=provider,
        workspace=workspace,
        goal="生成本周项目周报",
    )


def test_extract_all_returns_evidence(extractor: EvidenceExtractor) -> None:
    """Extractor should return multiple evidence items from rich workspace."""
    workspace = extractor.workspace
    files = workspace.list_files()
    evidences = extractor.extract_all(files)

    assert len(evidences) > 0
    for ev in evidences:
        assert ev.evidence_id.startswith("E-")
        assert ev.quote.strip() != ""


def test_evidence_ids_are_unique(extractor: EvidenceExtractor) -> None:
    """All evidence IDs should be unique within a single extraction run."""
    files = extractor.workspace.list_files()
    evidences = extractor.extract_all(files)

    ids = [ev.evidence_id for ev in evidences]
    assert len(ids) == len(set(ids))


def test_quotes_exist_in_source(extractor: EvidenceExtractor) -> None:
    """Every extracted quote must exist in its source file."""
    files = extractor.workspace.list_files()
    evidences = extractor.extract_all(files)

    for ev in evidences:
        content = extractor.workspace.read_file(ev.source_file)
        assert ev.quote in content, (
            f"Quote not found in {ev.source_file}: {ev.quote[:50]}"
        )


def test_line_range_contains_quote(extractor: EvidenceExtractor) -> None:
    """Quote must appear within the specified line range."""
    files = extractor.workspace.list_files()
    evidences = extractor.extract_all(files)

    for ev in evidences:
        content = extractor.workspace.read_file(ev.source_file)
        lines = content.splitlines()
        window = "\n".join(lines[ev.start_line - 1 : ev.end_line])
        assert ev.quote in window, (
            f"Quote not in lines {ev.start_line}-{ev.end_line} of {ev.source_file}"
        )


def test_invalid_quote_is_discarded() -> None:
    """Evidence with a quote not in the source should be discarded."""
    from unittest.mock import MagicMock

    workspace = WorkspaceTools(workspace_root=RICH_WORKSPACE)

    fake_provider = MagicMock()
    fake_provider.extract_evidence_from_file.return_value = EvidenceExtractionResult(
        candidates=[
            EvidenceCandidate(
                evidence_id="",
                source_file="meeting_notes.md",
                quote="this text does not exist anywhere in the file",
                start_line=1,
                end_line=1,
                evidence_type=EvidenceType.CONTEXT,
            )
        ]
    )

    extractor = EvidenceExtractor(
        provider=fake_provider,
        workspace=workspace,
        goal="test",
    )
    evidences = extractor.extract_all(["meeting_notes.md"])

    assert len(evidences) == 0
    assert len(extractor.get_discarded()) == 1
    assert "not found" in extractor.get_discarded()[0]["reason"]


def test_invalid_line_range_is_deterministically_relocated() -> None:
    """An exact quote survives a bad model locator through deterministic repair."""
    from unittest.mock import MagicMock

    workspace = WorkspaceTools(workspace_root=RICH_WORKSPACE)

    fake_provider = MagicMock()
    fake_provider.extract_evidence_from_file.return_value = EvidenceExtractionResult(
        candidates=[
            EvidenceCandidate(
                evidence_id="",
                source_file="meeting_notes.md",
                quote="支付重试逻辑的 API 设计仍未确定，李四需要本周给出方案，否则下游开发将被阻塞。",
                start_line=5,
                end_line=3,
                evidence_type=EvidenceType.RISK,
            )
        ]
    )

    extractor = EvidenceExtractor(
        provider=fake_provider,
        workspace=workspace,
        goal="test",
    )
    evidences = extractor.extract_all(["meeting_notes.md"])

    assert len(evidences) == 1
    assert evidences[0].start_line == 12
    assert evidences[0].end_line == 12
    assert evidences[0].metadata["locator_repaired"] is True
    assert extractor.get_discarded() == []
    report = extractor.get_source_reports()[0]
    assert report.locator_repaired_count == 1


def test_locator_repair_prefers_occurrence_nearest_model_line(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    source = tmp_path / "repeated.md"
    source.write_text("same\nmiddle\nsame\n", encoding="utf-8")
    provider = MagicMock()
    provider.extract_evidence_from_file.return_value = EvidenceExtractionResult(
        candidates=[
            EvidenceCandidate(
                evidence_id="",
                source_file="repeated.md",
                quote="same",
                start_line=4,
                end_line=4,
            )
        ]
    )
    extractor = EvidenceExtractor(
        provider=provider,
        workspace=WorkspaceTools(workspace_root=tmp_path),
        goal="test",
    )

    evidences = extractor.extract_all(["repeated.md"])

    assert evidences[0].start_line == 3
    assert evidences[0].end_line == 3


def test_multiple_evidence_types_extracted(extractor: EvidenceExtractor) -> None:
    """Extractor should identify different evidence types from rich content."""
    files = extractor.workspace.list_files()
    evidences = extractor.extract_all(files)

    types_found = {ev.evidence_type for ev in evidences}
    assert len(types_found) >= 2, f"Expected multiple types, got: {types_found}"


def test_stub_classification_uses_action_section_before_risk_keywords() -> None:
    content = """# 周会纪要

## 讨论内容

3. 告警频率上升，需要排查原因。

## 下一步

- 张三：协调 SRE 排查告警上升原因。
"""

    extraction = StubProvider().extract_evidence_from_file(
        "meeting.md",
        content,
        "生成周报",
    )

    by_quote = {candidate.quote: candidate for candidate in extraction.candidates}
    assert by_quote["3. 告警频率上升，需要排查原因。"].evidence_type == "risk"
    assert (
        by_quote["- 张三：协调 SRE 排查告警上升原因。"].evidence_type
        == "action_item"
    )


def test_extractor_reports_per_source_acceptance(extractor: EvidenceExtractor) -> None:
    files = extractor.workspace.list_files()

    evidences = extractor.extract_all(files)
    reports = extractor.get_source_reports()

    assert {report.source_id for report in reports} == set(files)
    assert sum(report.accepted_count for report in reports) == len(evidences)
    assert all(report.read_error_type is None for report in reports)
    assert all(report.discard_reason_counts == {} for report in reports)


def test_starting_index_prevents_repair_id_collision(rich_workspace: Path) -> None:
    extractor = EvidenceExtractor(
        provider=StubProvider(),
        workspace=WorkspaceTools(workspace_root=rich_workspace),
        goal="test",
        starting_index=7,
    )

    evidences = extractor.extract_all(["meeting_notes.md"])

    assert evidences[0].evidence_id == "E-0008"
