"""FastAPI application exposing WorkPilot runs over HTTP.

Design stance:
- The API is a thin shell: it triggers ``Runtime.execute()`` on a background
  thread and reads back the artifacts the Runtime already writes to disk.
- Run identity and lifecycle are accessed through ``RunRepository``. A
  PostgreSQL adapter provides durable history; the in-memory adapter is only
  an explicit development and test fallback.
- Security: this service can trigger arbitrary workspace analysis and spend
  real API-key budget. It is bound to localhost and restricted to a workspace
  whitelist root. Do NOT expose it publicly without adding authentication.
"""

import logging
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from workpilot.api.routes import register_routes
from workpilot.persistence import ExecutionRepository, RunRecord, RunRepository
from workpilot.persistence.execution_observer import RepositoryExecutionObserver
from workpilot.persistence.repository import RunRepositoryError
from workpilot.providers import get_provider
from workpilot.providers.registry import AVAILABLE
from workpilot.runtime.lease import LeaseHeartbeat
from workpilot.runtime.runner import Runtime

logger = logging.getLogger(__name__)


def create_app(
    workspace_root: Path,
    runs_root: Path,
    run_repository: RunRepository,
    allowed_providers: list[str] | None = None,
    lease_ttl_seconds: int = 30,
    heartbeat_seconds: int = 10,
) -> FastAPI:
    """Build the API bound to a workspace whitelist root and a runs directory.

    ``run_repository`` is required so no caller can silently fall back to
    process-local history. ``workspace_root`` is the ONLY directory whose
    sub-folders may be analyzed;
    request-supplied workspace names are resolved under it and path-escapes are
    rejected. ``runs_root`` is where per-run artifacts are written.
    """
    workspace_root = workspace_root.resolve()
    runs_root = runs_root.resolve()
    runs_root.mkdir(parents=True, exist_ok=True)
    providers = allowed_providers if allowed_providers is not None else list(AVAILABLE)
    repository = run_repository
    if heartbeat_seconds * 3 > lease_ttl_seconds:
        raise ValueError("heartbeat_seconds must not exceed one third of lease TTL")

    app = FastAPI(title="WorkPilot API", version="0.1.0")
    # Local dev only: the Vite dev server runs on a different port.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.run_repository = repository
    app.router.add_event_handler("shutdown", repository.close)

    def _resolve_workspace(name: str) -> Path:
        """Resolve a workspace name under the root, rejecting path escapes."""
        candidate = (workspace_root / name).resolve()
        if candidate != workspace_root and workspace_root not in candidate.parents:
            raise HTTPException(status_code=400, detail="workspace outside allowed root")
        if not candidate.is_dir():
            raise HTTPException(status_code=404, detail=f"workspace '{name}' not found")
        return candidate

    def _execute(record: RunRecord, workspace_path: Path) -> None:
        """Run the pipeline on a background thread and record the outcome."""
        lease = None
        heartbeat: LeaseHeartbeat | None = None
        try:
            lease = repository.claim_run(
                record.run_id,
                secrets.token_urlsafe(32),
                ttl_seconds=lease_ttl_seconds,
            )
            provider = get_provider(record.provider)
            execution_observer = (
                RepositoryExecutionObserver(
                    repository,
                    run_id=record.run_id,
                    output_dir=record.output_dir,
                    lease=lease,
                )
                if isinstance(repository, ExecutionRepository)
                else None
            )
            runtime: Runtime | None = None

            def record_lease_event(event_type: str, data: dict) -> None:
                if runtime is not None:
                    runtime.trace.append(
                        event_type=event_type,
                        data=data,
                        parent_step_id="run",
                    )

            heartbeat = LeaseHeartbeat(
                repository,
                lease,
                ttl_seconds=lease_ttl_seconds,
                heartbeat_seconds=heartbeat_seconds,
                on_event=record_lease_event,
            )
            runtime = Runtime(
                workspace_root=workspace_path,
                goal=record.goal,
                output_dir=record.output_dir,
                provider=provider,
                run_id=record.run_id,
                execution_observer=execution_observer,
                execution_guard=heartbeat,
            )
            heartbeat.start()
            result = runtime.execute()
            repository.commit_terminal(
                heartbeat.lease,
                result.state.value,
                result.failure_reason,
            )
        except Exception as exc:  # noqa: BLE001 — surface any failure as run state
            try:
                terminal_lease = heartbeat.lease if heartbeat is not None else lease
                if terminal_lease is None:
                    logger.error(
                        "run failed before lease heartbeat started",
                        extra={
                            "run_id": record.run_id,
                            "error_type": type(exc).__name__,
                        },
                    )
                    return
                repository.commit_terminal(terminal_lease, "failed", str(exc))
            except RunRepositoryError:
                # The durable store is unavailable, so there is nowhere safer
                # to mirror the failure. Infrastructure observability is added
                # in P1; never fall back to a split-brain in-memory history.
                logger.error(
                    "run repository unavailable while persisting terminal state",
                    extra={"run_id": record.run_id},
                )
                return
        finally:
            if heartbeat is not None:
                heartbeat.stop()

    register_routes(
        app,
        repository,
        providers,
        workspace_root,
        runs_root,
        _resolve_workspace,
        _execute,
    )
    return app
