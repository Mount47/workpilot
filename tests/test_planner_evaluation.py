"""Tests for reproducible offline Planner policy evaluation."""

import json
from pathlib import Path

from typer.testing import CliRunner
import pytest
from pydantic import ValidationError

from workpilot.cli import app
from workpilot.evaluation import (
    PlannerEvalCase,
    PlannerEvalRunner,
    PlannerEvalSuite,
    load_planner_eval_suite,
)
from workpilot.planning import PlanDraft


def plan_data(tool_override: str | None = None) -> dict:
    definitions = [
        ("scan_workspace", "workspace.scan", []),
        ("extract_evidence", "evidence.extract", ["scan_workspace"]),
        ("build_claims", tool_override or "claims.build", ["extract_evidence"]),
        ("render_artifacts", "artifacts.render", ["build_claims"]),
        ("verify", "verification.run", ["render_artifacts"]),
        ("finalize", "artifacts.finalize", ["verify"]),
    ]
    return {
        "steps": [
            {
                "step_id": step_id,
                "objective": f"Execute {step_id}.",
                "tool": tool,
                "dependencies": dependencies,
                "expected_output": f"Output of {step_id}.",
                "success_criteria": [f"{step_id} succeeds."],
                "evidence_required": step_id in {
                    "build_claims",
                    "render_artifacts",
                    "verify",
                },
            }
            for step_id, tool, dependencies in definitions
        ]
    }


def planner_suite() -> PlannerEvalSuite:
    return PlannerEvalSuite(
        name="planner policy",
        cases=[
            PlannerEvalCase(
                case_id="valid",
                goal="生成报告",
                draft=PlanDraft.model_validate(plan_data()),
                expected_selected="llm",
            ),
            PlannerEvalCase(
                case_id="invalid_tool",
                goal="生成报告",
                draft=PlanDraft.model_validate(plan_data("danger.execute")),
                expected_selected="deterministic",
                expected_fallback_reason="PlanValidationError",
            ),
            PlannerEvalCase(
                case_id="timeout",
                goal="生成报告",
                provider_error="timeout",
                expected_selected="deterministic",
                expected_fallback_reason="timeout",
            ),
            PlannerEvalCase(
                case_id="invalid_response",
                goal="生成报告",
                provider_error="invalid_response",
                expected_selected="deterministic",
                expected_fallback_reason="invalid_response",
            ),
            PlannerEvalCase(
                case_id="authentication",
                goal="生成报告",
                provider_error="authentication",
                expected_selected="failed",
                expected_fallback_reason="authentication",
            ),
        ],
    )


def test_planner_eval_runner_measures_decisions_safety_and_usage(
    tmp_path: Path,
) -> None:
    report = PlannerEvalRunner().run(planner_suite(), tmp_path / "planner-eval")

    assert report.summary.total_cases == 5
    assert report.summary.decision_accuracy == 1.0
    assert report.summary.plan_validity_rate == 0.8
    assert report.summary.llm_acceptance_rate == 0.2
    assert report.summary.fallback_rate == 0.6
    assert report.summary.failure_rate == 0.2
    assert report.summary.unsafe_acceptance_rate == 0.0
    assert report.summary.average_input_tokens == 60.0
    assert report.summary.average_output_tokens == 12.0
    assert report.summary.average_latency_ms == 6.0
    assert (tmp_path / "planner-eval" / "planner_eval_report.json").exists()


def test_load_planner_eval_suite(tmp_path: Path) -> None:
    suite_path = tmp_path / "planner.json"
    suite_path.write_text(
        json.dumps(planner_suite().model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = load_planner_eval_suite(suite_path)

    assert loaded.name == "planner policy"
    assert loaded.cases[0].draft is not None


def test_planner_eval_cli_outputs_summary(tmp_path: Path) -> None:
    suite_path = tmp_path / "planner.json"
    suite_path.write_text(
        json.dumps(planner_suite().model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    output = tmp_path / "cli-output"

    result = CliRunner().invoke(
        app,
        [
            "planner-eval",
            "--suite",
            str(suite_path),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    assert "Decision accuracy: 100.00%" in result.output
    assert "Unsafe acceptance: 0.00%" in result.output
    assert (output / "planner_eval_report.json").exists()


def test_planner_eval_case_requires_exactly_one_input_source() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        PlannerEvalCase(
            case_id="missing_input",
            goal="生成报告",
            expected_selected="failed",
        )
