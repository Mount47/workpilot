"""Tests for hard execution budgets and observable budget failures."""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from workpilot.providers.base import (
    EvidenceCandidate,
    EvidenceExtractionResult,
    GenerationResult,
    LLMProvider,
    StructuredGenerationResult,
)
from workpilot.providers.stub import StubProvider
from workpilot.runtime.budget import BudgetExceededError, ExecutionBudget
from workpilot.runtime.runner import Runtime


class TokenUsingProvider(LLMProvider):
    """Provider that exceeds a small token budget on its first source."""

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> GenerationResult:
        return GenerationResult(
            content="",
            provider="token-test",
            model="token-test",
            input_tokens=6,
            output_tokens=5,
        )

    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> StructuredGenerationResult:
        raise AssertionError("claim generation should not be reached")

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
            ],
            generations=(self.generate_text(""),),
        )


def test_execution_budget_enforces_time_with_injected_clock() -> None:
    now = [0.0]
    budget = ExecutionBudget(
        max_steps=3,
        token_budget=100,
        time_budget_seconds=1,
        clock=lambda: now[0],
    )
    budget.start()
    budget.consume_step("first")
    now[0] = 1.1

    with pytest.raises(BudgetExceededError, match="time budget exceeded"):
        budget.check_time()


def test_runtime_rejects_plan_when_step_budget_is_too_small(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "step-budget"
    runtime = Runtime(
        workspace_root=basic_workspace,
        goal="生成报告",
        output_dir=output,
        provider=StubProvider(),
        max_steps=1,
    )

    result = runtime.execute()

    assert result.state.value == "failed"
    assert "plan requires at least 7 runtime steps" in (result.failure_reason or "")
    trace = json.loads((output / "trace.json").read_text(encoding="utf-8"))
    assert trace["events"][-1]["event_type"] == "budget_summary"
    assert trace["events"][-1]["data"]["steps"]["used"] == 1
    context = json.loads((output / "run_context.json").read_text(encoding="utf-8"))
    assert context["run_status"] == "failed"
    assert context["last_error_type"] == "PlanValidationError"
    assert context["plan"]["validated"] is False


def test_runtime_token_budget_records_call_before_failure(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "token-budget"
    runtime = Runtime(
        workspace_root=basic_workspace,
        goal="生成报告",
        output_dir=output,
        provider=TokenUsingProvider(),
        token_budget=10,
    )

    result = runtime.execute()

    assert result.state.value == "failed"
    assert "token budget exceeded" in (result.failure_reason or "")
    trace = json.loads((output / "trace.json").read_text(encoding="utf-8"))
    event_types = [event["event_type"] for event in trace["events"]]
    assert "model_call_completed" in event_types
    assert "tool_call_failed" in event_types
    tool_failure = next(
        event for event in trace["events"]
        if event["event_type"] == "tool_call_failed"
    )
    assert tool_failure["data"]["tool"] == "evidence.extract"
    assert tool_failure["data"]["error_type"] == "BudgetExceededError"
    assert "output" not in tool_failure["data"]
    summary = next(
        event["data"]
        for event in trace["events"]
        if event["event_type"] == "budget_summary"
    )
    assert summary["tokens"]["used"] == 11
    context = json.loads((output / "run_context.json").read_text(encoding="utf-8"))
    assert context["run_status"] == "failed"
    assert context["budget"]["tokens"]["used"] == 11
    failed_steps = [step for step in context["steps"] if step["status"] == "failed"]
    assert failed_steps[0]["error_type"] == "BudgetExceededError"
