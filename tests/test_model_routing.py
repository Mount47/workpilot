"""Tests for explicit task model routes and safe failover."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from workpilot.providers.base import (
    EvidenceCandidate,
    EvidenceExtractionResult,
    GenerationResult,
    LLMProvider,
    StructuredGenerationResult,
)
from workpilot.providers.errors import ProviderCallError, ProviderErrorType
from workpilot.providers.routing import (
    CapabilityRequirement,
    ModelRoute,
    ModelRouter,
    ModelRoutingError,
    ModelTarget,
    ModelTier,
    RoutingEvent,
    TaskRoutedProvider,
    TaskType,
)
from workpilot.providers.retry import RetryPolicy
from workpilot.providers.stub import StubProvider
from workpilot.runtime.runner import Runtime


class RoutedContractProvider(LLMProvider):
    """Deterministic network-shaped provider used by Runtime routing tests."""

    model = "contract-model"

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> GenerationResult:
        return GenerationResult(
            content="ok",
            provider="openai",
            model=self.model,
            input_tokens=2,
            output_tokens=1,
        )

    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> StructuredGenerationResult:
        return StructuredGenerationResult(
            value=response_model.model_validate({"claims": []}),
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
        quote = lines[line_number - 1].strip()
        return EvidenceExtractionResult(
            candidates=[
                EvidenceCandidate(
                    evidence_id="",
                    source_file=file_path,
                    quote=quote,
                    start_line=line_number,
                    end_line=line_number,
                )
            ],
            generations=(self.generate_text(goal),),
        )


def target(provider: str, tier: ModelTier, model: str = "test-model") -> ModelTarget:
    return ModelTarget(provider=provider, model=model, tier=tier)


def test_route_rejects_silent_lower_tier_fallback() -> None:
    with pytest.raises(ValueError, match="below minimum tier"):
        ModelRoute(
            task_type=TaskType.ANALYSIS,
            targets=[target("deepseek", ModelTier.ECONOMY)],
            minimum_tier=ModelTier.BALANCED,
        )


def test_route_rejects_multiple_targets_without_explicit_failover() -> None:
    with pytest.raises(ValueError, match="allow_failover"):
        ModelRoute(
            task_type=TaskType.PLANNING,
            targets=[
                target("openai", ModelTier.BALANCED),
                target("claude", ModelTier.PREMIUM),
            ],
        )


def test_router_rejects_provider_without_required_capability() -> None:
    route = ModelRoute(
        task_type=TaskType.PLANNING,
        targets=[target("stub", ModelTier.ECONOMY)],
        requirements=CapabilityRequirement(structured_output=True),
    )
    router = ModelRouter([route], provider_factory=lambda _: StubProvider())

    with pytest.raises(ModelRoutingError, match="structured_output"):
        router.execute(TaskType.PLANNING, lambda _: "unused")


def test_router_retries_then_fails_over_only_for_transient_error() -> None:
    events: list[RoutingEvent] = []
    calls: dict[str, int] = {"deepseek": 0, "openai": 0}
    route = ModelRoute(
        task_type=TaskType.ANALYSIS,
        targets=[
            target("deepseek", ModelTier.BALANCED, "deepseek-v4-pro"),
            target("openai", ModelTier.BALANCED, "gpt-5.4-mini"),
        ],
        minimum_tier=ModelTier.BALANCED,
        requirements=CapabilityRequirement(structured_output=True),
        allow_failover=True,
    )

    def operation(provider: Any) -> str:
        name = provider.route_name
        calls[name] += 1
        if name == "deepseek":
            raise ProviderCallError(name, ProviderErrorType.TIMEOUT)
        return "ok"

    def factory(model_target: ModelTarget) -> Any:
        provider = StubProvider()
        provider.route_name = model_target.provider
        provider.model = model_target.model
        return provider

    router = ModelRouter(
        [route],
        retry_policy=RetryPolicy(max_attempts=2),
        provider_factory=factory,
        on_event=events.append,
        sleep=lambda _: None,
    )
    result = router.execute(TaskType.ANALYSIS, operation)

    assert result.value == "ok"
    assert result.target.provider == "openai"
    assert calls == {"deepseek": 2, "openai": 1}
    assert [event.event_type for event in events] == [
        "model_route_selected",
        "model_call_retry_scheduled",
        "model_route_failed_over",
        "model_route_selected",
    ]


def test_router_does_not_hide_authentication_failure_with_fallback() -> None:
    route = ModelRoute(
        task_type=TaskType.ANALYSIS,
        targets=[
            target("deepseek", ModelTier.BALANCED),
            target("openai", ModelTier.BALANCED),
        ],
        allow_failover=True,
    )
    router = ModelRouter(
        [route],
        provider_factory=lambda _: StubProvider(),
        sleep=lambda _: None,
    )

    with pytest.raises(ProviderCallError) as exc_info:
        router.execute(
            TaskType.ANALYSIS,
            lambda _: (_ for _ in ()).throw(
                ProviderCallError("deepseek", ProviderErrorType.AUTHENTICATION)
            ),
        )

    assert exc_info.value.error_type == ProviderErrorType.AUTHENTICATION


def test_router_checks_budget_before_each_physical_attempt() -> None:
    checked: list[int] = []
    route = ModelRoute(
        task_type=TaskType.EVIDENCE_EXTRACTION,
        targets=[target("deepseek", ModelTier.ECONOMY)],
    )
    router = ModelRouter(
        [route],
        retry_policy=RetryPolicy(max_attempts=2),
        provider_factory=lambda _: StubProvider(),
        before_attempt=checked.append,
        sleep=lambda _: None,
    )

    with pytest.raises(ProviderCallError):
        router.execute(
            TaskType.EVIDENCE_EXTRACTION,
            lambda _: (_ for _ in ()).throw(
                ProviderCallError("deepseek", ProviderErrorType.SERVER)
            ),
        )

    assert checked == [1, 2]


def test_task_routed_provider_adds_prompt_and_schema_metadata() -> None:
    route = ModelRoute(
        task_type=TaskType.ANALYSIS,
        targets=[target("openai", ModelTier.BALANCED, "contract-model")],
        requirements=CapabilityRequirement(structured_output=True),
    )
    router = ModelRouter(
        [route],
        provider_factory=lambda _: RoutedContractProvider(),
    )
    provider = TaskRoutedProvider(router, TaskType.ANALYSIS)

    class Response(BaseModel):
        claims: list[str]

    result = provider.generate_structured("analyze", Response)
    generation = result.generations[0]

    assert generation.task_type == "analysis"
    assert generation.prompt_version == "claim_builder.v1"
    assert generation.schema_name == "Response"
    assert generation.schema_version == "claim_draft.v1"
    assert generation.route_target_index == 0


def test_runtime_uses_routes_and_records_versioned_model_trace(
    basic_workspace: Path,
    tmp_path: Path,
) -> None:
    routes = [
        ModelRoute(
            task_type=task_type,
            targets=[target("openai", ModelTier.BALANCED, "contract-model")],
            requirements=CapabilityRequirement(structured_output=True),
        )
        for task_type in (
            TaskType.EVIDENCE_EXTRACTION,
            TaskType.ANALYSIS,
            TaskType.REVISION,
        )
    ]
    router = ModelRouter(
        routes,
        provider_factory=lambda _: RoutedContractProvider(),
        sleep=lambda _: None,
    )
    output = tmp_path / "routed-runtime"
    runtime = Runtime(
        workspace_root=basic_workspace,
        goal="生成项目报告",
        output_dir=output,
        provider=RoutedContractProvider(),
        model_router=router,
    )

    result = runtime.execute()

    assert result.state.value == "passed"
    trace = json.loads((output / "trace.json").read_text())
    route_events = [
        event for event in trace["events"]
        if event["event_type"] == "model_route_selected"
    ]
    assert route_events
    assert all(event["step_id"] is not None for event in route_events)
    model_events = [
        event for event in trace["events"]
        if event["event_type"] == "model_call_completed"
    ]
    assert {event["data"]["task_type"] for event in model_events} == {
        "evidence_extraction",
        "analysis",
    }
    assert all(event["data"]["prompt_version"] for event in model_events)
    assert all("prompt" not in event["data"] for event in model_events)
