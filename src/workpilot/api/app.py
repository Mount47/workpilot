"""FastAPI application exposing WorkPilot runs over HTTP.

Design stance (see docs/dev/前端方案-web-app.md):
- The API is a thin shell: it triggers ``Runtime.execute()`` on a background
  thread and reads back the artifacts the Runtime already writes to disk.
- Runs are tracked in an in-memory registry. Single-instance, short-lived —
  consistent with the project's deliberate "in-memory is the right tradeoff"
  position. Persistence is roadmap phase 7, not this layer's job.
- Security: this service can trigger arbitrary workspace analysis and spend
  real API-key budget. It is bound to localhost and restricted to a workspace
  whitelist root. Do NOT expose it publicly without adding authentication.
"""

import threading
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from workpilot.api.routes import register_routes
from workpilot.providers import get_provider
from workpilot.providers.registry import AVAILABLE
from workpilot.runtime.runner import Runtime


@dataclass
class _RunRecord:
    """In-memory bookkeeping for one triggered run."""

    run_id: str
    goal: str
    workspace: str
    provider: str
    output_dir: Path
    state: str = "pending"
    failure_reason: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def create_app(
    workspace_root: Path,
    runs_root: Path,
    allowed_providers: list[str] | None = None,
) -> FastAPI:
    """Build the API bound to a workspace whitelist root and a runs directory.

    ``workspace_root`` is the ONLY directory whose sub-folders may be analyzed;
    request-supplied workspace names are resolved under it and path-escapes are
    rejected. ``runs_root`` is where per-run artifacts are written.
    """
    workspace_root = workspace_root.resolve()
    runs_root = runs_root.resolve()
    runs_root.mkdir(parents=True, exist_ok=True)
    providers = allowed_providers if allowed_providers is not None else list(AVAILABLE)

    app = FastAPI(title="WorkPilot API", version="0.1.0")
    # Local dev only: the Vite dev server runs on a different port.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    registry: dict[str, _RunRecord] = {}
    registry_lock = threading.Lock()

    def _resolve_workspace(name: str) -> Path:
        """Resolve a workspace name under the root, rejecting path escapes."""
        candidate = (workspace_root / name).resolve()
        if candidate != workspace_root and workspace_root not in candidate.parents:
            raise HTTPException(status_code=400, detail="workspace outside allowed root")
        if not candidate.is_dir():
            raise HTTPException(status_code=404, detail=f"workspace '{name}' not found")
        return candidate

    def _execute(record: _RunRecord, workspace_path: Path) -> None:
        """Run the pipeline on a background thread and record the outcome."""
        try:
            provider = get_provider(record.provider)
            runtime = Runtime(
                workspace_root=workspace_path,
                goal=record.goal,
                output_dir=record.output_dir,
                provider=provider,
            )
            with record.lock:
                record.state = "running"
            result = runtime.execute()
            with record.lock:
                record.state = result.state.value
                record.failure_reason = result.failure_reason
        except Exception as exc:  # noqa: BLE001 — surface any failure as run state
            with record.lock:
                record.state = "failed"
                record.failure_reason = str(exc)

    register_routes(
        app,
        registry,
        registry_lock,
        providers,
        workspace_root,
        runs_root,
        _resolve_workspace,
        _execute,
        _RunRecord,
    )
    return app
