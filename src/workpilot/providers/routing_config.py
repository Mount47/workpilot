"""File-backed model routing configuration without embedded secrets."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from workpilot.providers.retry import RetryPolicy
from workpilot.providers.routing import ModelRoute, ModelRouter


class ModelRoutingConfig(BaseModel):
    """Validated configuration used to construct a ModelRouter."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="1.0", pattern=r"^1\.")
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    routes: list[ModelRoute] = Field(min_length=1)

    def build_router(self) -> ModelRouter:
        """Create a runtime router; API keys remain in environment settings."""
        return ModelRouter(
            routes=self.routes,
            retry_policy=self.retry_policy,
        )


def load_model_routing_config(path: Path) -> ModelRoutingConfig:
    """Load and validate a UTF-8 JSON model routing configuration."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return ModelRoutingConfig.model_validate(data)

