"""Runtime runner — orchestrates the main execution loop."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from workpilot.contracts import MissionContract
from workpilot.evidence.store import EvidenceStore
from workpilot.providers.base import LLMProvider
from workpilot.runtime import Run, RunState
from workpilot.synthesis.synthesizer import Synthesizer
from workpilot.trace.journal import TraceJournal
from workpilot.workspace.tools import WorkspaceTools
from workpilot.artifacts.writer import ArtifactWriter


class Runtime:
    """Main runtime that drives a single run through the state machine."""

    def __init__(
        self,
        workspace_root: Path,
        goal: str,
        output_dir: Path,
        provider: LLMProvider,
        max_steps: int = 30,
        time_budget_seconds: int = 300,
    ) -> None:
        self.run_id = f"run_{uuid.uuid4().hex[:8]}"
        self.run = Run(
            run_id=self.run_id,
            goal=goal,
            workspace_root=str(workspace_root),
            output_dir=str(output_dir),
        )
        self.contract = MissionContract(
            run_id=self.run_id,
            goal=goal,
            workspace_root=workspace_root,
            max_steps=max_steps,
            time_budget_seconds=time_budget_seconds,
        )
        self.provider = provider
        self.output_dir = output_dir
        self.evidence_store = EvidenceStore(run_id=self.run_id)
        self.trace = TraceJournal(run_id=self.run_id)
        self.workspace = WorkspaceTools(
            workspace_root=self.contract.workspace_root
        )
        self.synthesizer = Synthesizer(provider=provider)
        self.writer = ArtifactWriter(output_dir=output_dir)

    def execute(self) -> Run:
        """Run the full pipeline. Returns the Run with final state."""
        try:
            self._run_pipeline()
        except Exception as e:
            self.run.failure_reason = str(e)
            self.run.transition(RunState.FAILED)
            self.trace.append(
                event_type="run_failed",
                data={"error": str(e)},
            )
        finally:
            # Always write trace
            self.writer.write_json("trace.json", self.trace.export())

        return self.run

    def _run_pipeline(self) -> None:
        """Execute the main pipeline steps."""

        # 1. Start
        self.run.transition(RunState.PLANNING)
        self.trace.append(
            event_type="run_started",
            data={"goal": self.contract.goal, "run_id": self.run_id},
        )

        # 2. Retrieve — scan workspace
        self.run.transition(RunState.RETRIEVING)
        files = self.workspace.list_files()
        self.trace.append(
            event_type="workspace_scanned",
            data={"file_count": len(files), "files": files},
        )

        # 3. Extract evidence (Phase 1: provider generates stub evidence)
        evidences = self.provider.extract_evidence(
            files=files,
            workspace=self.workspace,
        )
        for ev in evidences:
            self.evidence_store.insert(ev)
        self.trace.append(
            event_type="evidence_extracted",
            data={"count": len(evidences)},
        )

        # 4. Synthesize
        self.run.transition(RunState.SYNTHESIZING)
        artifacts = self.synthesizer.generate(
            contract=self.contract,
            evidence_store=self.evidence_store,
        )
        self.trace.append(
            event_type="artifacts_generated",
            data={"artifacts": list(artifacts.keys())},
        )

        # 5. Verify (Phase 1: skip, mark passed directly)
        self.run.transition(RunState.VERIFYING)
        verification_report = {
            "status": "passed",
            "checks": [],
            "note": "Phase 1: verification skipped",
        }
        self.trace.append(
            event_type="verification_completed",
            data={"status": "passed"},
        )

        # 6. Write artifacts
        for name, content in artifacts.items():
            self.writer.write(name, content)
        self.writer.write_json("verification_report.json", verification_report)

        # 7. Done
        self.run.transition(RunState.PASSED)
        self.trace.append(
            event_type="run_completed",
            data={"status": "passed"},
        )
