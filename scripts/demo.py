"""Run and validate a deterministic, API-key-free WorkPilot demonstration."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE = ROOT / "tests" / "fixtures" / "workspaces" / "basic_project"
REQUIRED_ARTIFACTS = {
    "weekly_report.md",
    "evidence.json",
    "verification_report.json",
    "trace.json",
    "run_context.json",
    "project_snapshot.json",
}


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_demo(output_dir: Path) -> dict[str, int | str]:
    """Validate the user-visible evidence and verification contract."""
    missing = sorted(
        name for name in REQUIRED_ARTIFACTS if not (output_dir / name).is_file()
    )
    if missing:
        raise RuntimeError(f"missing required artifacts: {', '.join(missing)}")

    context = _load_json(output_dir / "run_context.json")
    evidences = _load_json(output_dir / "evidence.json")
    verification = _load_json(output_dir / "verification_report.json")
    trace = _load_json(output_dir / "trace.json")
    report = (output_dir / "weekly_report.md").read_text(encoding="utf-8")

    run_state = context.get("run_status")
    if run_state != "passed":
        raise RuntimeError(f"demo Run did not pass: {run_state}")
    if not isinstance(evidences, list) or not evidences:
        raise RuntimeError("demo produced no accepted Evidence")
    if verification.get("status") != "passed":
        raise RuntimeError("deterministic verification did not pass")
    verification_errors = verification.get("error_count")
    if verification_errors != 0:
        raise RuntimeError(
            f"verification contains {verification_errors!r} errors"
        )
    if "[E-" not in report:
        raise RuntimeError("report does not contain traceable Evidence citations")

    return {
        "run_state": str(run_state),
        "evidence_count": len(evidences),
        "verification_checks": int(verification.get("total_checks", 0)),
        "verification_errors": int(verification_errors),
        "trace_events": int(trace.get("event_count", 0)),
        "report_characters": len(report),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the offline Stub demo and validate its artifacts."
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=DEFAULT_WORKSPACE,
        help="Workspace to analyze (default: bundled basic_project fixture).",
    )
    parser.add_argument(
        "--goal",
        default="生成一份可追溯到原文证据的项目周报",
        help="Run goal passed to WorkPilot.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory; must not already exist.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = args.workspace.resolve()
    output_dir = (
        args.output.resolve()
        if args.output is not None
        else ROOT
        / "runs"
        / f"campus-demo-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    )
    if not workspace.is_dir():
        print(f"DEMO_FAILED workspace_not_found={workspace}", file=sys.stderr)
        return 2
    if output_dir.exists():
        print(f"DEMO_FAILED output_already_exists={output_dir}", file=sys.stderr)
        return 2

    environment = os.environ.copy()
    source_root = str(ROOT / "src")
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = source_root + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )
    command = [
        sys.executable,
        "-m",
        "workpilot.cli",
        "run",
        "--workspace",
        str(workspace),
        "--goal",
        args.goal,
        "--provider",
        "stub",
        "--output",
        str(output_dir),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        print("DEMO_FAILED workpilot_run_failed", file=sys.stderr)
        if completed.stdout:
            print(completed.stdout.decode("utf-8", errors="replace"), file=sys.stderr)
        if completed.stderr:
            print(completed.stderr.decode("utf-8", errors="replace"), file=sys.stderr)
        return completed.returncode

    try:
        summary = validate_demo(output_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"DEMO_FAILED validation_error={exc}", file=sys.stderr)
        return 1

    print("DEMO_OK")
    print(f"output_dir={output_dir}")
    for name, value in summary.items():
        print(f"{name}={value}")
    print(f"report={output_dir / 'weekly_report.md'}")
    print(f"trace={output_dir / 'trace.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
