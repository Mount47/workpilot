"""Versioned, fail-closed JSON codecs for Runtime recovery checkpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from workpilot.domain import Evidence, ProjectSnapshot
from workpilot.planning import Plan, ToolResult
from workpilot.verification.base import VerifyResult


CHECKPOINT_SCHEMA_VERSION = 1
TOOL_CODEC_VERSION = 1


class CheckpointValidationError(ValueError):
    """A checkpoint cannot be safely encoded or reconstructed."""


class VerifyResultCheckpoint(BaseModel):
    """JSON contract for one deterministic verifier result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    severity: str = Field(default="error", min_length=1)
    artifact: str = ""
    location: str = ""
    message: str = ""

    @classmethod
    def from_runtime(cls, result: VerifyResult) -> "VerifyResultCheckpoint":
        return cls.model_validate(result.to_dict())

    def to_runtime(self) -> VerifyResult:
        return VerifyResult(**self.model_dump())


class BudgetCheckpoint(BaseModel):
    """Resource counters needed to resume budget enforcement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    steps_used: int = Field(ge=0)
    steps_limit: int = Field(gt=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    token_limit: int = Field(gt=0)
    elapsed_seconds: float = Field(ge=0)
    time_limit_seconds: int = Field(gt=0)


class RuntimeStateCheckpoint(BaseModel):
    """Typed Runtime-owned state beyond individual tool outputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_snapshot: ProjectSnapshot | None = None
    rendered_artifacts: dict[str, Any] = Field(default_factory=dict)
    verification_results: list[VerifyResultCheckpoint] = Field(default_factory=list)
    verification_errors: list[VerifyResultCheckpoint] = Field(default_factory=list)
    revision_attempt: int = Field(default=1, ge=1)
    revision_feedback: list[str] = Field(default_factory=list)
    budget: BudgetCheckpoint

    @model_validator(mode="after")
    def validate_json_state(self) -> "RuntimeStateCheckpoint":
        _validate_artifacts(self.rendered_artifacts)
        return self


class ToolResultCheckpoint(BaseModel):
    """One tool result encoded under an explicit built-in codec."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str = Field(min_length=1)
    tool_version: str = Field(default="1.0", pattern=r"^[0-9]+\.[0-9]+$")
    codec_version: Literal[1] = TOOL_CODEC_VERSION
    status: Literal["completed"] = "completed"
    output_summary: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    value: Any = None

    @model_validator(mode="after")
    def validate_json_fields(self) -> "ToolResultCheckpoint":
        _validate_json_value(self.output_summary, field="output_summary")
        _validate_json_value(self.value, field="value")
        return self


class CheckpointPayload(BaseModel):
    """Complete version-1 recovery state committed after a PlanStep."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = CHECKPOINT_SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    committed_step_id: str = Field(min_length=1)
    plan: Plan
    step_results: dict[str, ToolResultCheckpoint] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    runtime_state: RuntimeStateCheckpoint

    @model_validator(mode="after")
    def validate_execution_identity(self) -> "CheckpointPayload":
        plan_step_ids = {step.step_id for step in self.plan.steps}
        if self.committed_step_id not in plan_step_ids:
            raise ValueError("committed_step_id is not present in the Plan")
        unknown_results = sorted(set(self.step_results) - plan_step_ids)
        if unknown_results:
            raise ValueError(
                "step_results contain unknown Plan steps: " + ", ".join(unknown_results)
            )
        registry = CheckpointCodecRegistry()
        for encoded in self.step_results.values():
            registry.validate_encoded(encoded)
        return self


class CheckpointCodecRegistry:
    """Encode only the six fixed Runtime tools; unknown values fail closed."""

    _TOOLS = {
        "workspace.scan",
        "evidence.extract",
        "claims.build",
        "artifacts.render",
        "verification.run",
        "artifacts.finalize",
    }

    def encode(
        self,
        tool: str,
        result: ToolResult,
        *,
        tool_version: str = "1.0",
    ) -> ToolResultCheckpoint:
        self._require_tool(tool)
        if result.status != "completed":
            raise CheckpointValidationError(
                "only completed ToolResults can be checkpointed"
            )
        try:
            value = self._encode_output(tool, result.output)
            encoded = ToolResultCheckpoint(
                tool=tool,
                tool_version=tool_version,
                status="completed",
                output_summary=dict(result.output_summary),
                evidence_ids=list(result.evidence_ids),
                value=value,
            )
            self.validate_encoded(encoded)
            return encoded
        except CheckpointValidationError:
            raise
        except (TypeError, ValueError, ValidationError) as exc:
            raise CheckpointValidationError(
                f"invalid {tool} checkpoint output"
            ) from exc

    def decode(self, encoded: ToolResultCheckpoint) -> ToolResult:
        self.validate_encoded(encoded)
        try:
            output = self._decode_output(encoded.tool, encoded.value)
        except CheckpointValidationError:
            raise
        except (TypeError, ValueError, ValidationError) as exc:
            raise CheckpointValidationError(
                f"invalid {encoded.tool} checkpoint output"
            ) from exc
        return ToolResult(
            status=encoded.status,
            output=output,
            output_summary=dict(encoded.output_summary),
            evidence_ids=list(encoded.evidence_ids),
        )

    def validate_encoded(self, encoded: ToolResultCheckpoint) -> None:
        self._require_tool(encoded.tool)
        if encoded.codec_version != TOOL_CODEC_VERSION:
            raise CheckpointValidationError(
                f"unsupported codec version for {encoded.tool}"
            )
        self._decode_output(encoded.tool, encoded.value)

    def _require_tool(self, tool: str) -> None:
        if tool not in self._TOOLS:
            raise CheckpointValidationError(f"unsupported tool codec: {tool}")

    @staticmethod
    def _encode_output(tool: str, output: Any) -> Any:
        if tool == "workspace.scan":
            return _validate_workspace_files(output)
        if tool == "evidence.extract":
            return _validate_evidence_summary(output)
        if tool == "claims.build":
            if not isinstance(output, ProjectSnapshot):
                raise CheckpointValidationError(
                    "claims.build requires a ProjectSnapshot"
                )
            return output.model_dump(mode="json")
        if tool == "artifacts.render":
            return _validate_artifacts(output)
        if tool == "verification.run":
            return _encode_verification_output(output)
        if tool == "artifacts.finalize":
            if output is not None:
                raise CheckpointValidationError(
                    "artifacts.finalize requires a null output"
                )
            return None
        raise CheckpointValidationError(f"unsupported tool codec: {tool}")

    @staticmethod
    def _decode_output(tool: str, value: Any) -> Any:
        if tool == "workspace.scan":
            return _validate_workspace_files(value)
        if tool == "evidence.extract":
            return _validate_evidence_summary(value)
        if tool == "claims.build":
            return ProjectSnapshot.model_validate(value)
        if tool == "artifacts.render":
            return _validate_artifacts(value)
        if tool == "verification.run":
            return _decode_verification_output(value)
        if tool == "artifacts.finalize":
            if value is not None:
                raise CheckpointValidationError(
                    "artifacts.finalize requires a null output"
                )
            return None
        raise CheckpointValidationError(f"unsupported tool codec: {tool}")


def canonical_json_bytes(payload: CheckpointPayload) -> bytes:
    """Return deterministic UTF-8 JSON bytes for hashing and atomic storage."""
    try:
        data = payload.model_dump(mode="json")
        return json.dumps(
            data,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CheckpointValidationError("checkpoint is not canonical JSON") from exc


def decode_checkpoint(data: bytes) -> CheckpointPayload:
    """Decode and validate one complete Checkpoint Payload."""
    try:
        raw = json.loads(data.decode("utf-8"))
        return CheckpointPayload.model_validate(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        if isinstance(exc, CheckpointValidationError):
            raise
        raise CheckpointValidationError("invalid checkpoint payload") from exc


def _validate_workspace_files(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise CheckpointValidationError(
            "workspace.scan output must be a list of relative paths"
        )
    validated: list[str] = []
    for item in value:
        path = Path(item)
        if path.is_absolute() or ".." in path.parts:
            raise CheckpointValidationError(
                "workspace.scan output contains an unsafe path"
            )
        validated.append(item)
    return validated


_EVIDENCE_SUMMARY_FIELDS = {
    "evidence_count",
    "discarded_count",
    "provider_error_count",
    "repair_count",
    "locator_repaired_count",
    "source_count",
}


def _validate_evidence_summary(value: Any) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != _EVIDENCE_SUMMARY_FIELDS:
        raise CheckpointValidationError(
            "evidence.extract output has an invalid summary shape"
        )
    if any(type(item) is not int or item < 0 for item in value.values()):
        raise CheckpointValidationError(
            "evidence.extract output counts must be non-negative integers"
        )
    return {key: value[key] for key in sorted(value)}


def _validate_artifacts(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CheckpointValidationError(
            "artifacts.render output must be an artifact mapping"
        )
    validated: dict[str, Any] = {}
    for name, content in value.items():
        if not isinstance(name, str) or not name.strip():
            raise CheckpointValidationError("artifact names must be non-empty strings")
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise CheckpointValidationError("artifact name is outside the Run directory")
        if not isinstance(content, (str, dict)):
            raise CheckpointValidationError(
                "artifact content must be text or a JSON object"
            )
        _validate_json_value(content, field=f"artifact {name}")
        validated[name] = content
    return validated


def _encode_verification_output(value: Any) -> dict[str, list[dict[str, str]]]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise CheckpointValidationError(
            "verification.run output must be a results/errors tuple"
        )
    results, errors = value
    if not isinstance(results, list) or not isinstance(errors, list):
        raise CheckpointValidationError(
            "verification.run results and errors must be lists"
        )
    if not all(isinstance(item, VerifyResult) for item in results + errors):
        raise CheckpointValidationError(
            "verification.run contains an invalid verifier result"
        )
    return {
        "results": [VerifyResultCheckpoint.from_runtime(item).model_dump() for item in results],
        "errors": [VerifyResultCheckpoint.from_runtime(item).model_dump() for item in errors],
    }


def _decode_verification_output(value: Any) -> tuple[list[VerifyResult], list[VerifyResult]]:
    if not isinstance(value, dict) or set(value) != {"results", "errors"}:
        raise CheckpointValidationError(
            "verification.run output has an invalid shape"
        )
    decoded: list[list[VerifyResult]] = []
    for key in ("results", "errors"):
        items = value[key]
        if not isinstance(items, list):
            raise CheckpointValidationError(
                f"verification.run {key} must be a list"
            )
        decoded.append(
            [VerifyResultCheckpoint.model_validate(item).to_runtime() for item in items]
        )
    return decoded[0], decoded[1]


def _validate_json_value(value: Any, *, field: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise CheckpointValidationError(f"{field} contains a non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, field=field)
        return
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise CheckpointValidationError(f"{field} contains a non-string key")
        for item in value.values():
            _validate_json_value(item, field=field)
        return
    raise CheckpointValidationError(f"{field} contains a non-JSON value")
