"""System-owned deterministic success rules for ToolResults."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from workpilot.planning.models import (
    PlanStep,
    SuccessCriteriaEvaluation,
    SuccessRuleCheck,
)
from workpilot.planning.tools import ToolResult


RuleEvaluator = Callable[[ToolResult], tuple[bool, str]]


DEFAULT_TOOL_SUCCESS_RULE_IDS: dict[str, list[str]] = {
    "workspace.scan": [
        "tool_result.completed",
        "workspace.scan.summary_valid",
    ],
    "evidence.extract": [
        "tool_result.completed",
        "evidence.extract.summary_valid",
    ],
    "claims.build": [
        "tool_result.completed",
        "claims.build.summary_valid",
    ],
    "artifacts.render": [
        "tool_result.completed",
        "artifacts.render.required_outputs_present",
    ],
    "verification.run": [
        "tool_result.completed",
        "verification.run.summary_valid",
    ],
    "artifacts.finalize": [
        "tool_result.completed",
        "artifacts.finalize.persisted",
    ],
}

REQUIRED_ARTIFACT_NAMES = {
    "weekly_report.md",
    "risks.json",
    "action_items.json",
    "project_snapshot.json",
}


class SuccessCriteriaError(RuntimeError):
    """Raised when a ToolResult violates a registered success rule."""

    error_type = "success_criteria_failed"
    retryable = False

    def __init__(self, evaluation: SuccessCriteriaEvaluation) -> None:
        self.evaluation = evaluation
        failed = [check.rule_id for check in evaluation.checks if not check.passed]
        super().__init__("Success criteria failed: " + ", ".join(failed))


@dataclass(frozen=True)
class SuccessRule:
    """One stable rule registered by trusted system code."""

    rule_id: str
    description: str
    evaluator: RuleEvaluator


class SuccessRuleRegistry:
    """Register and execute deterministic rules; unknown IDs fail closed."""

    def __init__(self) -> None:
        self._rules: dict[str, SuccessRule] = {}

    def register(self, rule: SuccessRule) -> None:
        if rule.rule_id in self._rules:
            raise ValueError(f"Success rule {rule.rule_id} is already registered")
        self._rules[rule.rule_id] = rule

    def require_known(self, rule_ids: list[str]) -> None:
        unknown = [rule_id for rule_id in rule_ids if rule_id not in self._rules]
        if unknown:
            raise ValueError("Unknown success rules: " + ", ".join(unknown))

    def evaluate(
        self,
        step: PlanStep,
        result: ToolResult,
    ) -> SuccessCriteriaEvaluation:
        self.require_known(step.success_rule_ids)
        checks = []
        for rule_id in step.success_rule_ids:
            passed, message = self._rules[rule_id].evaluator(result)
            checks.append(
                SuccessRuleCheck(
                    rule_id=rule_id,
                    passed=passed,
                    message=message,
                )
            )
        return SuccessCriteriaEvaluation(
            passed=all(check.passed for check in checks),
            checks=checks,
        )


def create_default_success_rule_registry() -> SuccessRuleRegistry:
    registry = SuccessRuleRegistry()
    for rule in [
        SuccessRule(
            rule_id="tool_result.completed",
            description="ToolResult declares completed execution.",
            evaluator=lambda result: (
                result.status == "completed",
                "ToolResult status is completed."
                if result.status == "completed"
                else "ToolResult status is not completed.",
            ),
        ),
        SuccessRule(
            rule_id="workspace.scan.summary_valid",
            description="Workspace scan reports a non-negative file count.",
            evaluator=lambda result: _non_negative_counts(
                result.output_summary,
                "file_count",
            ),
        ),
        SuccessRule(
            rule_id="evidence.extract.summary_valid",
            description="Evidence extraction reports non-negative counts.",
            evaluator=lambda result: _non_negative_counts(
                result.output_summary,
                "evidence_count",
                "discarded_count",
                "provider_error_count",
            ),
        ),
        SuccessRule(
            rule_id="claims.build.summary_valid",
            description="Claim building reports non-negative claim and source counts.",
            evaluator=lambda result: _non_negative_counts(
                result.output_summary,
                "claim_count",
                "source_count",
            ),
        ),
        SuccessRule(
            rule_id="artifacts.render.required_outputs_present",
            description="Artifact rendering reports every required output name.",
            evaluator=_required_artifacts_present,
        ),
        SuccessRule(
            rule_id="verification.run.summary_valid",
            description="Verification reports internally consistent counts.",
            evaluator=_verification_summary_valid,
        ),
        SuccessRule(
            rule_id="artifacts.finalize.persisted",
            description="Finalization reports persisted status.",
            evaluator=lambda result: (
                result.output_summary.get("status") == "persisted",
                "Finalization reported persisted status."
                if result.output_summary.get("status") == "persisted"
                else "Finalization did not report persisted status.",
            ),
        ),
    ]:
        registry.register(rule)
    return registry


def _non_negative_counts(
    summary: dict[str, Any],
    *field_names: str,
) -> tuple[bool, str]:
    valid = all(
        isinstance(summary.get(name), int)
        and not isinstance(summary.get(name), bool)
        and summary[name] >= 0
        for name in field_names
    )
    return (
        valid,
        "Required count fields are non-negative integers."
        if valid
        else "Required count fields are missing or invalid.",
    )


def _required_artifacts_present(result: ToolResult) -> tuple[bool, str]:
    names = result.output_summary.get("artifact_names")
    valid_names = (
        isinstance(names, list)
        and all(isinstance(name, str) for name in names)
        and REQUIRED_ARTIFACT_NAMES.issubset(names)
    )
    count = result.output_summary.get("artifact_count")
    valid_count = (
        isinstance(count, int)
        and not isinstance(count, bool)
        and isinstance(names, list)
        and count == len(names)
    )
    passed = valid_names and valid_count
    return (
        passed,
        "All required artifact names and a consistent count are present."
        if passed
        else "Required artifact names or artifact count are invalid.",
    )


def _verification_summary_valid(result: ToolResult) -> tuple[bool, str]:
    summary = result.output_summary
    check_count = summary.get("check_count")
    error_count = summary.get("error_count")
    passed = (
        isinstance(check_count, int)
        and not isinstance(check_count, bool)
        and isinstance(error_count, int)
        and not isinstance(error_count, bool)
        and 0 <= error_count <= check_count
    )
    return (
        passed,
        "Verification counts are internally consistent."
        if passed
        else "Verification counts are missing or inconsistent.",
    )
