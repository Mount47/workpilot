"""CLI contract tests for discoverable Provider support."""

from pathlib import Path

from typer.testing import CliRunner

from workpilot.cli import app


def test_providers_command_lists_transports_and_gemini() -> None:
    result = CliRunner().invoke(app, ["providers"])

    assert result.exit_code == 0
    assert "openai" in result.stdout
    assert "claude" in result.stdout
    assert "deepseek" in result.stdout
    assert "glm" in result.stdout
    assert "gemini" in result.stdout
    assert "custom" in result.stdout
    assert "anthropic_native" in result.stdout
    assert "openai_compatible" in result.stdout


def test_run_rejects_missing_route_config(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--workspace",
            str(workspace),
            "--goal",
            "生成报告",
            "--output",
            str(tmp_path / "output"),
            "--route-config",
            str(tmp_path / "missing.json"),
        ],
    )

    assert result.exit_code == 1
    assert "route config" in result.output


def test_serve_requires_persistent_storage_or_explicit_dev_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspaces"
    workspace.mkdir()
    monkeypatch.delenv("WORKPILOT_DATABASE_URL", raising=False)

    result = CliRunner().invoke(
        app,
        ["serve", "--workspace-root", str(workspace)],
    )

    assert result.exit_code == 1
    assert "persistent run storage is required" in result.output
    assert "--in-memory-runs" in result.output


def test_serve_allows_explicit_in_memory_development_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspaces"
    workspace.mkdir()
    captured = {}
    monkeypatch.delenv("WORKPILOT_DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "uvicorn.run",
        lambda app_instance, **kwargs: captured.update(
            {"app": app_instance, **kwargs}
        ),
    )

    result = CliRunner().invoke(
        app,
        [
            "serve",
            "--workspace-root",
            str(workspace),
            "--runs-root",
            str(tmp_path / "runs"),
            "--in-memory-runs",
        ],
    )

    assert result.exit_code == 0
    assert "in-memory (development)" in result.output
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8000
