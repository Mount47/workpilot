"""End-to-end contract for the interview-friendly offline demo."""

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).parents[1]


def test_demo_script_runs_stub_pipeline_and_validates_artifacts(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "campus-demo"
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = str(ROOT / "src") + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "demo.py"),
            "--output",
            str(output_dir),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "DEMO_OK" in completed.stdout
    assert "run_state=passed" in completed.stdout
    assert "evidence_count=" in completed.stdout
    assert "verification_errors=0" in completed.stdout
    assert "UnicodeDecodeError" not in completed.stderr

    expected_files = {
        "weekly_report.md",
        "evidence.json",
        "verification_report.json",
        "trace.json",
        "run_context.json",
        "project_snapshot.json",
    }
    assert expected_files <= {
        path.name for path in output_dir.iterdir() if path.is_file()
    }
    verification = json.loads(
        (output_dir / "verification_report.json").read_text(encoding="utf-8")
    )
    assert verification["status"] == "passed"
