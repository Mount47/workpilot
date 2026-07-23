"""Tests for the WorkPilot Web API shell.

These exercise the HTTP contract end-to-end with the deterministic stub
provider: trigger a run, poll to a terminal state, and read artifacts back.
Because runs execute on a background thread, tests poll the status endpoint.
"""

import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from workpilot.api import create_app
from workpilot.persistence import (
    InMemoryRunRepository,
    RepositoryUnavailableError,
    RunRecord,
)
from workpilot.persistence.sqlalchemy_repository import SQLAlchemyRunRepository

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "workspaces"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(
        workspace_root=FIXTURE_ROOT,
        runs_root=tmp_path / "runs",
        run_repository=InMemoryRunRepository(),
        allowed_providers=["stub"],
    )
    return TestClient(app)


def _wait_terminal(client: TestClient, run_id: str, timeout: float = 10.0) -> dict:
    """Poll the status endpoint until the run reaches a terminal state."""
    deadline = time.monotonic() + timeout
    terminal = {"passed", "failed", "cancelled"}
    while time.monotonic() < deadline:
        resp = client.get(f"/api/runs/{run_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["state"] in terminal:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def test_list_providers(client: TestClient) -> None:
    resp = client.get("/api/providers")
    assert resp.status_code == 200
    assert resp.json() == ["stub"]


def test_list_workspaces(client: TestClient) -> None:
    resp = client.get("/api/workspaces")
    assert resp.status_code == 200
    names = {w["name"] for w in resp.json()}
    assert {"basic_project", "rich_project"} <= names
    for w in resp.json():
        assert w["file_count"] > 0


def test_run_lifecycle_and_report(client: TestClient) -> None:
    resp = client.post(
        "/api/runs",
        json={"workspace": "basic_project", "goal": "生成本周项目周报", "provider": "stub"},
    )
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    assert run_id.startswith("run_")

    final = _wait_terminal(client, run_id)
    assert final["state"] == "passed"

    report = client.get(f"/api/runs/{run_id}/report")
    assert report.status_code == 200
    assert "## " in report.json()["markdown"]

    snapshot = client.get(f"/api/runs/{run_id}/artifacts/snapshot")
    assert snapshot.status_code == 200
    assert "claims" in snapshot.json()["data"]

    verification = client.get(f"/api/runs/{run_id}/artifacts/verification")
    assert verification.status_code == 200
    # The frontend VerificationPanel depends on this shape — assert it so a
    # backend schema drift that would break the UI is caught here.
    vdata = verification.json()["data"]
    assert vdata["status"] in ("passed", "failed", "incomplete")
    assert vdata["total_checks"] == len(vdata["checks"])
    assert "error_count" in vdata
    check = vdata["checks"][0]
    for field in ("check_id", "status", "severity", "location", "message"):
        assert field in check


def test_run_appears_in_history(client: TestClient) -> None:
    resp = client.post(
        "/api/runs",
        json={"workspace": "basic_project", "goal": "周报", "provider": "stub"},
    )
    run_id = resp.json()["run_id"]
    _wait_terminal(client, run_id)
    listing = client.get("/api/runs").json()
    assert any(r["run_id"] == run_id for r in listing)


def test_idempotency_key_reuses_run_and_starts_once(tmp_path: Path) -> None:
    class CountingRepository(InMemoryRunRepository):
        def __init__(self) -> None:
            super().__init__()
            self.claims = 0

        def claim_run(
            self,
            run_id: str,
            owner_token: str,
            *,
            ttl_seconds: int,
        ):
            self.claims += 1
            return super().claim_run(
                run_id,
                owner_token,
                ttl_seconds=ttl_seconds,
            )

    repository = CountingRepository()
    app = create_app(
        workspace_root=FIXTURE_ROOT,
        runs_root=tmp_path / "runs",
        run_repository=repository,
        allowed_providers=["stub"],
    )
    payload = {"workspace": "basic_project", "goal": "周报", "provider": "stub"}
    headers = {"Idempotency-Key": "same-request-key"}

    with TestClient(app) as idempotent_client:
        first = idempotent_client.post("/api/runs", json=payload, headers=headers)
        repeated = idempotent_client.post("/api/runs", json=payload, headers=headers)
        assert first.status_code == 202
        assert repeated.status_code == 202
        assert repeated.json()["run_id"] == first.json()["run_id"]
        assert _wait_terminal(idempotent_client, first.json()["run_id"])[
            "state"
        ] == "passed"

    assert repository.claims == 1
    assert len(repository.list()) == 1
    persisted = repository.get(first.json()["run_id"])
    assert persisted.execution_attempt == 1
    assert persisted.lease_owner is None
    assert "same-request-key" not in first.text
    assert "same-request-key" not in repeated.text


def test_idempotency_key_rejects_different_request(client: TestClient) -> None:
    headers = {"Idempotency-Key": "conflicting-request-key"}
    first = client.post(
        "/api/runs",
        json={"workspace": "basic_project", "goal": "周报", "provider": "stub"},
        headers=headers,
    )
    conflict = client.post(
        "/api/runs",
        json={
            "workspace": "basic_project",
            "goal": "不同目标",
            "provider": "stub",
        },
        headers=headers,
    )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "idempotency key conflict"}


def test_missing_idempotency_key_keeps_create_new_behavior(
    client: TestClient,
) -> None:
    payload = {"workspace": "basic_project", "goal": "周报", "provider": "stub"}

    first = client.post("/api/runs", json=payload)
    second = client.post("/api/runs", json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["run_id"] != second.json()["run_id"]


@pytest.mark.parametrize("key", ["", "x" * 257])
def test_invalid_idempotency_key_is_rejected(
    client: TestClient,
    key: str,
) -> None:
    response = client.post(
        "/api/runs",
        json={"workspace": "basic_project", "goal": "周报", "provider": "stub"},
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 422


def test_run_history_survives_app_reconstruction(tmp_path: Path) -> None:
    repository = InMemoryRunRepository()
    runs_root = tmp_path / "runs"
    first_app = create_app(
        workspace_root=FIXTURE_ROOT,
        runs_root=runs_root,
        allowed_providers=["stub"],
        run_repository=repository,
    )
    with TestClient(first_app) as first_client:
        response = first_client.post(
            "/api/runs",
            json={"workspace": "basic_project", "goal": "周报", "provider": "stub"},
        )
        run_id = response.json()["run_id"]
        assert _wait_terminal(first_client, run_id)["state"] == "passed"
        trace = json.loads(
            (runs_root / run_id / "trace.json").read_text(encoding="utf-8")
        )
        context = json.loads(
            (runs_root / run_id / "run_context.json").read_text(encoding="utf-8")
        )
        assert trace["run_id"] == run_id
        assert context["run_id"] == run_id

    rebuilt_app = create_app(
        workspace_root=FIXTURE_ROOT,
        runs_root=runs_root,
        allowed_providers=["stub"],
        run_repository=repository,
    )
    with TestClient(rebuilt_app) as rebuilt_client:
        assert rebuilt_client.get(f"/api/runs/{run_id}").json()["state"] == "passed"
        assert any(
            item["run_id"] == run_id
            for item in rebuilt_client.get("/api/runs").json()
        )
        assert rebuilt_client.get(f"/api/runs/{run_id}/report").status_code == 200


@pytest.mark.skipif(
    not os.environ.get("WORKPILOT_TEST_DATABASE_URL"),
    reason="dedicated PostgreSQL test database is not configured",
)
def test_postgresql_history_survives_repository_reconstruction(
    tmp_path: Path,
) -> None:
    database_url = os.environ["WORKPILOT_TEST_DATABASE_URL"]
    runs_root = tmp_path / "runs"
    goal = f"PostgreSQL restart acceptance {uuid.uuid4().hex}"

    first_repository = SQLAlchemyRunRepository.from_url(database_url)
    first_app = create_app(
        workspace_root=FIXTURE_ROOT,
        runs_root=runs_root,
        allowed_providers=["stub"],
        run_repository=first_repository,
    )
    with TestClient(first_app) as first_client:
        response = first_client.post(
            "/api/runs",
            json={"workspace": "basic_project", "goal": goal, "provider": "stub"},
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        assert _wait_terminal(first_client, run_id)["state"] == "passed"

    rebuilt_repository = SQLAlchemyRunRepository.from_url(database_url)
    rebuilt_app = create_app(
        workspace_root=FIXTURE_ROOT,
        runs_root=runs_root,
        allowed_providers=["stub"],
        run_repository=rebuilt_repository,
    )
    with TestClient(rebuilt_app) as rebuilt_client:
        persisted = rebuilt_client.get(f"/api/runs/{run_id}")
        assert persisted.status_code == 200
        assert persisted.json()["goal"] == goal
        assert persisted.json()["state"] == "passed"
        assert rebuilt_client.get(f"/api/runs/{run_id}/report").status_code == 200
        steps = rebuilt_repository.list_plan_steps(run_id)
        calls = rebuilt_repository.list_tool_calls(run_id)
        artifacts = rebuilt_repository.list_artifacts(run_id)
        assert len(steps) == 6
        assert len(calls) == 6
        assert {step.status for step in steps} == {"completed"}
        assert {call.state for call in calls} == {"completed"}
        assert {artifact.name for artifact in artifacts} >= {
            "trace.json",
            "run_context.json",
            "weekly_report.md",
        }


def test_repository_failure_prevents_run_start(tmp_path: Path) -> None:
    class UnavailableRepository(InMemoryRunRepository):
        def create(self, record: RunRecord) -> RunRecord:
            raise RepositoryUnavailableError("database unavailable")

    runs_root = tmp_path / "runs"
    app = create_app(
        workspace_root=FIXTURE_ROOT,
        runs_root=runs_root,
        allowed_providers=["stub"],
        run_repository=UnavailableRepository(),
    )
    with TestClient(app) as unavailable_client:
        response = unavailable_client.post(
            "/api/runs",
            json={"workspace": "basic_project", "goal": "周报", "provider": "stub"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "run repository unavailable"}
    assert not list(runs_root.iterdir())


def test_stale_api_worker_cannot_overwrite_takeover_state(tmp_path: Path) -> None:
    class Clock:
        now = datetime(2026, 7, 22, 8, 0, tzinfo=timezone.utc)

        def __call__(self):
            return self.now

    class TakeoverRepository(InMemoryRunRepository):
        def __init__(self, clock: Clock) -> None:
            super().__init__(clock=clock)
            self.clock = clock
            self.takeover_lease = None
            self.stale_terminal_attempted = Event()

        def assert_lease(self, lease):
            if self.takeover_lease is None:
                self.clock.now += timedelta(seconds=31)
                self.takeover_lease = super().claim_run(
                    lease.run_id,
                    "worker-b",
                    ttl_seconds=30,
                )
            return super().assert_lease(lease)

        def commit_terminal(self, lease, state, failure_reason=None):
            if self.takeover_lease is not None and lease != self.takeover_lease:
                self.stale_terminal_attempted.set()
            return super().commit_terminal(lease, state, failure_reason)

    clock = Clock()
    repository = TakeoverRepository(clock)
    app = create_app(
        workspace_root=FIXTURE_ROOT,
        runs_root=tmp_path / "runs",
        run_repository=repository,
        allowed_providers=["stub"],
    )
    with TestClient(app) as takeover_client:
        response = takeover_client.post(
            "/api/runs",
            json={
                "workspace": "basic_project",
                "goal": "report",
                "provider": "stub",
            },
        )
        run_id = response.json()["run_id"]
        assert repository.stale_terminal_attempted.wait(timeout=2)

    persisted = repository.get(run_id)
    assert persisted.state == "recovering"
    assert persisted.execution_attempt == 2
    assert persisted.lease_owner == "worker-b"


def test_unknown_workspace_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/runs",
        json={"workspace": "does_not_exist", "goal": "x", "provider": "stub"},
    )
    assert resp.status_code == 404


def test_workspace_path_escape_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/runs",
        json={"workspace": "../../etc", "goal": "x", "provider": "stub"},
    )
    assert resp.status_code == 400


def test_disallowed_provider_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/runs",
        json={"workspace": "basic_project", "goal": "x", "provider": "qwen"},
    )
    assert resp.status_code == 400


def test_evidence_artifact_carries_locators(client: TestClient) -> None:
    resp = client.post(
        "/api/runs",
        json={"workspace": "rich_project", "goal": "周报", "provider": "stub"},
    )
    run_id = resp.json()["run_id"]
    _wait_terminal(client, run_id)

    ev = client.get(f"/api/runs/{run_id}/artifacts/evidence")
    assert ev.status_code == 200
    records = ev.json()["data"]
    assert len(records) > 0
    first = records[0]
    assert first["evidence_id"].startswith("E-")
    assert first["quote"]
    assert first["locator"]["source_id"]


def test_source_endpoint_returns_lines(client: TestClient) -> None:
    resp = client.post(
        "/api/runs",
        json={"workspace": "rich_project", "goal": "周报", "provider": "stub"},
    )
    run_id = resp.json()["run_id"]
    _wait_terminal(client, run_id)

    ev = client.get(f"/api/runs/{run_id}/artifacts/evidence").json()["data"]
    source_id = ev[0]["locator"]["source_id"]
    src = client.get(f"/api/runs/{run_id}/source/{source_id}")
    assert src.status_code == 200
    assert isinstance(src.json()["lines"], list)
    assert len(src.json()["lines"]) > 0


def test_source_traversal_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/runs",
        json={"workspace": "rich_project", "goal": "周报", "provider": "stub"},
    )
    run_id = resp.json()["run_id"]
    _wait_terminal(client, run_id)
    # URL-encoded traversal must not escape the workspace.
    src = client.get(f"/api/runs/{run_id}/source/..%2f..%2fetc%2fpasswd")
    assert src.status_code in (400, 404)


def test_unknown_run_returns_404(client: TestClient) -> None:
    assert client.get("/api/runs/run_missing").status_code == 404


def test_unknown_artifact_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/api/runs",
        json={"workspace": "basic_project", "goal": "周报", "provider": "stub"},
    )
    run_id = resp.json()["run_id"]
    _wait_terminal(client, run_id)
    assert client.get(f"/api/runs/{run_id}/artifacts/nope").status_code == 404
