"""Offline Planner policy evaluation through the real validation stack."""

import json
from pathlib import Path
from statistics import mean

from pydantic import BaseModel

from workpilot.artifacts.writer import ArtifactWriter
from workpilot.contracts import MissionContract
from workpilot.evaluation.models import (
    PlannerEvalCase,
    PlannerEvalCaseResult,
    PlannerEvalSuite,
    PlannerEvaluationReport,
    PlannerEvaluationSummary,
)
from workpilot.planning import (
    ConstrainedLLMPlanner,
    DeterministicPlanner,
    FallbackPlanner,
    PlanDraft,
    PlanValidator,
    RuntimePlanPolicy,
    create_default_registry,
)
from workpilot.providers.base import (
    EvidenceExtractionResult,
    GenerationResult,
    LLMProvider,
    ProviderResponseError,
    StructuredGenerationResult,
)
from workpilot.providers.errors import ProviderCallError, ProviderErrorType


def load_planner_eval_suite(path: Path) -> PlannerEvalSuite:
    """Load a versioned UTF-8 Planner evaluation suite."""
    data = json.loads(path.resolve().read_text(encoding="utf-8"))
    return PlannerEvalSuite.model_validate(data)


class StaticPlannerEvalProvider(LLMProvider):
    """Replay one typed Planner case without network access."""

    def __init__(self, case: PlannerEvalCase) -> None:
        self.case = case

    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> StructuredGenerationResult:
        del prompt, system_prompt, temperature
        generation = self._generation()
        if self.case.provider_error == "invalid_response":
            raise ProviderResponseError("offline invalid response", (generation,))
        if self.case.provider_error is not None:
            error_type = ProviderErrorType(self.case.provider_error)
            raise ProviderCallError("planner-eval", error_type)
        if response_model is not PlanDraft or self.case.draft is None:
            raise TypeError("planner evaluation case requires a PlanDraft")
        return StructuredGenerationResult(
            value=self.case.draft,
            generations=(generation,),
        )

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> GenerationResult:
        del prompt, system_prompt, temperature
        return self._generation()

    def extract_evidence_from_file(
        self,
        file_path: str,
        content: str,
        goal: str,
    ) -> EvidenceExtractionResult:
        del file_path, content, goal
        return EvidenceExtractionResult(candidates=[])

    def _generation(self) -> GenerationResult:
        return GenerationResult(
            content="",
            provider="planner-eval",
            model="static-plan-draft",
            input_tokens=self.case.input_tokens,
            output_tokens=self.case.output_tokens,
            latency_ms=self.case.latency_ms,
            task_type="planning",
            prompt_version="planner.v1",
            schema_name="PlanDraft",
            schema_version="plan.v1",
        )


class PlannerEvalRunner:
    """Evaluate Planner decisions, safety rejection and deterministic fallback."""

    def run(
        self,
        suite: PlannerEvalSuite,
        output_dir: Path,
    ) -> PlannerEvaluationReport:
        output_dir = output_dir.resolve()
        results = [
            self._run_case(case, output_dir / "workspaces" / case.case_id)
            for case in suite.cases
        ]
        report = PlannerEvaluationReport(
            suite_name=suite.name,
            suite_version=suite.version,
            summary=self._summarize(results),
            cases=results,
        )
        ArtifactWriter(output_dir).write_json(
            "planner_eval_report.json",
            report.model_dump(mode="json"),
        )
        return report

    @staticmethod
    def _run_case(case: PlannerEvalCase, workspace: Path) -> PlannerEvalCaseResult:
        registry = create_default_registry()
        planner = FallbackPlanner(
            primary=ConstrainedLLMPlanner(
                provider=StaticPlannerEvalProvider(case),
                registry=registry,
            ),
            fallback=DeterministicPlanner(),
            validator=PlanValidator(registry),
            runtime_policy=RuntimePlanPolicy(),
        )
        contract = MissionContract(
            run_id=f"planner-eval-{case.case_id}",
            goal=case.goal,
            workspace_root=workspace,
        )
        plan = None
        try:
            plan = planner.create_plan(contract)
            actual_selected = planner.last_decision.selected
            fallback_reason = planner.last_decision.fallback_reason
        except ProviderCallError as exc:
            actual_selected = "failed"
            fallback_reason = exc.error_type.value

        generations = planner.get_model_calls()
        input_tokens = sum(call.input_tokens for call in generations)
        output_tokens = sum(call.output_tokens for call in generations)
        latency_ms = sum(call.latency_ms for call in generations)
        decision_correct = actual_selected == case.expected_selected
        if case.expected_fallback_reason is not None:
            decision_correct = (
                decision_correct
                and fallback_reason == case.expected_fallback_reason
            )
        unsafe_accepted = case.expected_selected != "llm" and actual_selected == "llm"
        return PlannerEvalCaseResult(
            case_id=case.case_id,
            expected_selected=case.expected_selected,
            actual_selected=actual_selected,
            decision_correct=decision_correct,
            plan_valid=bool(plan is not None and plan.validated),
            fallback_used=actual_selected == "deterministic",
            unsafe_plan_accepted=unsafe_accepted,
            fallback_reason=fallback_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _summarize(
        results: list[PlannerEvalCaseResult],
    ) -> PlannerEvaluationSummary:
        return PlannerEvaluationSummary(
            total_cases=len(results),
            decision_accuracy=mean(result.decision_correct for result in results),
            plan_validity_rate=mean(result.plan_valid for result in results),
            llm_acceptance_rate=mean(
                result.actual_selected == "llm" for result in results
            ),
            fallback_rate=mean(result.fallback_used for result in results),
            failure_rate=mean(
                result.actual_selected == "failed" for result in results
            ),
            unsafe_acceptance_rate=mean(
                result.unsafe_plan_accepted for result in results
            ),
            average_input_tokens=mean(result.input_tokens for result in results),
            average_output_tokens=mean(result.output_tokens for result in results),
            average_latency_ms=mean(result.latency_ms for result in results),
        )
