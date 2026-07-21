"""Route registration for the WorkPilot API.

Kept separate from app construction so create_app stays focused on wiring
(whitelist, registry, background executor) and this module owns the HTTP
contract. All read endpoints serve artifacts the Runtime wrote to disk.
"""

import json
import threading
import uuid
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException

from workpilot.api.models import (
    ArtifactResponse,
    ReportResponse,
    RunRequest,
    RunStatus,
    RunSummary,
    WorkspaceInfo,
)

# Imported lazily-safe names from app module at call time to avoid a cycle.
_REPORT_FILE = "weekly_report.md"
_ARTIFACT_FILES = {
    "snapshot": "project_snapshot.json",
    "verification": "verification_report.json",
    "trace": "trace.json",
    "evidence": "evidence.json",
    "action_items": "action_items.json",
    "risks": "risks.json",
}


def register_routes(
    app: FastAPI,
    registry: dict,
    registry_lock: threading.Lock,
    providers: list[str],
    workspace_root: Path,
    runs_root: Path,
    resolve_workspace: Callable[[str], Path],
    execute: Callable[[object, Path], None],
    record_factory: Callable[..., object],
) -> None:
    """Attach all HTTP routes to ``app``."""

    def _status(record) -> RunStatus:
        with record.lock:
            return RunStatus(
                run_id=record.run_id,
                state=record.state,
                goal=record.goal,
                workspace=record.workspace,
                provider=record.provider,
                failure_reason=record.failure_reason,
            )

    def _require(run_id: str):
        with registry_lock:
            record = registry.get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
        return record

    @app.get("/api/providers")
    def list_providers() -> list[str]:
        return providers

    @app.get("/api/workspaces")
    def list_workspaces() -> list[WorkspaceInfo]:
        result: list[WorkspaceInfo] = []
        for child in sorted(workspace_root.iterdir()):
            if child.is_dir():
                file_count = sum(1 for p in child.rglob("*") if p.is_file())
                result.append(WorkspaceInfo(name=child.name, file_count=file_count))
        return result

    @app.post("/api/runs", status_code=202)
    def create_run(request: RunRequest) -> RunStatus:
        if request.provider not in providers:
            raise HTTPException(
                status_code=400,
                detail=f"provider '{request.provider}' not allowed",
            )
        workspace_path = resolve_workspace(request.workspace)
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        record = record_factory(
            run_id=run_id,
            goal=request.goal,
            workspace=request.workspace,
            provider=request.provider,
            output_dir=(runs_root / run_id),
        )
        with registry_lock:
            registry[run_id] = record
        thread = threading.Thread(
            target=execute, args=(record, workspace_path), daemon=True
        )
        thread.start()
        return _status(record)

    @app.get("/api/runs")
    def list_runs() -> list[RunSummary]:
        with registry_lock:
            records = list(registry.values())
        return [
            RunSummary(
                run_id=r.run_id,
                state=r.state,
                goal=r.goal,
                workspace=r.workspace,
                provider=r.provider,
            )
            for r in records
        ]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> RunStatus:
        return _status(_require(run_id))

    @app.get("/api/runs/{run_id}/report")
    def get_report(run_id: str) -> ReportResponse:
        record = _require(run_id)
        path = record.output_dir / _REPORT_FILE
        if not path.exists():
            raise HTTPException(status_code=409, detail="report not ready")
        return ReportResponse(run_id=run_id, markdown=path.read_text(encoding="utf-8"))

    @app.get("/api/runs/{run_id}/artifacts/{artifact}")
    def get_artifact(run_id: str, artifact: str) -> ArtifactResponse:
        record = _require(run_id)
        filename = _ARTIFACT_FILES.get(artifact)
        if filename is None:
            raise HTTPException(status_code=404, detail=f"unknown artifact '{artifact}'")
        path = record.output_dir / filename
        if not path.exists():
            raise HTTPException(status_code=409, detail=f"artifact '{artifact}' not ready")
        return ArtifactResponse(
            run_id=run_id,
            artifact=artifact,
            data=json.loads(path.read_text(encoding="utf-8")),
        )

    @app.get("/api/runs/{run_id}/source/{source_id}")
    def get_source(run_id: str, source_id: str) -> dict:
        """Return a source file's lines so the UI can highlight evidence ranges.

        The path is resolved strictly under the run's workspace to prevent
        traversal; ``source_id`` is the evidence locator's source file name.
        """
        record = _require(run_id)
        workspace_path = resolve_workspace(record.workspace)
        candidate = (workspace_path / source_id).resolve()
        if workspace_path not in candidate.parents and candidate != workspace_path:
            raise HTTPException(status_code=400, detail="source outside workspace")
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail=f"source '{source_id}' not found")
        lines = candidate.read_text(encoding="utf-8").splitlines()
        return {"run_id": run_id, "source_id": source_id, "lines": lines}
