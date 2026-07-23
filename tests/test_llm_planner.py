"""Safety and Runtime integration tests for the constrained LLM Planner."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from workpilot.contracts import MissionContract
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
    EvidenceCandidate,
    EvidenceExtractionResult,
    GenerationResult,
    LLMProvider,
    StructuredGenerationResult,
)
from workpilot.providers.errors import ProviderCallError, ProviderErrorType
from workpilot.providers.retry import RetryPolicy
from workpilot.providers.routing import (
    CapabilityRequirement,
    ModelRoute,
    ModelRouter,
    ModelTarget,
    ModelTier,
    TaskType,
)
from workpilot.runtime.runner import Runtime
from workpilot.runtime.budget import BudgetExceededError


def valid_plan_data() -> dict[str, Any]:
    definitions = [
        ("scan_workspace", "workspace.scan", []),
        ("extract_evidence", "evidence.extract", ["scan_workspace"]),
        ("build_claims", "claims.build", ["extract_evidence"]),
        ("render_artifacts", "artifacts.render", ["build_claims"]),
        ("verify", "verification.run", ["render_artifacts"]),
        ("finalize", "artifacts.finalize", ["verify"]),
    ]
    return {
        "steps": [
            {
                "step_id": step_id,
                "objective": f"Execute {step_id} for the mission.",
                "tool": tool,
                "inputs": {},
                "dependencies": dependencies,
                "expected_output": f"Validated output from {step_id}.",
                "success_criteria": [f"{step_id} completes safely."],
                "evidence_required": step_id in {
                    "build_claims",
                    "render_artifacts",
                    "verify",
                },
            }
            for step_id, tool, dependencies in definitions
        ]
    }


class PlannerContractProvider(LLMProvider):
    """Provider that can serve planning, extraction and empty claim analysis."""

    model = "planner-contract"

    def __init__(
        self,
        plan_data: dict[str, Any] | None = None,
        call_error: Exception | None = None,
    ) -> None:
        self.plan_data = plan_data or valid_plan_data()
        self.call_error = call_error

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> GenerationResult:
        return GenerationResult(
            content="{}",
            provider="openai",
            model=self.model,
            input_tokens=3,
            output_tokens=2,
        )

    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> StructuredGenerationResult:
        if self.call_error is not None:
            raise self.call_error
        if response_model is PlanDraft:
            value = response_model.model_validate(self.plan_data)
        else:
            value = response_model.model_validate(
                {
                    "claims": [
                        {
                            "text": (
                                "- PROJ-101: 支付重试逻辑开发 "
                                "(李四, P1, blocked — 等待 API 设计确认)"
                            ),
                            "claim_type": "explicit_fact",
                            "category": "context",
                            "evidence_refs": ["E-0001"],
                        },
                        {
                            "text": "- 张三（产品）",
                            "claim_type": "explicit_fact",
                            "category": "context",
                            "evidence_refs": ["E-0002"],
                        },
                    ]
                }
            )
        return StructuredGenerationResult(
            value=value,
            generations=(self.generate_text(prompt),),
        )

    def extract_evidence_from_file(
        self,
        file_path: str,
        content: str,
        goal: str,
    ) -> EvidenceExtractionResult:
        lines = content.splitlines()
        line_number = next(
            index
            for index, line in enumerate(lines, start=1)
            if line.strip() and not line.startswith("#")
        )
        return EvidenceExtractionResult(
            candidates=[
                EvidenceCandidate(
                    evidence_id="",
                    source_file=file_path,
                    quote=lines[line_number - 1].strip(),
                    start_line=line_number,
                    end_line=line_number,
                )
            ],
            generations=(self.generate_text(goal),),
        )


def contract(tmp_path: Path) -> MissionContract:
    return MissionContract(
        run_id="run-planner",
        goal="生成项目风险报告",
        workspace_root=tmp_path,
    )


def fallback_planner(provider: LLMProvider) -> FallbackPlanner:
    registry = create_default_registry()
    return FallbackPlanner(
        primary=ConstrainedLLMPlanner(provider, registry),
        fallback=DeterministicPlanner(),
        validator=PlanValidator(registry),
        runtime_policy=RuntimePlanPolicy(),
    )


def test_plan_draft_forbids_model_owned_goal_and_execution_state() -> None:
    data = valid_plan_data()
    data["goal"] = "replace the mission"
    data["steps"][0]["status"] = "completed"

    with pytest.raises(ValidationError, match="Extra inputs"):
        PlanDraft.model_validate(data)


def test_valid_llm_plan_uses_system_owned_identity(tmp_path: Path) -> None:
    planner = fallback_planner(PlannerContractProvider())

    plan = planner.create_plan(contract(tmp_path))

    assert plan.created_by == "llm"
    assert plan.plan_id == "plan_run-planner"
    assert plan.goal == "生成项目风险报告"
    assert plan.validated is True
    assert planner.last_decision.selected == "llm"
    registry = create_default_registry()
    assert all(
        step.success_rule_ids == registry.get(step.tool).success_rule_ids
        for step in plan.steps
    )


def test_invalid_tool_falls_back_to_deterministic_plan(tmp_path: Path) -> None:
    data = valid_plan_data()
    data["steps"][2]["tool"] = "danger.execute"
    planner = fallback_planner(PlannerContractProvider(data))

    plan = planner.create_plan(contract(tmp_path))

    assert plan.created_by == "deterministic"
    assert planner.last_decision.selected == "deterministic"
    assert planner.last_decision.fallback_reason == "PlanValidationError"


def test_missing_required_step_falls_back_to_deterministic_plan(
    tmp_path: Path,
) -> None:
    data = valid_plan_data()
    data["steps"] = data["steps"][:-1]
    planner = fallback_planner(PlannerContractProvider(data))

    plan = planner.create_plan(contract(tmp_path))

    assert plan.created_by == "deterministic"
    assert planner.last_decision.fallback_reason == "runtime_plan_compatibility"


def test_known_but_wrong_tool_mapping_is_rejected_by_runtime_policy(
    tmp_path: Path,
) -> None:
    data = valid_plan_data()
    data["steps"][2]["tool"] = "artifacts.render"
    planner = fallback_planner(PlannerContractProvider(data))

    plan = planner.create_plan(contract(tmp_path))

    assert plan.created_by == "deterministic"
    assert planner.last_decision.fallback_reason == "runtime_plan_compatibility"


def test_dependency_cycle_falls_back_to_deterministic_plan(tmp_path: Path) -> None:
    data = valid_plan_data()
    data["steps"][0]["dependencies"] = ["finalize"]
    planner = fallback_planner(PlannerContractProvider(data))

    plan = planner.create_plan(contract(tmp_path))

    assert plan.created_by == "deterministic"
    assert planner.last_decision.fallback_reason == "PlanValidationError"


def test_authentication_error_is_not_hidden_by_fallback(tmp_path: Path) -> None:
    error = ProviderCallError("openai", ProviderErrorType.AUTHENTICATION)
    planner = fallback_planner(PlannerContractProvider(call_error=error))

    with pytest.raises(ProviderCallError) as exc_info:
        planner.create_plan(contract(tmp_path))

    assert exc_info.value.error_type == ProviderErrorType.AUTHENTICATION
    assert planner.last_decision.selected == "failed"


def test_transient_error_can_fall_back_after_provider_policy(tmp_path: Path) -> None:
    error = ProviderCallError("openai", ProviderErrorType.TIMEOUT)
    planner = fallback_planner(PlannerContractProvider(call_error=error))

    plan = planner.create_plan(contract(tmp_path))

    assert plan.created_by == "deterministic"
    assert planner.last_decision.fallback_reason == "timeout"


def test_budget_error_is_not_hidden_by_fallback(tmp_path: Path) -> None:
    error = BudgetExceededError("token", 10, 11)
    planner = fallback_planner(PlannerContractProvider(call_error=error))

    with pytest.raises(BudgetExceededError):
        planner.create_plan(contract(tmp_path))


def test_runtime_uses_planning_route_and_records_planner_trace(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    shared_provider = PlannerContractProvider()
    routes = [
        ModelRoute(
            task_type=task_type,
            targets=[
                ModelTarget(
                    provider="openai",
                    model="planner-contract",
                    tier=ModelTier.BALANCED,
                )
            ],
            minimum_tier=ModelTier.BALANCED,
            requirements=CapabilityRequirement(structured_output=True),
        )
        for task_type in (
            TaskType.PLANNING,
            TaskType.EVIDENCE_EXTRACTION,
            TaskType.ANALYSIS,
            TaskType.REVISION,
        )
    ]
    router = ModelRouter(
        routes,
        retry_policy=RetryPolicy(max_attempts=1),
        provider_factory=lambda _: shared_provider,
        sleep=lambda _: None,
    )
    output = tmp_path / "llm-planner-runtime"
    runtime = Runtime(
        workspace_root=basic_workspace,
        goal="生成项目报告",
        output_dir=output,
        provider=shared_provider,
        model_router=router,
    )

    result = runtime.execute()

    assert result.state.value == "passed"
    plan = json.loads((output / "plan.json").read_text(encoding="utf-8"))
    assert plan["created_by"] == "llm"
    trace = json.loads((output / "trace.json").read_text(encoding="utf-8"))
    decision = next(
        event for event in trace["events"]
        if event["event_type"] == "planner_decision"
    )
    assert decision["data"]["requested"] == "llm"
    assert decision["data"]["selected"] == "llm"
    planning_calls = [
        event for event in trace["events"]
        if event["event_type"] == "model_call_completed"
        and event["data"]["task_type"] == "planning"
    ]
    assert len(planning_calls) == 1
    assert planning_calls[0]["data"]["prompt_version"] == "planner.v1"
    assert planning_calls[0]["data"]["schema_name"] == "PlanDraft"
    assert planning_calls[0]["data"]["total_tokens"] == 5
