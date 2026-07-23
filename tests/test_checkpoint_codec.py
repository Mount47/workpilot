from datetime import datetime, timezone

import pytest

from workpilot.domain import Evidence, ProjectSnapshot, SourceLocator
from workpilot.planning import Plan, PlanStep, StepResultStore, ToolResult
from workpilot.runtime.checkpoint import (
    BudgetCheckpoint,
    CheckpointCodecRegistry,
    CheckpointPayload,
    CheckpointValidationError,
    RuntimeStateCheckpoint,
    VerifyResultCheckpoint,
    canonical_json_bytes,
    decode_checkpoint,
)
from workpilot.verification.base import VerifyResult


def _plan() -> Plan:
    return Plan(
        plan_id="plan_run-1",
        goal="summarize the project",
        created_by="test",
        validated=True,
        created_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        steps=[
            PlanStep(
                step_id="scan_workspace",
                objective="scan",
                tool="workspace.scan",
                expected_output="files",
                success_criteria=["scan succeeds"],
                status="completed",
                attempts=1,
            )
        ],
    )


def _snapshot() -> ProjectSnapshot:
    return ProjectSnapshot(
        project_id="project",
        snapshot_id="snapshot-1",
        as_of=datetime(2026, 7, 22, tzinfo=timezone.utc),
        source_ids=["notes.md"],
    )


def _evidence() -> Evidence:
    return Evidence(
        evidence_id="E-0001",
        locator=SourceLocator.for_file_lines("notes.md", 1, 1, "sha256:abc"),
        quote="Project status is green.",
        created_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )


def _budget() -> BudgetCheckpoint:
    return BudgetCheckpoint(
        steps_used=3,
        steps_limit=30,
        input_tokens=10,
        output_tokens=20,
        token_limit=1000,
        elapsed_seconds=1.25,
        time_limit_seconds=300,
    )


@pytest.mark.parametrize(
    ("tool", "output"),
    [
        ("workspace.scan", ["README.md", "docs/plan.md"]),
        (
            "evidence.extract",
            {
                "evidence_count": 1,
                "discarded_count": 0,
                "provider_error_count": 0,
                "repair_count": 0,
                "locator_repaired_count": 0,
                "source_count": 2,
            },
        ),
        ("claims.build", _snapshot()),
        (
            "artifacts.render",
            {
                "weekly_report.md": "# Report\n",
                "risks.json": {"schema_version": "0.3", "risks": []},
            },
        ),
        (
            "verification.run",
            (
                [VerifyResult("claim.support", "passed", message="supported")],
                [],
            ),
        ),
        ("artifacts.finalize", None),
    ],
)
def test_builtin_tool_results_round_trip(tool: str, output: object) -> None:
    registry = CheckpointCodecRegistry()
    result = ToolResult(
        output=output,
        output_summary={"safe": True},
        evidence_ids=["E-0001"],
    )

    encoded = registry.encode(tool, result)
    restored = registry.decode(encoded)

    assert restored.status == "completed"
    assert restored.output_summary == {"safe": True}
    assert restored.evidence_ids == ["E-0001"]
    if tool == "claims.build":
        assert isinstance(restored.output, ProjectSnapshot)
        assert restored.output == output
    elif tool == "verification.run":
        restored_results, restored_errors = restored.output
        assert [item.to_dict() for item in restored_results] == [
            item.to_dict() for item in output[0]  # type: ignore[index]
        ]
        assert restored_errors == []
    else:
        assert restored.output == output


def test_full_checkpoint_round_trip_is_canonical() -> None:
    registry = CheckpointCodecRegistry()
    scan_result = registry.encode(
        "workspace.scan",
        ToolResult(
            output=["README.md"],
            output_summary={"file_count": 1},
        ),
    )
    payload = CheckpointPayload(
        run_id="run-1",
        sequence=1,
        committed_step_id="scan_workspace",
        plan=_plan(),
        step_results={"scan_workspace": scan_result},
        evidence=[_evidence()],
        runtime_state=RuntimeStateCheckpoint(
            project_snapshot=None,
            rendered_artifacts={},
            verification_results=[
                VerifyResultCheckpoint(
                    check_id="claim.support",
                    status="passed",
                    message="supported",
                )
            ],
            verification_errors=[],
            revision_attempt=1,
            revision_feedback=[],
            budget=_budget(),
        ),
    )

    first = canonical_json_bytes(payload)
    second = canonical_json_bytes(decode_checkpoint(first))

    assert first == second
    assert first.startswith(b'{"committed_step_id"')
    restored = decode_checkpoint(first)
    assert restored.run_id == "run-1"
    assert restored.sequence == 1
    assert restored.evidence[0].evidence_id == "E-0001"


def test_step_result_store_has_controlled_restore_boundary() -> None:
    store = StepResultStore()
    result = ToolResult(output=["README.md"], output_summary={"file_count": 1})
    store.restore({"scan_workspace": result})

    assert store.checkpoint_items() == (("scan_workspace", result),)
    with pytest.raises(ValueError, match="already has"):
        store.restore({"scan_workspace": result})


def test_unknown_tool_and_arbitrary_objects_fail_closed() -> None:
    registry = CheckpointCodecRegistry()

    with pytest.raises(CheckpointValidationError, match="unsupported tool"):
        registry.encode("shell.execute", ToolResult(output="unsafe"))
    with pytest.raises(CheckpointValidationError, match="workspace.scan"):
        registry.encode("workspace.scan", ToolResult(output=object()))


def test_decode_rejects_unknown_schema_and_invalid_identity() -> None:
    with pytest.raises(CheckpointValidationError, match="invalid checkpoint"):
        decode_checkpoint(b'{"schema_version":2}')

    with pytest.raises(ValueError, match="run_id"):
        CheckpointPayload(
            run_id="",
            sequence=1,
            committed_step_id="scan_workspace",
            plan=_plan(),
            runtime_state=RuntimeStateCheckpoint(budget=_budget()),
        )
