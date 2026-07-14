"""Run golden workspace cases through the real WorkPilot runtime."""

import json
import re
from pathlib import Path
from statistics import mean

from workpilot.artifacts.writer import ArtifactWriter
from workpilot.evaluation.models import (
    EvalCaseResult,
    EvalSuite,
    EntityAccuracyMetrics,
    EvaluationReport,
    EvaluationSummary,
    PrecisionRecallMetric,
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
            citation_validity = self._optional_ratio(
                valid_citations,
                len(citation_checks),
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
            claim_support = self._optional_ratio(
                supported_claims,
                len(support_checks),
            )
            unsupported_rate = self._optional_ratio(
                unsupported_claims,
                len(support_checks),
            )
            entity_field_checks = [
                check
                for check in checks
                if check["check_id"].startswith("entity.field_")
                and check["check_id"] != "entity.field_unknown"
            ]
            supported_entity_fields = sum(
                check["check_id"] == "entity.field_supported"
                and check["status"] == "passed"
                for check in entity_field_checks
            )
            entity_field_support = self._optional_ratio(
                supported_entity_fields,
                len(entity_field_checks),
            )
            entity_population = self._entity_population(runtime.project_snapshot)
            entity_accuracy = self._entity_accuracy(
                runtime.project_snapshot,
                case,
            )
            source_coverage_checks = [
                check
                for check in checks
                if check["check_id"] == "source.claim_coverage"
            ]
            covered_claim_sources = sum(
                check["status"] == "passed" for check in source_coverage_checks
            )
            claim_source_coverage = self._optional_ratio(
                covered_claim_sources,
                len(source_coverage_checks),
            )
            quality_metrics = self._quality_metrics(trace_events)
            revision_count = sum(
                event["event_type"] == "revision_requested"
                for event in trace_events
            )

            return EvalCaseResult(
                case_id=case.case_id,
                bad_case_ids=case.bad_case_ids,
                expected_status=case.expected_status,
                actual_status=run.state.value,
                task_completed=run.state.value == case.expected_status,
                evidence_precision=evidence_precision,
                evidence_recall=evidence_recall,
                citation_validity_rate=citation_validity,
                claim_support_rate=claim_support,
                unsupported_claim_rate=unsupported_rate,
                entity_field_support_rate=entity_field_support,
                **entity_population,
                entity_accuracy=entity_accuracy,
                source_coverage_rate=quality_metrics["source_coverage_rate"],
                evidence_acceptance_rate=(
                    quality_metrics["evidence_acceptance_rate"]
                ),
                evidence_discard_rate=quality_metrics["evidence_discard_rate"],
                locator_repair_count=quality_metrics["locator_repair_count"],
                claim_source_coverage_rate=claim_source_coverage,
                evidence_repair_trigger_count=(
                    quality_metrics["evidence_repair_trigger_count"]
                ),
                evidence_repair_recovered=(
                    quality_metrics["evidence_repair_recovered"]
                ),
                repair_model_call_count=(
                    quality_metrics["repair_model_call_count"]
                ),
                repair_token_count=quality_metrics["repair_token_count"],
                repair_estimated_cost=quality_metrics["repair_estimated_cost"],
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
    def _optional_ratio(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    @classmethod
    def _quality_metrics(cls, trace_events: list[dict]) -> dict:
        quality_events = [
            event
            for event in trace_events
            if event["event_type"] == "evidence_quality_evaluated"
        ]
        final_quality = quality_events[-1]["data"] if quality_events else {}
        scanned = final_quality.get("scanned_source_count", 0)
        reported = final_quality.get("reported_source_count", 0)
        candidates = final_quality.get("candidate_count", 0)
        accepted = final_quality.get("accepted_evidence_count", 0)
        discarded = final_quality.get("discarded_count", 0)
        locator_repaired = final_quality.get("locator_repaired_count", 0)
        repair_trigger_count = sum(
            event["event_type"] == "evidence_repair_requested"
            for event in trace_events
        )
        repair_recovered = bool(
            repair_trigger_count
            and quality_events
            and quality_events[0]["data"].get("passed") is False
            and final_quality.get("passed") is True
        )

        repair_started = False
        repair_calls: list[dict] = []
        for event in trace_events:
            if event["event_type"] == "evidence_repair_requested":
                repair_started = True
                continue
            if (
                repair_started
                and event["event_type"] == "evidence_quality_evaluated"
            ):
                repair_started = False
                continue
            if repair_started and event["event_type"] == "model_call_completed":
                repair_calls.append(event["data"])

        costs = [call.get("estimated_cost") for call in repair_calls]
        repair_cost = (
            sum(costs)
            if repair_calls and all(cost is not None for cost in costs)
            else 0.0 if not repair_calls else None
        )
        return {
            "source_coverage_rate": cls._ratio(reported, scanned, 1.0),
            "evidence_acceptance_rate": cls._ratio(
                accepted,
                candidates,
                0.0,
            ),
            "evidence_discard_rate": cls._ratio(discarded, candidates, 0.0),
            "locator_repair_count": locator_repaired,
            "evidence_repair_trigger_count": repair_trigger_count,
            "evidence_repair_recovered": repair_recovered,
            "repair_model_call_count": len(repair_calls),
            "repair_token_count": sum(
                call.get("input_tokens", 0) + call.get("output_tokens", 0)
                for call in repair_calls
            ),
            "repair_estimated_cost": repair_cost,
        }

    @classmethod
    def _entity_population(cls, snapshot) -> dict[str, float | None]:
        if snapshot is None:
            return {
                "action_owner_population_rate": None,
                "action_due_date_population_rate": None,
                "risk_owner_population_rate": None,
                "risk_severity_population_rate": None,
                "risk_mitigation_population_rate": None,
            }
        actions = snapshot.action_items
        risks = snapshot.risks
        return {
            "action_owner_population_rate": cls._optional_ratio(
                sum(action.owner.value is not None for action in actions),
                len(actions),
            ),
            "action_due_date_population_rate": cls._optional_ratio(
                sum(action.due_date_text.value is not None for action in actions),
                len(actions),
            ),
            "risk_owner_population_rate": cls._optional_ratio(
                sum(risk.owner.value is not None for risk in risks),
                len(risks),
            ),
            "risk_severity_population_rate": cls._optional_ratio(
                sum(risk.severity.value != "unknown" for risk in risks),
                len(risks),
            ),
            "risk_mitigation_population_rate": cls._optional_ratio(
                sum(risk.mitigation.value is not None for risk in risks),
                len(risks),
            ),
        }

    @classmethod
    def _entity_accuracy(cls, snapshot, case) -> EntityAccuracyMetrics:
        if snapshot is None:
            return EntityAccuracyMetrics()
        action_pairs, unmatched_actions, missing_actions = cls._match_entities(
            snapshot.action_items,
            case.expected_action_items,
        )
        risk_pairs, unmatched_risks, missing_risks = cls._match_entities(
            snapshot.risks,
            case.expected_risks,
        )
        action_metric = cls._metric(
            true_positive=len(action_pairs),
            false_positive=(
                len(unmatched_actions) if case.expected_entities_exhaustive else 0
            ),
            false_negative=len(missing_actions),
            precision_applicable=case.expected_entities_exhaustive,
        )
        risk_metric = cls._metric(
            true_positive=len(risk_pairs),
            false_positive=(
                len(unmatched_risks) if case.expected_entities_exhaustive else 0
            ),
            false_negative=len(missing_risks),
            precision_applicable=case.expected_entities_exhaustive,
        )
        action_owner = cls._score_fields(
            action_pairs,
            missing_actions,
            unmatched_actions if case.expected_entities_exhaustive else [],
            expected_name="owner",
            actual_value=lambda item: item.owner.value,
        )
        action_due = cls._score_fields(
            action_pairs,
            missing_actions,
            unmatched_actions if case.expected_entities_exhaustive else [],
            expected_name="due_date_text",
            actual_value=lambda item: item.due_date_text.value,
        )
        risk_owner = cls._score_fields(
            risk_pairs,
            missing_risks,
            unmatched_risks if case.expected_entities_exhaustive else [],
            expected_name="owner",
            actual_value=lambda item: item.owner.value,
        )
        risk_severity = cls._score_fields(
            risk_pairs,
            missing_risks,
            unmatched_risks if case.expected_entities_exhaustive else [],
            expected_name="severity",
            actual_value=lambda item: (
                None if item.severity.value == "unknown" else item.severity.value
            ),
        )
        risk_mitigation = cls._score_fields(
            risk_pairs,
            missing_risks,
            unmatched_risks if case.expected_entities_exhaustive else [],
            expected_name="mitigation",
            actual_value=lambda item: item.mitigation.value,
        )
        return EntityAccuracyMetrics(
            action_item=action_metric,
            risk=risk_metric,
            action_owner=action_owner,
            action_due_date=action_due,
            risk_owner=risk_owner,
            risk_severity=risk_severity,
            risk_mitigation=risk_mitigation,
        )

    @classmethod
    def _match_entities(cls, actual_items, expected_items):
        remaining = list(actual_items)
        pairs = []
        missing = []
        for expected in expected_items:
            expected_key = cls._entity_key(expected.claim_text)
            matched_index = next(
                (
                    index
                    for index, actual in enumerate(remaining)
                    if cls._entity_key(actual.description) == expected_key
                ),
                None,
            )
            if matched_index is None:
                missing.append(expected)
            else:
                pairs.append((remaining.pop(matched_index), expected))
        return pairs, remaining, missing

    @staticmethod
    def _entity_key(text: str) -> str:
        return re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", text, count=1).strip()

    @classmethod
    def _score_fields(
        cls,
        pairs,
        missing_expected,
        unmatched_actual,
        *,
        expected_name: str,
        actual_value,
    ) -> PrecisionRecallMetric:
        true_positive = false_positive = false_negative = 0
        labelled_count = 0
        for actual, expected in pairs:
            label = getattr(expected, expected_name)
            if label is None:
                continue
            labelled_count += 1
            predicted = actual_value(actual)
            if predicted == label.value and label.value is not None:
                true_positive += 1
            elif predicted is not None and label.value is None:
                false_positive += 1
            elif predicted is None and label.value is not None:
                false_negative += 1
            elif predicted != label.value:
                false_positive += 1
                false_negative += 1
        for expected in missing_expected:
            label = getattr(expected, expected_name)
            if label is not None:
                labelled_count += 1
                if label.value is not None:
                    false_negative += 1
        false_positive += sum(
            actual_value(actual) is not None for actual in unmatched_actual
        )
        if labelled_count == 0 and not unmatched_actual:
            return PrecisionRecallMetric()
        return cls._metric(true_positive, false_positive, false_negative)

    @classmethod
    def _metric(
        cls,
        true_positive: int,
        false_positive: int,
        false_negative: int,
        *,
        precision_applicable: bool = True,
    ) -> PrecisionRecallMetric:
        precision = (
            cls._optional_ratio(true_positive, true_positive + false_positive)
            if precision_applicable
            else None
        )
        recall = cls._optional_ratio(
            true_positive,
            true_positive + false_negative,
        )
        return PrecisionRecallMetric(
            precision=precision,
            recall=recall,
            true_positive=true_positive,
            false_positive=false_positive,
            false_negative=false_negative,
        )

    @staticmethod
    def _failed_case(case, error: str) -> EvalCaseResult:
        return EvalCaseResult(
            case_id=case.case_id,
            bad_case_ids=case.bad_case_ids,
            expected_status=case.expected_status,
            actual_status="evaluation_error",
            task_completed=False,
            evidence_precision=0.0,
            evidence_recall=0.0,
            citation_validity_rate=None,
            claim_support_rate=None,
            unsupported_claim_rate=None,
            entity_field_support_rate=None,
            action_owner_population_rate=None,
            action_due_date_population_rate=None,
            risk_owner_population_rate=None,
            risk_severity_population_rate=None,
            risk_mitigation_population_rate=None,
            entity_accuracy=EntityAccuracyMetrics(),
            source_coverage_rate=0.0,
            evidence_acceptance_rate=0.0,
            evidence_discard_rate=0.0,
            locator_repair_count=0,
            claim_source_coverage_rate=None,
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
        repair_cases = [
            result
            for result in results
            if result.evidence_repair_trigger_count > 0
        ]
        repair_recovery_rate = (
            mean(result.evidence_repair_recovered for result in repair_cases)
            if repair_cases
            else None
        )
        known_repair_costs = [
            result.repair_estimated_cost
            for result in results
            if result.repair_estimated_cost is not None
        ]
        return EvaluationSummary(
            total_cases=len(results),
            task_completion_rate=mean(result.task_completed for result in results),
            evidence_precision=mean(result.evidence_precision for result in results),
            evidence_recall=mean(result.evidence_recall for result in results),
            citation_validity_rate=EvalRunner._optional_mean(
                result.citation_validity_rate for result in results
            ),
            claim_support_rate=EvalRunner._optional_mean(
                result.claim_support_rate for result in results
            ),
            unsupported_claim_rate=EvalRunner._optional_mean(
                result.unsupported_claim_rate for result in results
            ),
            entity_field_support_rate=EvalRunner._optional_mean(
                result.entity_field_support_rate for result in results
            ),
            action_owner_population_rate=EvalRunner._optional_mean(
                result.action_owner_population_rate for result in results
            ),
            action_due_date_population_rate=EvalRunner._optional_mean(
                result.action_due_date_population_rate for result in results
            ),
            risk_owner_population_rate=EvalRunner._optional_mean(
                result.risk_owner_population_rate for result in results
            ),
            risk_severity_population_rate=EvalRunner._optional_mean(
                result.risk_severity_population_rate for result in results
            ),
            risk_mitigation_population_rate=EvalRunner._optional_mean(
                result.risk_mitigation_population_rate for result in results
            ),
            entity_accuracy=EvalRunner._summarize_entity_accuracy(results),
            source_coverage_rate=mean(
                result.source_coverage_rate for result in results
            ),
            evidence_acceptance_rate=mean(
                result.evidence_acceptance_rate for result in results
            ),
            evidence_discard_rate=mean(
                result.evidence_discard_rate for result in results
            ),
            average_locator_repair_count=mean(
                result.locator_repair_count for result in results
            ),
            claim_source_coverage_rate=EvalRunner._optional_mean(
                result.claim_source_coverage_rate for result in results
            ),
            evidence_repair_trigger_rate=mean(
                result.evidence_repair_trigger_count > 0 for result in results
            ),
            evidence_repair_recovery_rate=repair_recovery_rate,
            average_repair_model_call_count=mean(
                result.repair_model_call_count for result in results
            ),
            average_repair_token_count=mean(
                result.repair_token_count for result in results
            ),
            average_repair_estimated_cost=(
                mean(known_repair_costs) if known_repair_costs else None
            ),
            revision_recovery_rate=recovery_rate,
            average_revision_count=mean(result.revision_count for result in results),
        )

    @staticmethod
    def _optional_mean(values) -> float | None:
        known = [value for value in values if value is not None]
        return mean(known) if known else None

    @classmethod
    def _summarize_entity_accuracy(
        cls,
        results: list[EvalCaseResult],
    ) -> EntityAccuracyMetrics:
        fields = EntityAccuracyMetrics.model_fields
        aggregated = {}
        for field_name in fields:
            metrics = [getattr(result.entity_accuracy, field_name) for result in results]
            precision_applicable = any(
                metric.precision is not None for metric in metrics
            )
            aggregated[field_name] = cls._metric(
                sum(metric.true_positive for metric in metrics),
                sum(metric.false_positive for metric in metrics),
                sum(metric.false_negative for metric in metrics),
                precision_applicable=precision_applicable,
            )
        return EntityAccuracyMetrics(**aggregated)
