"""Registry of tool specifications, input schemas and bound handlers."""

from typing import Any

from pydantic import BaseModel

from workpilot.planning.models import ToolSpec
from workpilot.planning.tools import ToolHandler, ToolInput, ToolResult


class ToolRegistry:
    """Store immutable tool specifications used for plan validation."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._input_models: dict[str, type[BaseModel]] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(
        self,
        spec: ToolSpec,
        input_model: type[BaseModel] = ToolInput,
    ) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Tool {spec.name} is already registered")
        self._specs[spec.name] = spec
        self._input_models[spec.name] = input_model

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def list_all(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def input_schema(self, name: str) -> dict[str, Any]:
        input_model = self._input_models.get(name)
        if input_model is None:
            raise KeyError(f"Tool {name} is not registered")
        return input_model.model_json_schema()

    def validate_inputs(self, name: str, inputs: dict[str, Any]) -> BaseModel:
        input_model = self._input_models.get(name)
        if input_model is None:
            raise KeyError(f"Tool {name} is not registered")
        return input_model.model_validate(inputs)

    def bind(self, handler: ToolHandler) -> None:
        name = handler.spec.name
        registered = self._specs.get(name)
        if registered is None:
            raise ValueError(f"Tool {name} must be registered before binding")
        if name in self._handlers:
            raise ValueError(f"Tool {name} already has a bound handler")
        if handler.spec.version != registered.version:
            raise ValueError(
                f"Tool {name} handler version {handler.spec.version} does not match "
                f"registered version {registered.version}"
            )
        if handler.input_model is not self._input_models[name]:
            raise ValueError(f"Tool {name} handler input model does not match Registry")
        self._handlers[name] = handler

    def invoke(self, name: str, inputs: dict[str, Any], step) -> ToolResult:
        handler = self._handlers.get(name)
        if handler is None:
            raise ValueError(f"Tool {name} has no bound handler")
        validated = self.validate_inputs(name, inputs)
        return handler.execute(validated, step)


def create_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for spec in [
        ToolSpec(
            name="workspace.scan",
            description="List sources inside the authorized workspace.",
        ),
        ToolSpec(
            name="evidence.extract",
            description="Extract and validate citable evidence from sources.",
        ),
        ToolSpec(
            name="claims.build",
            description="Build structured project claims from evidence.",
            reentrant=True,
            evidence_required=True,
        ),
        ToolSpec(
            name="artifacts.render",
            description="Render artifacts from the current project snapshot.",
            reentrant=True,
            evidence_required=True,
        ),
        ToolSpec(
            name="verification.run",
            description="Verify claim support and artifact citations.",
            reentrant=True,
            evidence_required=True,
        ),
        ToolSpec(
            name="artifacts.finalize",
            description="Persist final artifacts and verification results.",
        ),
    ]:
        registry.register(spec)
    return registry
