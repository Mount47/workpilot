"""Deterministic baseline planner for the current trusted workflow."""

from workpilot.contracts import MissionContract
from workpilot.planning.models import Plan, PlanStep


class DeterministicPlanner:
    """Create a reproducible plan without consulting a model."""

    def create_plan(self, contract: MissionContract) -> Plan:
        return Plan(
            plan_id=f"plan_{contract.run_id}",
            goal=contract.goal,
            created_by="deterministic",
            steps=[
                PlanStep(
                    step_id="scan_workspace",
                    objective="Discover authorized project sources.",
                    tool="workspace.scan",
                    expected_output="A bounded list of workspace source paths.",
                    success_criteria=["Workspace scan completes without boundary violation."],
                ),
                PlanStep(
                    step_id="extract_evidence",
                    objective="Extract exact, citable evidence from discovered sources.",
                    tool="evidence.extract",
                    dependencies=["scan_workspace"],
                    expected_output="Validated Evidence records or an explicit empty result.",
                    success_criteria=["Every Evidence quote matches its source locator."],
                ),
                PlanStep(
                    step_id="build_claims",
                    objective="Build the structured project snapshot from Evidence.",
                    tool="claims.build",
                    dependencies=["extract_evidence"],
                    expected_output="A ProjectSnapshot containing structured Claims.",
                    success_criteria=["Every non-unknown Claim references Evidence."],
                    evidence_required=True,
                ),
                PlanStep(
                    step_id="render_artifacts",
                    objective="Render all report artifacts from the ProjectSnapshot.",
                    tool="artifacts.render",
                    dependencies=["build_claims"],
                    expected_output="Markdown and structured project artifacts.",
                    success_criteria=["Artifacts introduce no facts outside the Snapshot."],
                    evidence_required=True,
                ),
                PlanStep(
                    step_id="verify",
                    objective="Verify Claim support and rendered citations.",
                    tool="verification.run",
                    dependencies=["render_artifacts"],
                    expected_output="Structured verification results.",
                    success_criteria=["All error-severity checks pass or trigger revision."],
                    evidence_required=True,
                ),
                PlanStep(
                    step_id="finalize",
                    objective="Persist final artifacts and verification results.",
                    tool="artifacts.finalize",
                    dependencies=["verify"],
                    expected_output="A complete run output directory.",
                    success_criteria=["Required artifacts are written exactly once."],
                ),
            ],
        )
