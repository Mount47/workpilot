"""Tests for loading and running offline evaluation suites."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from workpilot.domain import (
    ActionItem,
    Claim,
    ClaimCategory,
    ClaimType,
    ProjectSnapshot,
    Risk,
    RiskLevel,
    SupportedText,
)
from workpilot.evaluation import (
    EvalCase,
    EvalRunner,
    EvalSuite,
    ExpectedActionItem,
    ExpectedField,
    ExpectedRisk,
    load_eval_suite,
)


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


def test_expected_field_distinguishes_unlabelled_from_explicit_null(
    tmp_path: Path,
) -> None:
    case = EvalCase(
        case_id="field_labels",
        workspace=tmp_path,
        goal="生成报告",
        expected_action_items=[
            ExpectedActionItem(
                claim_text="行动项",
                owner=ExpectedField(value=None),
            )
        ],
    )

    assert case.expected_action_items[0].owner is not None
    assert case.expected_action_items[0].owner.value is None
    assert case.expected_action_items[0].due_date_text is None


def test_eval_case_rejects_duplicate_entity_anchors(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="anchors must be unique"):
        EvalCase(
            case_id="duplicate_entities",
            workspace=tmp_path,
            goal="生成报告",
            expected_action_items=[
                ExpectedActionItem(claim_text="同一行动项"),
                ExpectedActionItem(claim_text="同一行动项"),
            ],
        )


def test_expected_risk_rejects_non_normalized_severity() -> None:
    with pytest.raises(ValidationError, match="normalized level"):
        ExpectedRisk(
            claim_text="风险",
            severity=ExpectedField(value="P1"),
        )


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
    assert report.summary.average_locator_repair_count == 0.0
    assert report.summary.claim_source_coverage_rate == 1.0
    assert report.summary.entity_field_support_rate is None
    assert report.summary.action_owner_population_rate == 0.0
    assert report.summary.action_due_date_population_rate == 0.0
    assert report.summary.risk_owner_population_rate == 0.0
    assert report.summary.risk_severity_population_rate == 0.0
    assert report.summary.risk_mitigation_population_rate == 0.0
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
    assert report.cases[0].citation_validity_rate is None
    assert report.cases[0].claim_support_rate is None
    assert report.cases[0].claim_source_coverage_rate is None
    assert report.cases[0].entity_field_support_rate is None
    assert report.cases[0].action_owner_population_rate is None
    assert "does not exist" in (report.cases[0].error or "")


def test_optional_metrics_do_not_treat_zero_checks_as_perfect() -> None:
    assert EvalRunner._optional_ratio(0, 0) is None
    assert EvalRunner._optional_mean([None, None]) is None
    assert EvalRunner._optional_mean([None, 0.5, 1.0]) == 0.75


def test_entity_accuracy_scores_golden_matches_fields_and_missing_entities(
    tmp_path: Path,
) -> None:
    action_text = "- 李四：输出 API 文档。"
    risk_text = "- 发布窗口存在风险。"
    snapshot = ProjectSnapshot(
        project_id="project-1",
        snapshot_id="snapshot-1",
        claims=[
            Claim(
                claim_id="C-0001",
                text=action_text,
                claim_type=ClaimType.EXPLICIT_FACT,
                category=ClaimCategory.ACTION_ITEM,
                evidence_refs=["E-0001"],
            ),
            Claim(
                claim_id="C-0002",
                text=risk_text,
                claim_type=ClaimType.EXPLICIT_FACT,
                category=ClaimCategory.RISK,
                evidence_refs=["E-0002"],
            ),
        ],
        action_items=[
            ActionItem(
                action_id="A-0001",
                claim_id="C-0001",
                description=action_text,
                owner=SupportedText(value="李四", evidence_refs=["E-0001"]),
                due_date_text=SupportedText(
                    value="本周五",
                    evidence_refs=["E-0001"],
                ),
                source_refs=["E-0001"],
            )
        ],
        risks=[
            Risk(
                risk_id="R-0001",
                claim_id="C-0002",
                description=risk_text,
                owner=SupportedText(value="王五", evidence_refs=["E-0002"]),
                severity=RiskLevel.HIGH,
                severity_evidence_refs=["E-0002"],
                source_refs=["E-0002"],
            )
        ],
    )
    case = EvalCase(
        case_id="entity_metrics",
        workspace=tmp_path,
        goal="生成报告",
        expected_entities_exhaustive=True,
        expected_action_items=[
            ExpectedActionItem(
                claim_text="李四：输出 API 文档。",
                owner=ExpectedField(value="李四"),
                due_date_text=ExpectedField(value=None),
            ),
            ExpectedActionItem(
                claim_text="张三：执行回归。",
                owner=ExpectedField(value="张三"),
            ),
        ],
        expected_risks=[
            ExpectedRisk(
                claim_text="发布窗口存在风险。",
                owner=ExpectedField(value="王五"),
                severity=ExpectedField(value=None),
            )
        ],
    )

    metrics = EvalRunner._entity_accuracy(snapshot, case)

    assert metrics.action_item.precision == 1.0
    assert metrics.action_item.recall == 0.5
    assert metrics.action_owner.precision == 1.0
    assert metrics.action_owner.recall == 0.5
    assert metrics.action_due_date.false_positive == 1
    assert metrics.risk_owner.precision == 1.0
    assert metrics.risk_owner.recall == 1.0
    assert metrics.risk_severity.false_positive == 1


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
                "locator_repaired_count": 2,
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
    assert metrics["locator_repair_count"] == 2
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
