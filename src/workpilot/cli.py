"""WorkPilot CLI — main entry point."""

import json
from pathlib import Path
from typing import Optional

import typer

from workpilot.config import Settings
from workpilot.providers.preflight import (
    ProviderConfigurationError,
    ProviderPreflightResult,
    inspect_provider_configuration,
)
from workpilot.providers.registry import (
    AVAILABLE,
    get_provider,
    get_provider_descriptor,
)
from workpilot.providers.routing_config import load_model_routing_config
from workpilot.evaluation import (
    EvalRunner,
    PlannerEvalRunner,
    load_eval_suite,
    load_planner_eval_suite,
)
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
    route_config: Optional[Path] = typer.Option(
        None,
        "--route-config",
        help="JSON model routing configuration",
    ),
) -> None:
    """Execute a mission on a workspace."""
    if not workspace.exists():
        typer.echo(f"Error: workspace '{workspace}' does not exist.", err=True)
        raise typer.Exit(1)
    if route_config is not None and not route_config.exists():
        typer.echo(f"Error: route config '{route_config}' does not exist.", err=True)
        raise typer.Exit(1)

    provider_kwargs: dict = {}
    if model:
        provider_kwargs["model"] = model
    if base_url:
        provider_kwargs["base_url"] = base_url

    try:
        llm_provider = get_provider(provider, **provider_kwargs)
    except (ProviderConfigurationError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    model_router = None
    if route_config is not None:
        try:
            model_router = load_model_routing_config(route_config).build_router()
        except (ValueError, json.JSONDecodeError) as exc:
            typer.echo(f"Error: invalid route config: {exc}", err=True)
            raise typer.Exit(1) from exc
    runtime = Runtime(
        workspace_root=workspace,
        goal=goal,
        output_dir=output,
        provider=llm_provider,
        model_router=model_router,
        max_steps=max_steps,
        time_budget_seconds=time_budget_seconds,
        token_budget=token_budget,
    )

    typer.echo(f"[WorkPilot] Starting run: {runtime.run_id}")
    typer.echo(f"  Workspace: {workspace.resolve()}")
    typer.echo(f"  Goal: {goal}")
    typer.echo(f"  Provider: {provider}")
    if route_config is not None:
        typer.echo(f"  Route config: {route_config.resolve()}")
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
    typer.echo(f"  Source coverage: {summary.source_coverage_rate:.2%}")
    typer.echo(f"  Evidence acceptance: {summary.evidence_acceptance_rate:.2%}")
    typer.echo(
        f"  Claim source coverage: {summary.claim_source_coverage_rate:.2%}"
    )
    typer.echo(
        f"  Evidence repair trigger: {summary.evidence_repair_trigger_rate:.2%}"
    )
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


@app.command("doctor")
def provider_doctor(
    provider: Optional[str] = typer.Option(
        None,
        help="Provider to inspect; defaults to WORKPILOT_PROVIDER",
    ),
    model: Optional[str] = typer.Option(None, help="Model override to inspect"),
    base_url: Optional[str] = typer.Option(
        None,
        "--base-url",
        help="API base URL override to inspect",
    ),
    route_config: Optional[Path] = typer.Option(
        None,
        "--route-config",
        help="Inspect every target in a JSON model route configuration",
    ),
) -> None:
    """Check API configuration offline without exposing keys or using tokens."""
    settings = Settings()
    results: list[tuple[str | None, ProviderPreflightResult]] = []
    if route_config is not None:
        if not route_config.exists():
            typer.echo(f"Error: route config '{route_config}' does not exist.", err=True)
            raise typer.Exit(1)
        try:
            config = load_model_routing_config(route_config)
            for route in config.routes:
                for target in route.targets:
                    required_capabilities = [
                        name
                        for name, required in {
                            "structured_output": route.requirements.structured_output,
                            "native_tools": route.requirements.native_tools,
                            "multimodal_input": route.requirements.multimodal_input,
                        }.items()
                        if required
                    ]
                    result = inspect_provider_configuration(
                        target.provider,
                        model=target.model,
                        required_capabilities=required_capabilities,
                        settings=settings,
                    )
                    results.append((route.task_type.value, result))
        except (ValueError, json.JSONDecodeError) as exc:
            typer.echo(f"Error: invalid route config: {exc}", err=True)
            raise typer.Exit(1) from exc
    else:
        selected = provider or settings.workpilot_provider
        try:
            results.append(
                (
                    None,
                    inspect_provider_configuration(
                        selected,
                        model=model,
                        base_url=base_url,
                        settings=settings,
                    ),
                )
            )
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc

    typer.echo("[WorkPilot] Offline provider preflight")
    for index, (task_type, result) in enumerate(results, start=1):
        if len(results) > 1:
            typer.echo(f"Target {index}: {task_type or 'default'}")
        _print_preflight_result(result)
        if index < len(results):
            typer.echo()
    ready = all(result.ready for _, result in results)
    typer.echo()
    typer.echo(f"Overall ready: {'yes' if ready else 'no'}")
    typer.echo("Network request sent: no")
    if not ready:
        raise typer.Exit(1)


@app.command("planner-eval")
def planner_evaluate(
    suite: Path = typer.Option(..., help="Path to a Planner evaluation suite"),
    output: Path = typer.Option(..., help="Output directory for evaluation results"),
) -> None:
    """Run an offline Planner safety and fallback evaluation suite."""
    if not suite.exists():
        typer.echo(f"Error: Planner evaluation suite '{suite}' does not exist.", err=True)
        raise typer.Exit(1)
    report = PlannerEvalRunner().run(load_planner_eval_suite(suite), output)
    summary = report.summary
    typer.echo(f"[WorkPilot] Planner evaluation completed: {report.suite_name}")
    typer.echo(f"  Cases: {summary.total_cases}")
    typer.echo(f"  Decision accuracy: {summary.decision_accuracy:.2%}")
    typer.echo(f"  Plan validity: {summary.plan_validity_rate:.2%}")
    typer.echo(f"  Fallback rate: {summary.fallback_rate:.2%}")
    typer.echo(f"  Unsafe acceptance: {summary.unsafe_acceptance_rate:.2%}")
    typer.echo(f"  Output: {(output / 'planner_eval_report.json').resolve()}")


def _print_preflight_result(result: ProviderPreflightResult) -> None:
    typer.echo(f"  Provider: {result.provider}")
    typer.echo(f"  Transport: {result.transport}")
    typer.echo(f"  Model: {result.model or '<missing>'}")
    typer.echo(f"  Base URL: {result.base_url or '<not required>'}")
    typer.echo(f"  API key env: {result.api_key_env or '<not required>'}")
    typer.echo(
        "  API key configured: "
        + ("yes" if result.api_key_configured else "no")
    )
    typer.echo(f"  Structured output: {result.structured_output_mode}")
    typer.echo(f"  Ready: {'yes' if result.ready else 'no'}")
    for issue in result.issues:
        typer.echo(f"  Issue [{issue.code}]: {issue.message}")


if __name__ == "__main__":
    app()
