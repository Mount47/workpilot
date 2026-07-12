"""Typed tool inputs, handlers and safe execution results."""

from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from workpilot.planning.models import PlanStep, ToolSpec


class ToolInput(BaseModel):
    """Default no-argument input contract; unknown fields fail closed."""

    model_config = ConfigDict(extra="forbid")


class ToolResult(BaseModel):
    """Internal output plus the only summary allowed into Trace."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: str = "completed"
    output: Any = Field(default=None, exclude=True)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class ToolHandler(Protocol):
    """One bound implementation for a registered ToolSpec."""

    spec: ToolSpec
    input_model: type[BaseModel]

    def execute(self, tool_input: BaseModel, step: PlanStep) -> ToolResult: ...


class CallableToolHandler:
    """Adapt a typed in-process callback to the ToolHandler protocol."""

    def __init__(
        self,
        *,
        spec: ToolSpec,
        callback: Callable[[BaseModel, PlanStep], Any],
        input_model: type[BaseModel] = ToolInput,
        summarize: Callable[[Any], dict[str, Any]] | None = None,
    ) -> None:
        self.spec = spec
        self.callback = callback
        self.input_model = input_model
        self.summarize = summarize or (lambda _: {})

    def execute(self, tool_input: BaseModel, step: PlanStep) -> ToolResult:
        """Execute without swallowing exceptions or exposing raw output."""
        output = self.callback(tool_input, step)
        if isinstance(output, ToolResult):
            return output
        return ToolResult(
            output=output,
            output_summary=self.summarize(output),
        )
