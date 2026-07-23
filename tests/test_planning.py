"""Tests for structured plan validation and deterministic execution."""

from pathlib import Path
import json

import pytest
from pydantic import BaseModel

from workpilot.analysis.claim_builder import ClaimDraft, ClaimDraftCollection
from workpilot.analysis.entity_builder import EntityProjectionDraft
from workpilot.contracts import MissionContract
from workpilot.domain import ClaimCategory, ClaimType
from workpilot.planning import (
    DeterministicPlanner,
    Plan,
    PlanExecutor,
    PlanStep,
    PlanStepStatus,
    PlanValidationError,
    PlanValidator,
    create_default_registry,
)
from workpilot.providers.base import (
    EvidenceCandidate,
    EvidenceExtractionResult,
    GenerationResult,
    LLMProvider,
    ProviderResponseError,
    StructuredGenerationResult,
)
from workpilot.runtime.runner import Runtime


class RevisionProvider(LLMProvider):
    """First emits a missing ref, then repairs it from verifier feedback."""

    def __init__(self) -> None:
        self.claim_calls = 0

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> GenerationResult:
        return GenerationResult(content="", provider="revision", model="revision")

    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> StructuredGenerationResult:
        if response_model is EntityProjectionDraft:
            return StructuredGenerationResult(
                value=EntityProjectionDraft(),
                generations=(
                    GenerationResult(
                        content="{}",
                        provider="revision",
                        model="revision",
                    ),
                ),
            )
        self.claim_calls += 1
        evidence_ref = "E-9999" if self.claim_calls == 1 else "E-0001"
        return StructuredGenerationResult(
            value=ClaimDraftCollection(
                claims=[
                    ClaimDraft(
                        text="# Issue 列表",
                        claim_type=ClaimType.EXPLICIT_FACT,
                        category=ClaimCategory.CONTEXT,
                        evidence_refs=[evidence_ref],
                    ),
                    ClaimDraft(
                        text="# 周会纪要 2026-06-23",
                        claim_type=ClaimType.EXPLICIT_FACT,
                        category=ClaimCategory.CONTEXT,
                        evidence_refs=["E-0002"],
                    ),
                ]
            ),
            generations=(
                GenerationResult(
                    content="{}",
                    provider="revision",
                    model="revision",
                ),
            ),
        )

    def extract_evidence_from_file(
        self,
        file_path: str,
        content: str,
        goal: str,
    ) -> EvidenceExtractionResult:
        first_line = content.splitlines()[0]
        return EvidenceExtractionResult(
            candidates=[
                EvidenceCandidate(
                    evidence_id="",
                    source_file=file_path,
                    quote=first_line,
                    start_line=1,
                    end_line=1,
                )
            ]
        )


class FailingRevisionProvider(RevisionProvider):
    """Build once, then fail while attempting the requested revision."""

    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> StructuredGenerationResult:
        if response_model is EntityProjectionDraft:
            return super().generate_structured(
                prompt,
                response_model,
                system_prompt,
                temperature,
            )
        if self.claim_calls == 0:
            return super().generate_structured(
                prompt,
                response_model,
                system_prompt,
                temperature,
            )
        self.claim_calls += 1
        generation = GenerationResult(
            content="private invalid response",
            provider="revision",
            model="revision",
        )
        raise ProviderResponseError(
            "safe structured response failure",
            (generation,),
        )


def _contract(tmp_path: Path, max_steps: int = 30) -> MissionContract:
    return MissionContract(
        run_id="run-1",
        goal="生成项目报告",
        workspace_root=tmp_path,
        max_steps=max_steps,
    )


def test_default_plan_passes_all_deterministic_checks(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    plan = DeterministicPlanner().create_plan(contract)

    PlanValidator(create_default_registry()).validate(plan, contract)

    assert plan.validated is True
    assert len(plan.steps) == 6


def test_plan_validator_rejects_unknown_and_unauthorized_tool(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    plan = Plan(
        plan_id="plan-1",
        goal=contract.goal,
        created_by="test",
        steps=[
            PlanStep(
                step_id="bad_tool",
                objective="Use an unknown tool.",
                tool="danger.execute",
                expected_output="Nothing.",
                success_criteria=["Never runs."],
            )
        ],
    )

    with pytest.raises(PlanValidationError, match="unknown tool"):
        PlanValidator(create_default_registry()).validate(plan, contract)


def test_plan_validator_rejects_cycle_and_insufficient_budget(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path, max_steps=2)
    plan = Plan(
        plan_id="plan-1",
        goal=contract.goal,
        created_by="test",
        steps=[
            PlanStep(
                step_id="first",
                objective="First.",
                tool="workspace.scan",
                dependencies=["second"],
                expected_output="First output.",
                success_criteria=["First succeeds."],
            ),
            PlanStep(
                step_id="second",
                objective="Second.",
                tool="evidence.extract",
                dependencies=["first"],
                expected_output="Second output.",
                success_criteria=["Second succeeds."],
            ),
        ],
    )

    with pytest.raises(PlanValidationError) as exc_info:
        PlanValidator(create_default_registry()).validate(plan, contract)

    message = str(exc_info.value)
    assert "requires at least 3 runtime steps" in message
    assert "dependency cycle" in message


def test_executor_enforces_dependencies_and_reentry(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    registry = create_default_registry()
    plan = DeterministicPlanner().create_plan(contract)
    PlanValidator(registry).validate(plan, contract)
    executor = PlanExecutor(plan, registry)

    with pytest.raises(ValueError, match="incomplete dependencies"):
        executor.execute_step("extract_evidence", lambda step: None)

    executor.execute_step("scan_workspace", lambda step: "files")
    executor.execute_step("extract_evidence", lambda step: "evidence")
    executor.execute_step("build_claims", lambda step: "claims")
    executor.execute_step(
        "build_claims",
        lambda step: "revised claims",
        allow_reentry=True,
    )

    assert plan.get_step("build_claims").status == PlanStepStatus.COMPLETED
    assert plan.get_step("build_claims").attempts == 2
    with pytest.raises(ValueError, match="does not allow step reentry"):
        executor.execute_step(
            "scan_workspace",
            lambda step: None,
            allow_reentry=True,
        )


def test_executor_records_handler_failure(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    registry = create_default_registry()
    plan = DeterministicPlanner().create_plan(contract)
    PlanValidator(registry).validate(plan, contract)
    executor = PlanExecutor(plan, registry)

    def fail(_: PlanStep) -> None:
        raise RuntimeError("tool failed")

    with pytest.raises(RuntimeError, match="tool failed"):
        executor.execute_step("scan_workspace", fail)

    step = plan.get_step("scan_workspace")
    assert step.status == PlanStepStatus.FAILED
    assert step.attempts == 1
    assert step.last_error_type == "RuntimeError"


def test_executor_invokes_registry_bound_handler(tmp_path: Path) -> None:
    from workpilot.planning import CallableToolHandler, ToolInput

    contract = _contract(tmp_path)
    registry = create_default_registry()
    plan = DeterministicPlanner().create_plan(contract)
    PlanValidator(registry).validate(plan, contract)
    spec = registry.get("workspace.scan")
    assert spec is not None
    registry.bind(
        CallableToolHandler(
            spec=spec,
            input_model=ToolInput,
            callback=lambda *_: ["a.md"],
            summarize=lambda files: {"file_count": len(files)},
        )
    )
    executor = PlanExecutor(plan, registry)

    result = executor.execute_registered_step("scan_workspace")

    assert result.output == ["a.md"]
    assert result.output_summary == {"file_count": 1}


def test_runtime_revision_reenters_only_reentrant_plan_steps(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "revision"
    runtime = Runtime(
        workspace_root=basic_workspace,
        goal="生成项目报告",
        output_dir=output,
        provider=RevisionProvider(),
        enable_entity_projection=True,
    )

    result = runtime.execute()

    assert result.state.value == "passed"
    plan = json.loads((output / "plan.json").read_text(encoding="utf-8"))
    attempts = {step["step_id"]: step["attempts"] for step in plan["steps"]}
    assert attempts["scan_workspace"] == 1
    assert attempts["extract_evidence"] == 1
    assert attempts["build_claims"] == 2
    assert attempts["render_artifacts"] == 2
    assert attempts["verify"] == 2
    assert attempts["finalize"] == 1
    context = json.loads((output / "run_context.json").read_text(encoding="utf-8"))
    assert context["revision_count"] == 1


def test_failed_revision_persists_latest_verification_report(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "failed-revision"
    runtime = Runtime(
        workspace_root=basic_workspace,
        goal="生成项目报告",
        output_dir=output,
        provider=FailingRevisionProvider(),
        enable_entity_projection=True,
    )

    result = runtime.execute()

    assert result.state.value == "failed"
    verification = json.loads(
        (output / "verification_report.json").read_text(encoding="utf-8")
    )
    assert verification["status"] == "incomplete"
    assert verification["total_checks"] > 0
    assert verification["error_count"] > 0
    context = json.loads((output / "run_context.json").read_text(encoding="utf-8"))
    assert "verification_report.json" in context["artifact_names"]
