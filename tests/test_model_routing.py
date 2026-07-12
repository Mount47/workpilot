"""Tests for explicit task model routes and safe failover."""

from typing import Any

import pytest

from workpilot.providers.errors import ProviderCallError, ProviderErrorType
from workpilot.providers.routing import (
    CapabilityRequirement,
    ModelRoute,
    ModelRouter,
    ModelRoutingError,
    ModelTarget,
    ModelTier,
    RoutingEvent,
    TaskType,
)
from workpilot.providers.retry import RetryPolicy
from workpilot.providers.stub import StubProvider


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

