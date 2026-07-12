"""Mission contract models."""

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class MissionContract(BaseModel):
    """Defines the boundary of a single run."""

    run_id: str
    goal: str
    workspace_root: Path
    allowed_tools: list[str] = Field(
        default_factory=lambda: [
            "workspace.scan",
            "evidence.extract",
            "claims.build",
            "artifacts.render",
            "verification.run",
            "artifacts.finalize",
            "list_files",
            "read_file",
            "search_text",
        ]
    )
    max_steps: int = Field(default=30, ge=1)
    token_budget: int = Field(default=100_000, ge=1)
    time_budget_seconds: int = Field(default=300, ge=1)
    allowed_artifact_types: list[str] = Field(
        default_factory=lambda: [
            "weekly_report",
            "risks",
            "action_items",
            "project_snapshot",
            "plan",
            "run_context",
            "verification_report",
            "trace",
        ]
    )
    forbidden_actions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("workspace_root")
    @classmethod
    def normalize_workspace(cls, v: Path) -> Path:
        """Ensure workspace_root is absolute and resolved."""
        return v.resolve()

    def is_tool_allowed(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools

    def is_artifact_allowed(self, artifact_type: str) -> bool:
        return artifact_type in self.allowed_artifact_types

    def is_action_forbidden(self, action: str) -> bool:
        return action in self.forbidden_actions
