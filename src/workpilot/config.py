"""Application configuration."""

from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Global settings for a WorkPilot run."""

    provider: str = Field(default="stub", description="LLM provider name")
    model: str = Field(default="stub", description="Model name")
    max_steps: int = Field(default=30, ge=1)
    time_budget_seconds: int = Field(default=300, ge=1)
    token_budget: int = Field(default=100_000, ge=1)
    allowed_artifact_types: list[str] = Field(
        default_factory=lambda: [
            "weekly_report",
            "risks",
            "action_items",
            "verification_report",
            "trace",
        ]
    )
    forbidden_actions: list[str] = Field(default_factory=list)
