"""WorkPilot CLI — main entry point."""

import json
from pathlib import Path
from typing import Optional

import typer

from workpilot.providers.registry import (
    AVAILABLE,
    get_provider,
    get_provider_descriptor,
)
from workpilot.evaluation import EvalRunner, load_eval_suite
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
    model: Optional[str] = typer.Option(None, help="Model name (overrides provider default)"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="Custom API base URL"),
    max_steps: int = typer.Option(30, help="Maximum execution steps"),
    time_budget_seconds: int = typer.Option(300, help="Time budget in seconds"),
    token_budget: int = typer.Option(100_000, help="Total model token budget"),
) -> None:
    """Execute a mission on a workspace."""
    if not workspace.exists():
        typer.echo(f"Error: workspace '{workspace}' does not exist.", err=True)
        raise typer.Exit(1)

    provider_kwargs: dict = {}
    if model:
        provider_kwargs["model"] = model
    if base_url:
        provider_kwargs["base_url"] = base_url

    llm_provider = get_provider(provider, **provider_kwargs)
    runtime = Runtime(
        workspace_root=workspace,
        goal=goal,
        output_dir=output,
        provider=llm_provider,
        max_steps=max_steps,
        time_budget_seconds=time_budget_seconds,
        token_budget=token_budget,
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


@app.command("eval")
def evaluate(
    suite: Path = typer.Option(..., help="Path to a JSON evaluation suite"),
    output: Path = typer.Option(..., help="Output directory for evaluation results"),
    provider: str = typer.Option("stub", help="LLM provider name"),
    model: Optional[str] = typer.Option(None, help="Model name override"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="Custom API base URL"),
) -> None:
    """Run a reproducible offline evaluation suite."""
    if not suite.exists():
        typer.echo(f"Error: evaluation suite '{suite}' does not exist.", err=True)
        raise typer.Exit(1)

    provider_kwargs: dict = {}
    if model:
        provider_kwargs["model"] = model
    if base_url:
        provider_kwargs["base_url"] = base_url

    eval_suite = load_eval_suite(suite)
    report = EvalRunner(
        provider_name=provider,
        provider_kwargs=provider_kwargs,
    ).run(eval_suite, output)
    summary = report.summary
    typer.echo(f"[WorkPilot] Evaluation completed: {report.suite_name}")
    typer.echo(f"  Cases: {summary.total_cases}")
    typer.echo(f"  Task completion: {summary.task_completion_rate:.2%}")
    typer.echo(f"  Evidence precision: {summary.evidence_precision:.2%}")
    typer.echo(f"  Evidence recall: {summary.evidence_recall:.2%}")
    typer.echo(f"  Claim support: {summary.claim_support_rate:.2%}")
    typer.echo(f"  Output: {(output / 'eval_report.json').resolve()}")


@app.command("providers")
def providers_list() -> None:
    """List registered model providers and adapter capabilities."""
    typer.echo("Registered WorkPilot providers:")
    for name in AVAILABLE:
        descriptor = get_provider_descriptor(name)
        model = descriptor.default_model or "<required>"
        structured = descriptor.capabilities.structured_output_mode
        typer.echo(
            f"  {name:<10} transport={descriptor.transport.value:<20} "
            f"model={model:<24} structured={structured}"
        )


if __name__ == "__main__":
    app()
