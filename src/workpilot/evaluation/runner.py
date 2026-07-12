"""Run golden workspace cases through the real WorkPilot runtime."""

import json
from pathlib import Path
from statistics import mean

from workpilot.artifacts.writer import ArtifactWriter
from workpilot.evaluation.models import (
    EvalCaseResult,
    EvalSuite,
    EvaluationReport,
    EvaluationSummary,
)
from workpilot.providers.registry import get_provider
from workpilot.runtime.runner import Runtime


def load_eval_suite(path: Path) -> EvalSuite:
    """Load a JSON suite and resolve case workspaces relative to the suite."""
    suite_path = path.resolve()
    data = json.loads(suite_path.read_text(encoding="utf-8"))
    suite = EvalSuite.model_validate(data)
    resolved_cases = [
        case.model_copy(
            update={
                "workspace": (
                    case.workspace
                    if case.workspace.is_absolute()
                    else (suite_path.parent / case.workspace).resolve()
                )
            }
        )
        for case in suite.cases
    ]
    return suite.model_copy(update={"cases": resolved_cases})


class EvalRunner:
    """Execute every case independently and aggregate deterministic metrics."""

    def __init__(
        self,
        provider_name: str = "stub",
        provider_kwargs: dict | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.provider_kwargs = provider_kwargs or {}

    def run(self, suite: EvalSuite, output_dir: Path) -> EvaluationReport:
        output_dir = output_dir.resolve()
        results = [
            self._run_case(case, output_dir / "cases" / case.case_id)
            for case in suite.cases
        ]
        summary = self._summarize(results)
        report = EvaluationReport(
            suite_name=suite.name,
            suite_version=suite.version,
            provider=self.provider_name,
            summary=summary,
            cases=results,
        )
        ArtifactWriter(output_dir).write_json(
            "eval_report.json",
            report.model_dump(mode="json"),
        )
        return report

    def _run_case(self, case, case_output: Path) -> EvalCaseResult:
        if not case.workspace.exists() or not case.workspace.is_dir():
            return self._failed_case(
                case,
                f"Workspace does not exist or is not a directory: {case.workspace}",
            )

        try:
            provider = get_provider(self.provider_name, **dict(self.provider_kwargs))
            runtime = Runtime(
                workspace_root=case.workspace,
                goal=case.goal,
                output_dir=case_output,
                provider=provider,
            )
            run = runtime.execute()
            checks = self._load_checks(case_output)
            trace_events = runtime.trace.export()["events"]

            expected_quotes = set(case.expected_evidence_quotes)
            actual_quotes = {
                evidence.quote for evidence in runtime.evidence_store.list_all()
            }
            true_positives = len(expected_quotes & actual_quotes)
            evidence_precision = self._ratio(
                true_positives,
                len(actual_quotes),
                empty_value=1.0 if not expected_quotes else 0.0,
            )
            evidence_recall = self._ratio(
                true_positives,
                len(expected_quotes),
                empty_value=1.0,
            )

            citation_checks = [
                check for check in checks if check["check_id"].startswith("citation.")
            ]
            valid_citations = sum(
                check["status"] == "passed" for check in citation_checks
            )
            citation_validity = self._ratio(
                valid_citations,
                len(citation_checks),
                empty_value=1.0,
            )

            support_checks = [
                check
                for check in checks
                if check["check_id"].startswith("claim.")
                and check["check_id"] != "claim.unknown"
            ]
            supported_claims = sum(
                check["check_id"] == "claim.supported"
                and check["status"] == "passed"
                for check in support_checks
            )
            unsupported_claims = sum(
                check["status"] == "failed" for check in support_checks
            )
            semantic_unverified = sum(
                check["check_id"] == "claim.semantic_support_pending"
                for check in support_checks
            )
            claim_support = self._ratio(
                supported_claims,
                len(support_checks),
                empty_value=1.0,
            )
            unsupported_rate = self._ratio(
                unsupported_claims,
                len(support_checks),
                empty_value=0.0,
            )
            revision_count = sum(
                event["event_type"] == "revision_requested"
                for event in trace_events
            )

            return EvalCaseResult(
                case_id=case.case_id,
                expected_status=case.expected_status,
                actual_status=run.state.value,
                task_completed=run.state.value == case.expected_status,
                evidence_precision=evidence_precision,
                evidence_recall=evidence_recall,
                citation_validity_rate=citation_validity,
                claim_support_rate=claim_support,
                unsupported_claim_rate=unsupported_rate,
                semantic_unverified_count=semantic_unverified,
                revision_count=revision_count,
                error=run.failure_reason,
            )
        except Exception as exc:
            return self._failed_case(case, str(exc))

    @staticmethod
    def _load_checks(case_output: Path) -> list[dict]:
        report_path = case_output / "verification_report.json"
        if not report_path.exists():
            return []
        report = json.loads(report_path.read_text(encoding="utf-8"))
        return report.get("checks", [])

    @staticmethod
    def _ratio(numerator: int, denominator: int, empty_value: float) -> float:
        return numerator / denominator if denominator else empty_value

    @staticmethod
    def _failed_case(case, error: str) -> EvalCaseResult:
        return EvalCaseResult(
            case_id=case.case_id,
            expected_status=case.expected_status,
            actual_status="evaluation_error",
            task_completed=False,
            evidence_precision=0.0,
            evidence_recall=0.0,
            citation_validity_rate=0.0,
            claim_support_rate=0.0,
            unsupported_claim_rate=0.0,
            error=error,
        )

    @staticmethod
    def _summarize(results: list[EvalCaseResult]) -> EvaluationSummary:
        revised_cases = [result for result in results if result.revision_count > 0]
        recovered = sum(
            result.task_completed and result.actual_status == "passed"
            for result in revised_cases
        )
        recovery_rate = (
            recovered / len(revised_cases) if revised_cases else None
        )
        return EvaluationSummary(
            total_cases=len(results),
            task_completion_rate=mean(result.task_completed for result in results),
            evidence_precision=mean(result.evidence_precision for result in results),
            evidence_recall=mean(result.evidence_recall for result in results),
            citation_validity_rate=mean(
                result.citation_validity_rate for result in results
            ),
            claim_support_rate=mean(result.claim_support_rate for result in results),
            unsupported_claim_rate=mean(
                result.unsupported_claim_rate for result in results
            ),
            revision_recovery_rate=recovery_rate,
            average_revision_count=mean(result.revision_count for result in results),
        )
