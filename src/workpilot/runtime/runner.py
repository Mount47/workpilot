"""Runtime runner — orchestrates the main execution loop."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from workpilot.contracts import MissionContract
from workpilot.evidence.extraction import EvidenceExtractor
from workpilot.evidence.store import EvidenceStore
from workpilot.providers.base import LLMProvider
from workpilot.runtime import Run, RunState
from workpilot.synthesis.synthesizer import Synthesizer
from workpilot.trace.journal import TraceJournal
from workpilot.verification.citation_verifier import CitationVerifier
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

        # 3. Extract evidence via EvidenceExtractor
        extractor = EvidenceExtractor(
            provider=self.provider,
            workspace=self.workspace,
            goal=self.contract.goal,
        )
        evidences = extractor.extract_all(files)
        for ev in evidences:
            self.evidence_store.insert(ev)
        self.trace.append(
            event_type="evidence_extracted",
            data={
                "count": len(evidences),
                "discarded": len(extractor.get_discarded()),
            },
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

        # 5. Verify — Citation Verifier
        self.run.transition(RunState.VERIFYING)
        citation_verifier = CitationVerifier(
            evidence_store=self.evidence_store,
            workspace=self.workspace,
        )
        verify_results = citation_verifier.verify(artifacts=artifacts)

        errors = [r for r in verify_results if r.status == "failed" and r.severity == "error"]
        verification_report = {
            "status": "failed" if errors else "passed",
            "checks": [r.to_dict() for r in verify_results],
            "error_count": len(errors),
            "total_checks": len(verify_results),
        }
        self.trace.append(
            event_type="verification_completed",
            data={"status": verification_report["status"], "error_count": len(errors)},
        )

        # 6. Write artifacts
        for name, content in artifacts.items():
            self.writer.write(name, content)
        self.writer.write_json("verification_report.json", verification_report)

        # 7. Final state based on verification
        if errors:
            self.run.failure_reason = f"Citation verification failed: {len(errors)} error(s)"
            self.run.transition(RunState.FAILED)
            self.trace.append(
                event_type="run_completed",
                data={"status": "failed", "reason": self.run.failure_reason},
            )
        else:
            self.run.transition(RunState.PASSED)
            self.trace.append(
                event_type="run_completed",
                data={"status": "passed"},
            )
