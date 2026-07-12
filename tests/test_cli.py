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
