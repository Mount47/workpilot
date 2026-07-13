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

    # All run artifacts should exist
    expected_files = [
        "weekly_report.md",
        "risks.json",
        "action_items.json",
        "project_snapshot.json",
        "plan.json",
        "run_context.json",
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
    assert all("event_id" in event for event in trace["events"])
    event_types = {event["event_type"] for event in trace["events"]}
    assert "step_started" in event_types
    assert "step_completed" in event_types
    assert "budget_summary" in event_types
    assert "tool_call_started" in event_types
    assert "tool_call_completed" in event_types
    assert "scheduler_started" in event_types
    assert "scheduler_step_ready" in event_types
    assert "scheduler_paused" in event_types
    assert "scheduler_completed" in event_types
    scheduler_events = [
        event["event_type"]
        for event in trace["events"]
        if event["event_type"].startswith("scheduler_")
    ]
    assert scheduler_events.count("scheduler_started") == 2
    assert scheduler_events.count("scheduler_step_ready") == 6
    assert scheduler_events.index("scheduler_paused") < scheduler_events.index(
        "scheduler_completed"
    )
    tool_events = [
        event for event in trace["events"]
        if event["event_type"] == "tool_call_completed"
    ]
    assert len(tool_events) == 6
    assert all(event["data"]["tool_version"] == "1.0" for event in tool_events)
    assert all("output" not in event["data"] for event in tool_events)
    assert all(
        event["data"]["success_evaluation"]["passed"] is True
        for event in tool_events
    )

    # weekly_report.md should contain evidence references
    report = (tmp_output / "weekly_report.md").read_text()
    assert "[E-" in report

    # risks.json should be valid
    risks = json.loads((tmp_output / "risks.json").read_text())
    assert risks["schema_version"] == "0.2"
    assert len(risks["risks"]) >= 1

    # action_items.json should be valid
    actions = json.loads((tmp_output / "action_items.json").read_text())
    assert actions["schema_version"] == "0.2"
    assert len(actions["action_items"]) >= 1
    assert "source_refs" in actions["action_items"][0]

    # project_snapshot.json is the structured source of all artifacts
    snapshot = json.loads((tmp_output / "project_snapshot.json").read_text())
    assert len(snapshot["claims"]) > 0
    assert all("claim_id" in claim for claim in snapshot["claims"])

    # Runtime verifies both claims and rendered citations
    verification = json.loads(
        (tmp_output / "verification_report.json").read_text()
    )
    check_ids = {check["check_id"] for check in verification["checks"]}
    assert "claim.supported" in check_ids
    assert "citation.valid" in check_ids

    # Working Memory exports only safe IDs, state and resource summaries.
    context = json.loads((tmp_output / "run_context.json").read_text())
    assert context["run_status"] == "passed"
    assert context["evidence_count"] > 0
    assert len(context["claim_ids"]) > 0
    assert all(step["status"] == "completed" for step in context["steps"])
    assert "weekly_report.md" in context["artifact_names"]
    assert context["plan"]["validated"] is True
    assert all(step["status"] == "completed" for step in context["plan"]["steps"])
    assert all(
        step["success_criteria_passed"] is True
        for step in context["plan"]["steps"]
    )

    plan = json.loads((tmp_output / "plan.json").read_text())
    assert plan["validated"] is True
    assert len(plan["steps"]) == 6
    assert all(step["attempts"] == 1 for step in plan["steps"])
