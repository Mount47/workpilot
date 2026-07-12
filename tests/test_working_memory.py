"""Tests for run-scoped Working Memory and its safe export boundary."""

import json
from pathlib import Path

import pytest

from workpilot.contracts import MissionContract
from workpilot.domain import (
    Claim,
    ClaimCategory,
    ClaimType,
    Evidence,
    ProjectSnapshot,
    SourceLocator,
)
from workpilot.evidence.store import EvidenceStore
from workpilot.memory import MemoryStepStatus, WorkingMemory
from workpilot.verification.base import VerifyResult


def _memory(tmp_path: Path) -> WorkingMemory:
    contract = MissionContract(
        run_id="run-1",
        goal="生成报告",
        workspace_root=tmp_path,
    )
    return WorkingMemory(contract)


def test_working_memory_tracks_run_state_without_exporting_quotes(
    tmp_path: Path,
) -> None:
    memory = _memory(tmp_path)
    memory.set_run_status("running")
    memory.start_step("step_0001", "retrieve.evidence", 1)
    memory.add_evidence(
        Evidence(
            evidence_id="E-0001",
            locator=SourceLocator.for_file_lines("meeting.md", 1, 1),
            quote="这是不应出现在安全快照中的原文。",
            evidence_type="decision",
        )
    )
    memory.set_project_snapshot(
        ProjectSnapshot(
            project_id="project-1",
            snapshot_id="snapshot-1",
            claims=[
                Claim(
                    claim_id="C-0001",
                    text="这是不应出现在安全快照中的原文。",
                    claim_type=ClaimType.EXPLICIT_FACT,
                    category=ClaimCategory.DECISION,
                    evidence_refs=["E-0001"],
                )
            ],
        )
    )
    memory.complete_step("step_0001")
    memory.record_artifacts(["weekly_report.md"])
    memory.update_budget({"steps": {"used": 1, "limit": 30}})
    memory.set_run_status("passed")

    snapshot = memory.export()
    encoded = json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False)

    assert snapshot.run_status == "passed"
    assert snapshot.evidence_ids == ["E-0001"]
    assert snapshot.claim_ids == ["C-0001"]
    assert snapshot.steps[0].status == MemoryStepStatus.COMPLETED
    assert "weekly_report.md" in snapshot.artifact_names
    assert "这是不应出现在安全快照中的原文" not in encoded


def test_working_memory_records_safe_verification_and_revision(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    memory.record_verification(
        [
            VerifyResult(
                check_id="claim.evidence_exists",
                status="failed",
                severity="error",
                location="claims.C-0001",
                message="sensitive full error details",
            )
        ]
    )
    memory.request_revision("private corrective feedback")

    snapshot = memory.export()
    encoded = json.dumps(snapshot.model_dump(mode="json"))

    assert snapshot.verification.error_count == 1
    assert snapshot.revision_count == 1
    assert snapshot.has_revision_feedback is True
    assert "private corrective feedback" not in encoded
    assert "sensitive full error details" not in encoded


def test_working_memory_rejects_duplicate_or_finished_step(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    memory.start_step("step_0001", "planning", 1)

    with pytest.raises(ValueError, match="already contains"):
        memory.start_step("step_0001", "planning", 1)

    memory.complete_step("step_0001")
    with pytest.raises(ValueError, match="already completed"):
        memory.complete_step("step_0001")


def test_working_memory_rejects_mismatched_evidence_store(tmp_path: Path) -> None:
    contract = MissionContract(
        run_id="run-1",
        goal="生成报告",
        workspace_root=tmp_path,
    )

    with pytest.raises(ValueError, match="run_id must match"):
        WorkingMemory(contract, EvidenceStore(run_id="run-2"))
