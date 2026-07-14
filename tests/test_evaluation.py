"""Tests for loading and running offline evaluation suites."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from workpilot.evaluation import EvalCase, EvalRunner, EvalSuite, load_eval_suite


def test_load_eval_suite_resolves_relative_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "name": "test suite",
                "cases": [
                    {
                        "case_id": "case_1",
                        "workspace": "workspace",
                        "goal": "生成报告"
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    suite = load_eval_suite(suite_path)

    assert suite.cases[0].workspace == workspace.resolve()


def test_eval_case_rejects_invalid_bad_case_id(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="BC-XXX"):
        EvalCase(
            case_id="bad-id",
            workspace=tmp_path,
            goal="生成报告",
            bad_case_ids=["bad-case-1"],
        )


def test_eval_runner_outputs_report(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    suite = EvalSuite(
        name="test suite",
        cases=[
            EvalCase(
                case_id="basic",
                workspace=basic_workspace,
                goal="生成本周项目周报",
                expected_evidence_quotes=[
                    "- 支付重试方案截止日期定为本周五。"
                ],
                bad_case_ids=["BC-001"],
            )
        ],
    )

    report = EvalRunner(provider_name="stub").run(suite, tmp_path / "eval")

    assert report.summary.total_cases == 1
    assert report.summary.task_completion_rate == 1.0
    assert report.summary.evidence_recall == 1.0
    assert report.summary.claim_support_rate == 1.0
    assert report.summary.source_coverage_rate == 1.0
    assert report.summary.evidence_acceptance_rate == 1.0
    assert report.summary.evidence_discard_rate == 0.0
    assert report.summary.claim_source_coverage_rate == 1.0
    assert report.summary.evidence_repair_trigger_rate == 0.0
    assert report.summary.evidence_repair_recovery_rate is None
    assert report.summary.average_repair_model_call_count == 0.0
    assert report.summary.average_repair_token_count == 0.0
    assert report.summary.average_repair_estimated_cost == 0.0
    assert report.cases[0].citation_validity_rate == 1.0
    assert report.cases[0].bad_case_ids == ["BC-001"]
    assert (tmp_path / "eval" / "eval_report.json").exists()


def test_eval_runner_records_missing_workspace_without_stopping(tmp_path: Path) -> None:
    suite = EvalSuite(
        name="test suite",
        cases=[
            EvalCase(
                case_id="missing",
                workspace=tmp_path / "missing",
                goal="生成报告",
            )
        ],
    )

    report = EvalRunner().run(suite, tmp_path / "eval")

    assert report.summary.task_completion_rate == 0.0
    assert report.cases[0].actual_status == "evaluation_error"
    assert report.cases[0].source_coverage_rate == 0.0
    assert "does not exist" in (report.cases[0].error or "")


def test_quality_metrics_measure_only_calls_inside_repair_window() -> None:
    events = [
        {
            "event_type": "evidence_quality_evaluated",
            "data": {
                "passed": False,
                "scanned_source_count": 2,
                "reported_source_count": 2,
                "accepted_evidence_count": 1,
                "candidate_count": 3,
                "discarded_count": 2,
            },
        },
        {"event_type": "evidence_repair_requested", "data": {}},
        {
            "event_type": "model_call_completed",
            "data": {
                "input_tokens": 100,
                "output_tokens": 20,
                "estimated_cost": 0.01,
            },
        },
        {
            "event_type": "evidence_quality_evaluated",
            "data": {
                "passed": True,
                "scanned_source_count": 2,
                "reported_source_count": 2,
                "accepted_evidence_count": 3,
                "candidate_count": 4,
                "discarded_count": 1,
            },
        },
        {
            "event_type": "model_call_completed",
            "data": {
                "input_tokens": 200,
                "output_tokens": 50,
                "estimated_cost": 0.02,
            },
        },
    ]

    metrics = EvalRunner._quality_metrics(events)

    assert metrics["source_coverage_rate"] == 1.0
    assert metrics["evidence_acceptance_rate"] == 0.75
    assert metrics["evidence_discard_rate"] == 0.25
    assert metrics["evidence_repair_trigger_count"] == 1
    assert metrics["evidence_repair_recovered"] is True
    assert metrics["repair_model_call_count"] == 1
    assert metrics["repair_token_count"] == 120
    assert metrics["repair_estimated_cost"] == 0.01


def test_quality_metrics_keep_unknown_repair_cost_null() -> None:
    metrics = EvalRunner._quality_metrics(
        [
            {"event_type": "evidence_repair_requested", "data": {}},
            {
                "event_type": "model_call_completed",
                "data": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "estimated_cost": None,
                },
            },
            {
                "event_type": "evidence_quality_evaluated",
                "data": {
                    "passed": False,
                    "scanned_source_count": 1,
                    "reported_source_count": 1,
                    "accepted_evidence_count": 0,
                    "candidate_count": 1,
                    "discarded_count": 1,
                },
            },
        ]
    )

    assert metrics["repair_estimated_cost"] is None
    assert metrics["evidence_repair_recovered"] is False
