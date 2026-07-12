"""Explicit task-to-model routing with capability and quality boundaries."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, model_validator

from workpilot.providers.base import LLMProvider
from workpilot.providers.base import (
    EvidenceExtractionResult,
    GenerationResult,
    StructuredGenerationResult,
)
from workpilot.providers.errors import ProviderCallError
from workpilot.providers.registry import get_provider, get_provider_descriptor
from workpilot.providers.retry import RetryEvent, RetryPolicy


T = TypeVar("T")


class TaskType(str, Enum):
    """Model-backed task categories used by WorkPilot."""

    EVIDENCE_EXTRACTION = "evidence_extraction"
    PLANNING = "planning"
    ANALYSIS = "analysis"
    REVISION = "revision"
    SEMANTIC_VERIFICATION = "semantic_verification"


class ModelTier(str, Enum):
    """Internal routing tier, not a universal model quality ranking."""

    ECONOMY = "economy"
    BALANCED = "balanced"
    PREMIUM = "premium"


TIER_RANK = {
    ModelTier.ECONOMY: 1,
    ModelTier.BALANCED: 2,
    ModelTier.PREMIUM: 3,
}


class CapabilityRequirement(BaseModel):
    """Minimum adapter capabilities required by one route."""

    structured_output: bool = False
    native_tools: bool = False
    multimodal_input: bool = False


class ModelTarget(BaseModel):
    """One explicit Provider/model candidate in route order."""

    provider: str = Field(min_length=1)
    model: str | None = None
    tier: ModelTier


class ModelRoute(BaseModel):
    """Ordered targets and the safety boundary for one TaskType."""

    task_type: TaskType
    targets: list[ModelTarget] = Field(min_length=1)
    minimum_tier: ModelTier = ModelTier.ECONOMY
    requirements: CapabilityRequirement = Field(
        default_factory=CapabilityRequirement
    )
    allow_failover: bool = False

    @model_validator(mode="after")
    def validate_targets(self) -> "ModelRoute":
        if len(self.targets) > 1 and not self.allow_failover:
            raise ValueError("multiple targets require allow_failover=true")
        identities = [(target.provider, target.model) for target in self.targets]
        if len(identities) != len(set(identities)):
            raise ValueError("route targets must be unique")
        for target in self.targets:
            if TIER_RANK[target.tier] < TIER_RANK[self.minimum_tier]:
                raise ValueError(
                    f"target {target.provider} is below minimum tier "
                    f"{self.minimum_tier.value}"
                )
        return self


class ModelRoutingError(ValueError):
    """A route is missing or cannot satisfy its declared capabilities."""

    error_type = "model_routing"
    retryable = False


@dataclass(frozen=True)
class RoutingEvent:
    """Sanitized route decision suitable for Trace."""

    event_type: str
    task_type: str
    provider: str
    model: str
    target_index: int
    error_type: str | None = None
    attempt: int | None = None
    delay_seconds: float | None = None


@dataclass(frozen=True)
class RoutedResult(Generic[T]):
    """Operation result plus the selected route target."""

    value: T
    task_type: TaskType
    target: ModelTarget
    target_index: int


class PromptContract(BaseModel):
    """Version identifiers recorded for a model-backed task."""

    prompt_version: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)


DEFAULT_PROMPT_CONTRACTS: dict[TaskType, PromptContract] = {
    TaskType.EVIDENCE_EXTRACTION: PromptContract(
        prompt_version="evidence_extraction.v1",
        schema_version="evidence_candidate.v1",
    ),
    TaskType.PLANNING: PromptContract(
        prompt_version="planner.v1",
        schema_version="plan.v1",
    ),
    TaskType.ANALYSIS: PromptContract(
        prompt_version="claim_builder.v1",
        schema_version="claim_draft.v1",
    ),
    TaskType.REVISION: PromptContract(
        prompt_version="claim_revision.v1",
        schema_version="claim_draft.v1",
    ),
    TaskType.SEMANTIC_VERIFICATION: PromptContract(
        prompt_version="semantic_verifier.v1",
        schema_version="verification_result.v1",
    ),
}


ProviderFactory = Callable[[ModelTarget], LLMProvider]


class ModelRouter:
    """Select, retry and explicitly fail over model-backed operations."""

    def __init__(
        self,
        routes: list[ModelRoute],
        *,
        retry_policy: RetryPolicy | None = None,
        provider_factory: ProviderFactory | None = None,
        before_attempt: Callable[[int], None] | None = None,
        on_event: Callable[[RoutingEvent], None] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.routes = {route.task_type: route for route in routes}
        if len(self.routes) != len(routes):
            raise ModelRoutingError("each task type may have only one route")
        self.retry_policy = retry_policy or RetryPolicy()
        self.provider_factory = provider_factory or self._default_provider_factory
        self.before_attempt = before_attempt
        self.on_event = on_event
        self.sleep = sleep

    def has_route(self, task_type: TaskType) -> bool:
        """Return whether a task has an explicit route."""
        return task_type in self.routes

    def execute(
        self,
        task_type: TaskType,
        operation: Callable[[LLMProvider], T],
    ) -> RoutedResult[T]:
        """Execute an operation using an explicit, validated route."""
        route = self.routes.get(task_type)
        if route is None:
            raise ModelRoutingError(f"no model route configured for {task_type.value}")

        for target_index, target in enumerate(route.targets):
            self._validate_capabilities(target, route.requirements)
            provider = self.provider_factory(target)
            model = target.model or str(getattr(provider, "model", "<default>"))
            self._emit(
                RoutingEvent(
                    event_type="model_route_selected",
                    task_type=task_type.value,
                    provider=target.provider,
                    model=model,
                    target_index=target_index,
                )
            )
            try:
                kwargs: dict[str, Any] = {
                    "before_attempt": self.before_attempt,
                    "on_retry": lambda event: self._on_retry(
                        task_type, target, target_index, model, event
                    ),
                }
                if self.sleep is not None:
                    kwargs["sleep"] = self.sleep
                value = self.retry_policy.execute(
                    lambda: operation(provider),
                    **kwargs,
                )
                return RoutedResult(
                    value=value,
                    task_type=task_type,
                    target=target,
                    target_index=target_index,
                )
            except ProviderCallError as exc:
                has_fallback = target_index + 1 < len(route.targets)
                if (
                    not route.allow_failover
                    or not has_fallback
                    or not exc.retryable
                ):
                    raise
                next_target = route.targets[target_index + 1]
                self._emit(
                    RoutingEvent(
                        event_type="model_route_failed_over",
                        task_type=task_type.value,
                        provider=next_target.provider,
                        model=next_target.model or "<default>",
                        target_index=target_index + 1,
                        error_type=exc.error_type.value,
                    )
                )
        raise ModelRoutingError(f"route exhausted for {task_type.value}")

    @staticmethod
    def _default_provider_factory(target: ModelTarget) -> LLMProvider:
        kwargs = {"model": target.model} if target.model else {}
        return get_provider(target.provider, **kwargs)

    @staticmethod
    def _validate_capabilities(
        target: ModelTarget,
        requirements: CapabilityRequirement,
    ) -> None:
        try:
            capabilities = get_provider_descriptor(target.provider).capabilities
        except ValueError as exc:
            raise ModelRoutingError(str(exc)) from exc
        missing: list[str] = []
        if (
            requirements.structured_output
            and capabilities.structured_output_mode == "none"
        ):
            missing.append("structured_output")
        if requirements.native_tools and not capabilities.native_tools:
            missing.append("native_tools")
        if requirements.multimodal_input and not capabilities.multimodal_input:
            missing.append("multimodal_input")
        if missing:
            raise ModelRoutingError(
                f"provider '{target.provider}' lacks capabilities: "
                f"{', '.join(missing)}"
            )

    def _on_retry(
        self,
        task_type: TaskType,
        target: ModelTarget,
        target_index: int,
        model: str,
        event: RetryEvent,
    ) -> None:
        self._emit(
            RoutingEvent(
                event_type="model_call_retry_scheduled",
                task_type=task_type.value,
                provider=target.provider,
                model=model,
                target_index=target_index,
                error_type=event.error_type,
                attempt=event.next_attempt,
                delay_seconds=event.delay_seconds,
            )
        )

    def _emit(self, event: RoutingEvent) -> None:
        if self.on_event is not None:
            self.on_event(event)


class TaskRoutedProvider(LLMProvider):
    """LLMProvider view bound to one TaskType and PromptContract."""

    def __init__(
        self,
        router: ModelRouter,
        task_type: TaskType,
        contract: PromptContract | None = None,
    ) -> None:
        self.router = router
        self.task_type = task_type
        self.contract = contract or DEFAULT_PROMPT_CONTRACTS[task_type]

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> GenerationResult:
        routed = self.router.execute(
            self.task_type,
            lambda provider: provider.generate_text(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
            ),
        )
        return self._annotate_generation(routed.value, routed, schema_name=None)

    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> StructuredGenerationResult:
        routed = self.router.execute(
            self.task_type,
            lambda provider: provider.generate_structured(
                prompt=prompt,
                response_model=response_model,
                system_prompt=system_prompt,
                temperature=temperature,
            ),
        )
        return StructuredGenerationResult(
            value=routed.value.value,
            generations=tuple(
                self._annotate_generation(
                    generation,
                    routed,
                    schema_name=response_model.__name__,
                )
                for generation in routed.value.generations
            ),
        )

    def extract_evidence_from_file(
        self,
        file_path: str,
        content: str,
        goal: str,
    ) -> EvidenceExtractionResult:
        routed = self.router.execute(
            self.task_type,
            lambda provider: provider.extract_evidence_from_file(
                file_path=file_path,
                content=content,
                goal=goal,
            ),
        )
        return EvidenceExtractionResult(
            candidates=routed.value.candidates,
            generations=tuple(
                self._annotate_generation(
                    generation,
                    routed,
                    schema_name="EvidenceCandidate[]",
                )
                for generation in routed.value.generations
            ),
            error_type=routed.value.error_type,
        )

    def _annotate_generation(
        self,
        generation: GenerationResult,
        routed: RoutedResult[Any],
        *,
        schema_name: str | None,
    ) -> GenerationResult:
        return generation.model_copy(
            update={
                "task_type": self.task_type.value,
                "prompt_version": self.contract.prompt_version,
                "schema_name": schema_name,
                "schema_version": self.contract.schema_version,
                "route_target_index": routed.target_index,
            }
        )
