"""Tests for loading and running offline evaluation suites."""

import json
from pathlib import Path

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
            )
        ],
    )

    report = EvalRunner(provider_name="stub").run(suite, tmp_path / "eval")

    assert report.summary.total_cases == 1
    assert report.summary.task_completion_rate == 1.0
    assert report.summary.evidence_recall == 1.0
    assert report.summary.claim_support_rate == 1.0
    assert report.cases[0].citation_validity_rate == 1.0
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
    assert "does not exist" in (report.cases[0].error or "")
