"""Tests for CitationVerifier — verifies citation checking logic."""

from pathlib import Path

import pytest

from workpilot.evidence.store import EvidenceStore
from workpilot.providers.base import EvidenceCandidate, EvidenceType
from workpilot.verification.citation_verifier import CitationVerifier
from workpilot.workspace.tools import WorkspaceTools


RICH_WORKSPACE = Path(__file__).parent / "fixtures" / "workspaces" / "rich_project"


@pytest.fixture
def workspace() -> WorkspaceTools:
    return WorkspaceTools(workspace_root=RICH_WORKSPACE)


@pytest.fixture
def valid_evidence(workspace: WorkspaceTools) -> EvidenceStore:
    """Evidence store with valid references to rich_project files."""
    store = EvidenceStore(run_id="test-run")
    store.insert(EvidenceCandidate(
        evidence_id="E-0001",
        source_file="meeting_notes.md",
        quote="支付重试逻辑的 API 设计仍未确定，李四需要本周给出方案，否则下游开发将被阻塞。",
        start_line=12,
        end_line=12,
        evidence_type=EvidenceType.RISK,
    ))
    store.insert(EvidenceCandidate(
        evidence_id="E-0002",
        source_file="meeting_notes.md",
        quote="支付重试方案截止日期定为本周五（6月27日）。",
        start_line=18,
        end_line=18,
        evidence_type=EvidenceType.DECISION,
    ))
    store.insert(EvidenceCandidate(
        evidence_id="E-0003",
        source_file="pr_summary.md",
        quote="QPS 从 1200 提升至 1380（+15%）",
        start_line=7,
        end_line=7,
        evidence_type=EvidenceType.PROGRESS,
    ))
    return store


def test_valid_citations_pass(workspace: WorkspaceTools, valid_evidence: EvidenceStore) -> None:
    """All valid citations should pass verification."""
    verifier = CitationVerifier(evidence_store=valid_evidence, workspace=workspace)
    artifacts = {
        "weekly_report.md": (
            "## Progress\n\n"
            "- 商品详情页缓存优化已上线 [E-0003]\n\n"
            "## Risks\n\n"
            "- 支付重试 API 设计未确定 [E-0001]\n"
        ),
    }

    results = verifier.verify(artifacts=artifacts)

    failed = [r for r in results if r.status == "failed"]
    assert len(failed) == 0, f"Unexpected failures: {[r.to_dict() for r in failed]}"


def test_nonexistent_evidence_id_fails(workspace: WorkspaceTools, valid_evidence: EvidenceStore) -> None:
    """Referencing an evidence ID not in the store should fail."""
    verifier = CitationVerifier(evidence_store=valid_evidence, workspace=workspace)
    artifacts = {
        "weekly_report.md": "- 某个结论 [E-9999]\n",
    }

    results = verifier.verify(artifacts=artifacts)

    failed = [r for r in results if r.status == "failed"]
    assert len(failed) == 1
    assert "E-9999" in failed[0].message
    assert failed[0].check_id == "citation.exists"


def test_quote_mismatch_fails(workspace: WorkspaceTools) -> None:
    """Evidence with a quote that doesn't match source file should fail."""
    store = EvidenceStore(run_id="test-run")
    store.insert(EvidenceCandidate(
        evidence_id="E-0001",
        source_file="meeting_notes.md",
        quote="this text absolutely does not exist in the file",
        start_line=1,
        end_line=1,
        evidence_type=EvidenceType.CONTEXT,
    ))

    verifier = CitationVerifier(evidence_store=store, workspace=workspace)
    artifacts = {"report.md": "- 结论 [E-0001]\n"}

    results = verifier.verify(artifacts=artifacts)

    failed = [r for r in results if r.status == "failed"]
    assert len(failed) == 1
    assert failed[0].check_id == "citation.quote_match"


def test_source_file_not_found_fails(workspace: WorkspaceTools) -> None:
    """Evidence pointing to a nonexistent file should fail."""
    store = EvidenceStore(run_id="test-run")
    store.insert(EvidenceCandidate(
        evidence_id="E-0001",
        source_file="nonexistent_file.md",
        quote="some quote",
        start_line=1,
        end_line=1,
        evidence_type=EvidenceType.CONTEXT,
    ))

    verifier = CitationVerifier(evidence_store=store, workspace=workspace)
    artifacts = {"report.md": "- 结论 [E-0001]\n"}

    results = verifier.verify(artifacts=artifacts)

    failed = [r for r in results if r.status == "failed"]
    assert len(failed) == 1
    assert failed[0].check_id == "citation.source_exists"


def test_line_range_mismatch_warns(workspace: WorkspaceTools) -> None:
    """Quote exists in file but not in specified line range should warn."""
    store = EvidenceStore(run_id="test-run")
    # This quote is on line 18, but we claim it's on line 1
    store.insert(EvidenceCandidate(
        evidence_id="E-0001",
        source_file="meeting_notes.md",
        quote="支付重试方案截止日期定为本周五（6月27日）。",
        start_line=1,
        end_line=2,
        evidence_type=EvidenceType.DECISION,
    ))

    verifier = CitationVerifier(evidence_store=store, workspace=workspace)
    artifacts = {"report.md": "- 决策 [E-0001]\n"}

    results = verifier.verify(artifacts=artifacts)

    warnings = [r for r in results if r.status == "failed" and r.severity == "warning"]
    assert len(warnings) == 1
    assert warnings[0].check_id == "citation.line_range"


def test_structured_artifact_source_refs(workspace: WorkspaceTools, valid_evidence: EvidenceStore) -> None:
    """Verifier should check source_refs in structured JSON artifacts."""
    verifier = CitationVerifier(evidence_store=valid_evidence, workspace=workspace)
    artifacts = {
        "risks.json": {
            "schema_version": "0.1",
            "risks": [
                {
                    "risk_id": "R-0001",
                    "title": "支付重试延期风险",
                    "source_refs": ["E-0001", "E-9999"],
                }
            ],
        }
    }

    results = verifier.verify(artifacts=artifacts)

    failed = [r for r in results if r.status == "failed"]
    assert len(failed) == 1
    assert "E-9999" in failed[0].message
