"""Request and response contracts for the WorkPilot Web API.

The API layer is a thin, read-only shell over the existing Runtime: it never
redefines domain models, it triggers a run and reads back the artifacts the
Runtime already writes to disk. These schemas only describe the HTTP surface.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    """Trigger a single run against a whitelisted workspace."""

    workspace: str = Field(min_length=1, description="Workspace name under the allowed root")
    goal: str = Field(min_length=1, description="Mission goal")
    provider: str = Field(default="stub", description="LLM provider name")


class RunStatus(BaseModel):
    """Live status of a triggered run, polled by the client."""

    run_id: str
    state: Literal[
        "pending",
        "planning",
        "retrieving",
        "synthesizing",
        "verifying",
        "revising",
        "running",
        "passed",
        "failed",
        "cancelled",
    ]
    goal: str
    workspace: str
    provider: str
    failure_reason: str | None = None


class RunSummary(BaseModel):
    """One row in the run history list."""

    run_id: str
    state: str
    goal: str
    workspace: str
    provider: str


class WorkspaceInfo(BaseModel):
    """A workspace the API is allowed to run against."""

    name: str
    file_count: int


class ReportResponse(BaseModel):
    """Rendered weekly report markdown for a completed run."""

    run_id: str
    markdown: str


class ArtifactResponse(BaseModel):
    """Raw JSON artifact (snapshot / verification / trace) for a run."""

    run_id: str
    artifact: str
    data: Any
