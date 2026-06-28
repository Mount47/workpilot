"""WorkPilot CLI — main entry point."""

import json
from pathlib import Path
from typing import Optional

import typer

from workpilot.providers.registry import get_provider
from workpilot.runtime.runner import Runtime

app = typer.Typer(
    name="workpilot",
    help="Evidence-gated agent for project knowledge work.",
    no_args_is_help=True,
)


@app.command()
def run(
    workspace: Path = typer.Option(..., help="Path to workspace directory"),
    goal: str = typer.Option(..., help="Mission goal"),
    output: Path = typer.Option(..., help="Output directory for artifacts"),
    provider: str = typer.Option("stub", help="LLM provider name"),
    max_steps: int = typer.Option(30, help="Maximum execution steps"),
    time_budget_seconds: int = typer.Option(300, help="Time budget in seconds"),
) -> None:
    """Execute a mission on a workspace."""
    if not workspace.exists():
        typer.echo(f"Error: workspace '{workspace}' does not exist.", err=True)
        raise typer.Exit(1)

    llm_provider = get_provider(provider)
    runtime = Runtime(
        workspace_root=workspace,
        goal=goal,
        output_dir=output,
        provider=llm_provider,
        max_steps=max_steps,
        time_budget_seconds=time_budget_seconds,
    )

    typer.echo(f"[WorkPilot] Starting run: {runtime.run_id}")
    typer.echo(f"  Workspace: {workspace.resolve()}")
    typer.echo(f"  Goal: {goal}")
    typer.echo(f"  Provider: {provider}")
    typer.echo()

    result = runtime.execute()

    typer.echo(f"[WorkPilot] Run completed: {result.state.value}")
    typer.echo(f"  Output: {output.resolve()}")

    if result.failure_reason:
        typer.echo(f"  Failure: {result.failure_reason}", err=True)
        raise typer.Exit(1)


@app.command()
def verify(
    workspace: Path = typer.Option(..., help="Path to workspace directory"),
    run_dir: Path = typer.Option(..., "--run", help="Path to run output directory"),
) -> None:
    """Re-run verification on existing artifacts."""
    typer.echo("[WorkPilot] Verification not yet implemented (Phase 3).")
    raise typer.Exit(0)


@app.command("trace")
def trace_show(
    run_dir: Path = typer.Option(..., "--run", help="Path to run output directory"),
) -> None:
    """Show trace for a run."""
    trace_path = run_dir / "trace.json"
    if not trace_path.exists():
        typer.echo(f"Error: trace.json not found in {run_dir}", err=True)
        raise typer.Exit(1)

    data = json.loads(trace_path.read_text(encoding="utf-8"))
    typer.echo(f"Run: {data.get('run_id', 'unknown')}")
    typer.echo(f"Events: {data.get('event_count', 0)}")
    typer.echo()
    for event in data.get("events", []):
        typer.echo(
            f"  [{event['sequence']}] {event['event_type']} — {event['timestamp']}"
        )


if __name__ == "__main__":
    app()
