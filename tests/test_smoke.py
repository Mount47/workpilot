"""Smoke test — end-to-end run with StubProvider."""

import json
from pathlib import Path

from workpilot.providers.registry import get_provider
from workpilot.runtime.runner import Runtime


def test_smoke_run(basic_workspace: Path, tmp_output: Path) -> None:
    """Full pipeline with stub provider produces all expected artifacts."""
    provider = get_provider("stub")
    runtime = Runtime(
        workspace_root=basic_workspace,
        goal="生成本周项目周报",
        output_dir=tmp_output,
        provider=provider,
    )

    result = runtime.execute()

    # Run should pass
    assert result.state.value == "passed"
    assert result.failure_reason is None

    # All 5 artifacts should exist
    expected_files = [
        "weekly_report.md",
        "risks.json",
        "action_items.json",
        "verification_report.json",
        "trace.json",
    ]
    for f in expected_files:
        path = tmp_output / f
        assert path.exists(), f"Missing artifact: {f}"
        assert path.stat().st_size > 0, f"Empty artifact: {f}"

    # trace.json should have events
    trace = json.loads((tmp_output / "trace.json").read_text())
    assert trace["event_count"] >= 4
    assert trace["run_id"] == result.run_id

    # weekly_report.md should contain evidence references
    report = (tmp_output / "weekly_report.md").read_text()
    assert "[E-" in report

    # risks.json should be valid
    risks = json.loads((tmp_output / "risks.json").read_text())
    assert risks["schema_version"] == "0.1"
    assert len(risks["risks"]) >= 1

    # action_items.json should be valid
    actions = json.loads((tmp_output / "action_items.json").read_text())
    assert actions["schema_version"] == "0.1"
    assert len(actions["action_items"]) >= 1
    assert "source_refs" in actions["action_items"][0]
