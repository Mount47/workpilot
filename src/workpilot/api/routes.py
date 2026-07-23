"""Route registration for the WorkPilot API.

Kept separate from app construction so create_app stays focused on wiring
(whitelist, repository, background executor) and this module owns the HTTP
contract. All read endpoints serve artifacts the Runtime wrote to disk.
"""

import json
import hashlib
import threading
import uuid
from pathlib import Path
from typing import Annotated, Callable

from fastapi import FastAPI, Header, HTTPException

from workpilot.api.models import (
    ArtifactResponse,
    ReportResponse,
    RunRequest,
    RunStatus,
    RunSummary,
    WorkspaceInfo,
)
from workpilot.persistence import (
    IdempotencyConflictError,
    RepositoryUnavailableError,
    RunAlreadyExistsError,
    RunRecord,
    RunRepository,
    RunRepositoryError,
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
    repository: RunRepository,
    providers: list[str],
    workspace_root: Path,
    runs_root: Path,
    resolve_workspace: Callable[[str], Path],
    execute: Callable[[RunRecord, Path], None],
) -> None:
    """Attach all HTTP routes to ``app``."""

    def _status(record: RunRecord) -> RunStatus:
        return RunStatus(
            run_id=record.run_id,
            state=record.state,
            goal=record.goal,
            workspace=record.workspace,
            provider=record.provider,
            failure_reason=record.failure_reason,
        )

    def _require(run_id: str) -> RunRecord:
        try:
            record = repository.get(run_id)
        except RunRepositoryError as exc:
            raise HTTPException(
                status_code=503, detail="run repository unavailable"
            ) from exc
        if record is None:
            raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
        return record

    def _output_dir(record: RunRecord) -> Path:
        path = record.output_dir.resolve()
        if path != runs_root and runs_root not in path.parents:
            raise HTTPException(status_code=500, detail="invalid persisted output path")
        return path

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
    def create_run(
        request: RunRequest,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key", min_length=1, max_length=256),
        ] = None,
    ) -> RunStatus:
        if request.provider not in providers:
            raise HTTPException(
                status_code=400,
                detail=f"provider '{request.provider}' not allowed",
            )
        workspace_path = resolve_workspace(request.workspace)
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        record = RunRecord(
            run_id=run_id,
            goal=request.goal,
            workspace=request.workspace,
            provider=request.provider,
            output_dir=(runs_root / run_id),
        )
        try:
            if idempotency_key is None:
                repository.create(record)
                created = True
            else:
                if not idempotency_key.strip():
                    raise HTTPException(
                        status_code=422,
                        detail="idempotency key must not be blank",
                    )
                key_hash = hashlib.sha256(
                    idempotency_key.encode("utf-8")
                ).hexdigest()
                canonical_request = json.dumps(
                    request.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                request_fingerprint = hashlib.sha256(
                    canonical_request.encode("utf-8")
                ).hexdigest()
                create_result = repository.create_or_get(
                    record,
                    key_hash=key_hash,
                    request_fingerprint=request_fingerprint,
                )
                record = create_result.record
                created = create_result.created
        except IdempotencyConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail="idempotency key conflict",
            ) from exc
        except RunAlreadyExistsError as exc:
            raise HTTPException(status_code=409, detail="run id collision") from exc
        except RepositoryUnavailableError as exc:
            raise HTTPException(
                status_code=503, detail="run repository unavailable"
            ) from exc
        if not created:
            return _status(record)
        thread = threading.Thread(
            target=execute, args=(record, workspace_path), daemon=True
        )
        try:
            thread.start()
        except RuntimeError as exc:
            try:
                repository.update_state(run_id, "failed", "run executor unavailable")
            except RunRepositoryError:
                pass
            raise HTTPException(
                status_code=503, detail="run executor unavailable"
            ) from exc
        return _status(record)

    @app.get("/api/runs")
    def list_runs() -> list[RunSummary]:
        try:
            records = repository.list()
        except RunRepositoryError as exc:
            raise HTTPException(
                status_code=503, detail="run repository unavailable"
            ) from exc
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
        path = _output_dir(record) / _REPORT_FILE
        if not path.exists():
            raise HTTPException(status_code=409, detail="report not ready")
        return ReportResponse(run_id=run_id, markdown=path.read_text(encoding="utf-8"))

    @app.get("/api/runs/{run_id}/artifacts/{artifact}")
    def get_artifact(run_id: str, artifact: str) -> ArtifactResponse:
        record = _require(run_id)
        filename = _ARTIFACT_FILES.get(artifact)
        if filename is None:
            raise HTTPException(status_code=404, detail=f"unknown artifact '{artifact}'")
        path = _output_dir(record) / filename
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
