"""CLI contract tests for discoverable Provider support."""

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
