"""Tests for the WorkPilot Web API shell.

These exercise the HTTP contract end-to-end with the deterministic stub
provider: trigger a run, poll to a terminal state, and read artifacts back.
Because runs execute on a background thread, tests poll the status endpoint.
"""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workpilot.api import create_app

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "workspaces"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(
        workspace_root=FIXTURE_ROOT,
        runs_root=tmp_path / "runs",
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
