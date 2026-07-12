"""Providers package."""

from workpilot.providers.base import EvidenceCandidate, LLMProvider
from workpilot.providers.registry import get_provider, get_provider_descriptor
from workpilot.providers.retry import RetryEvent, RetryPolicy
from workpilot.providers.routing import (
    CapabilityRequirement,
    ModelRoute,
    ModelRouter,
    ModelTarget,
    ModelTier,
    PromptContract,
    TaskRoutedProvider,
    TaskType,
)
from workpilot.providers.routing_config import (
    ModelRoutingConfig,
    load_model_routing_config,
)
from workpilot.providers.specs import (
    ProviderCapabilities,
    ProviderDescriptor,
    ProviderTransport,
)

__all__ = [
    "EvidenceCandidate",
    "LLMProvider",
    "ProviderCapabilities",
    "ProviderDescriptor",
    "ProviderTransport",
    "get_provider",
    "get_provider_descriptor",
    "RetryEvent",
    "RetryPolicy",
    "CapabilityRequirement",
    "ModelRoute",
    "ModelRouter",
    "ModelTarget",
    "ModelTier",
    "PromptContract",
    "TaskRoutedProvider",
    "TaskType",
    "ModelRoutingConfig",
    "load_model_routing_config",
]
