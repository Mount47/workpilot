"""Runtime runner — orchestrates one bounded and observable agent run."""

import uuid
from contextlib import contextmanager
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Iterator

from workpilot.analysis import ClaimBuilder
from workpilot.artifacts.writer import ArtifactWriter
from workpilot.contracts import MissionContract
from workpilot.domain import ProjectSnapshot
from workpilot.evidence.extraction import EvidenceExtractor
from workpilot.evidence.store import EvidenceStore
from workpilot.memory import WorkingMemory
from workpilot.planning import (
    DeterministicPlanner,
    Plan,
    PlanExecutor,
    PlanStep,
    PlanValidator,
    create_default_registry,
)
from workpilot.providers.base import (
    GenerationResult,
    LLMProvider,
    ProviderResponseError,
)
from workpilot.providers.stub import StubProvider
from workpilot.runtime import Run, RunState
from workpilot.runtime.budget import ExecutionBudget
from workpilot.synthesis.synthesizer import Synthesizer
from workpilot.trace.journal import TraceJournal
from workpilot.verification.citation_verifier import CitationVerifier
from workpilot.verification.claim_support_verifier import ClaimSupportVerifier
from workpilot.workspace.tools import WorkspaceTools


class Runtime:
    """Drive a single run while enforcing the Mission Contract."""

    MAX_SYNTHESIS_ATTEMPTS = 3

    def __init__(
        self,
        workspace_root: Path,
        goal: str,
        output_dir: Path,
        provider: LLMProvider,
        max_steps: int = 30,
        time_budget_seconds: int = 300,
        token_budget: int = 100_000,
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
            token_budget=token_budget,
        )
        self.provider = provider
        self.output_dir = output_dir
        evidence_store = EvidenceStore(run_id=self.run_id)
        self.memory = WorkingMemory(
            contract=self.contract,
            evidence_store=evidence_store,
        )
        # Compatibility alias while modules migrate to RunContext reads.
        self.evidence_store = self.memory.evidence_store
        self.trace = TraceJournal(run_id=self.run_id)
        self.workspace = WorkspaceTools(workspace_root=self.contract.workspace_root)
        self.claim_builder = ClaimBuilder(provider=provider)
        self.synthesizer = Synthesizer(provider=provider)
        self.tool_registry = create_default_registry()
        self.planner = DeterministicPlanner()
        self.plan_validator = PlanValidator(self.tool_registry)
        self.plan: Plan | None = None
        self.writer = ArtifactWriter(output_dir=output_dir)
        self.project_snapshot: ProjectSnapshot | None = None
        self.budget = ExecutionBudget(
            max_steps=self.contract.max_steps,
            token_budget=self.contract.token_budget,
            time_budget_seconds=self.contract.time_budget_seconds,
        )

    def execute(self) -> Run:
        """Run the full pipeline and always persist its observable trace."""
        self.budget.start()
        self.memory.set_run_status("running")
        self.memory.update_budget(self.budget.snapshot())
        self.trace.append(
            event_type="run_started",
            data={"goal": self.contract.goal, "run_id": self.run_id},
            parent_step_id="run",
        )
        try:
            self._run_pipeline()
        except Exception as exc:
            error_type = self._error_type(exc)
            self.run.failure_reason = str(exc)
            terminal = {RunState.PASSED, RunState.FAILED, RunState.CANCELLED}
            if self.run.state not in terminal:
                self.run.transition(RunState.FAILED)
            self.memory.set_run_status("failed", error_type)
            self.trace.append(
                event_type="run_failed",
                data={
                    "error": str(exc),
                    "error_type": error_type,
                },
                parent_step_id="run",
            )
        finally:
            self.memory.update_budget(self.budget.snapshot())
            self.memory.record_artifacts(["run_context.json", "trace.json"])
            self.trace.append(
                event_type="budget_summary",
                data=self.budget.snapshot(),
                parent_step_id="run",
            )
            self.writer.write_json(
                "run_context.json",
                self.memory.export().model_dump(mode="json"),
            )
            self.writer.write_json("trace.json", self.trace.export())
        return self.run

    def _run_pipeline(self) -> None:
        with self._step("planning", state=RunState.PLANNING) as step_id:
            self.plan = self.planner.create_plan(self.contract)
            self.memory.set_plan(self.plan)
            self.trace.append(
                event_type="plan_created",
                data={
                    "plan_id": self.plan.plan_id,
                    "created_by": self.plan.created_by,
                    "step_count": len(self.plan.steps),
                },
                step_id=step_id,
                parent_step_id="run",
            )
            self.plan_validator.validate(
                self.plan,
                self.contract,
                reserved_runtime_steps=1,
            )
            self.trace.append(
                event_type="plan_validated",
                data={"plan_id": self.plan.plan_id, "status": "passed"},
                step_id=step_id,
                parent_step_id="run",
            )

        plan_executor = PlanExecutor(self.plan, self.tool_registry)

        def scan_workspace(_: PlanStep) -> list[str]:
            with self._step(
                "retrieve.workspace",
                state=RunState.RETRIEVING,
            ) as step_id:
                files = self.workspace.list_files()
                self.trace.append(
                    event_type="workspace_scanned",
                    data={"file_count": len(files), "files": files},
                    step_id=step_id,
                    parent_step_id="run",
                )
                return files

        files = self._execute_plan_step(
            plan_executor,
            "scan_workspace",
            scan_workspace,
        )

        def extract_evidence(_: PlanStep) -> None:
            with self._step("retrieve.evidence") as step_id:
                if not isinstance(self.provider, StubProvider):
                    self.budget.check_model_call_allowed()
                extractor = EvidenceExtractor(
                    provider=self.provider,
                    workspace=self.workspace,
                    goal=self.contract.goal,
                    on_model_call=lambda call: self._observe_model_call(call, step_id),
                )
                evidences = extractor.extract_all(files)
                for evidence in evidences:
                    self.memory.add_evidence(evidence)
                self.trace.append(
                    event_type="evidence_extracted",
                    data={
                        "count": len(evidences),
                        "discarded": len(extractor.get_discarded()),
                        "model_call_count": len(extractor.get_model_calls()),
                        "provider_errors": extractor.get_provider_errors(),
                    },
                    step_id=step_id,
                    parent_step_id="run",
                )

        self._execute_plan_step(
            plan_executor,
            "extract_evidence",
            extract_evidence,
        )

        citation_verifier = CitationVerifier(
            evidence_store=self.evidence_store,
            workspace=self.workspace,
        )
        claim_support_verifier = ClaimSupportVerifier(
            evidence_store=self.evidence_store,
        )
        artifacts: dict = {}
        verify_results = []
        errors = []

        for attempt in range(1, self.MAX_SYNTHESIS_ATTEMPTS + 1):
            allow_reentry = attempt > 1

            def build_claims(_: PlanStep) -> None:
                claim_state = (
                    RunState.SYNTHESIZING if attempt == 1 else RunState.REVISING
                )
                step_name = (
                    "synthesize.claims" if attempt == 1 else "revise.claims"
                )
                with self._step(step_name, state=claim_state) as step_id:
                    if not isinstance(self.provider, StubProvider):
                        self.budget.check_model_call_allowed()
                    try:
                        self.project_snapshot = self.claim_builder.build(
                            project_id=self.contract.workspace_root.name,
                            snapshot_id=f"{self.run_id}-attempt-{attempt}",
                            goal=self.contract.goal,
                            evidence_store=self.evidence_store,
                            feedback=self.memory.revision_feedback,
                        )
                    except ProviderResponseError as exc:
                        for generation in exc.generations:
                            self._observe_model_call(generation, step_id)
                        raise
                    for generation in self.claim_builder.get_model_calls():
                        self._observe_model_call(generation, step_id)
                    self.memory.set_project_snapshot(self.project_snapshot)
                    self.trace.append(
                        event_type="claims_built",
                        data={
                            "attempt": attempt,
                            "claim_count": len(self.project_snapshot.claims),
                        },
                        step_id=step_id,
                        parent_step_id="run",
                    )

            self._execute_plan_step(
                plan_executor,
                "build_claims",
                build_claims,
                allow_reentry=allow_reentry,
            )

            def render_artifacts(_: PlanStep) -> dict:
                with self._step("synthesize.artifacts") as step_id:
                    rendered = self.synthesizer.generate(
                        contract=self.contract,
                        project_snapshot=self.project_snapshot,
                    )
                    self.memory.record_artifacts(list(rendered.keys()))
                    self.trace.append(
                        event_type="artifacts_generated",
                        data={
                            "attempt": attempt,
                            "artifacts": list(rendered.keys()),
                        },
                        step_id=step_id,
                        parent_step_id="run",
                    )
                    return rendered

            artifacts = self._execute_plan_step(
                plan_executor,
                "render_artifacts",
                render_artifacts,
                allow_reentry=allow_reentry,
            )

            def verify(_: PlanStep) -> tuple[list, list]:
                with self._step("verify", state=RunState.VERIFYING) as step_id:
                    claim_results = claim_support_verifier.verify(
                        project_snapshot=self.project_snapshot,
                    )
                    citation_results = citation_verifier.verify(artifacts=artifacts)
                    results = claim_results + citation_results
                    current_errors = [
                        result
                        for result in results
                        if result.status == "failed" and result.severity == "error"
                    ]
                    self.memory.record_verification(results)
                    self.trace.append(
                        event_type="verification_completed",
                        data={
                            "attempt": attempt,
                            "status": "failed" if current_errors else "passed",
                            "error_count": len(current_errors),
                            "claim_check_count": len(claim_results),
                            "citation_check_count": len(citation_results),
                        },
                        step_id=step_id,
                        parent_step_id="run",
                    )
                    return results, current_errors

            verify_results, errors = self._execute_plan_step(
                plan_executor,
                "verify",
                verify,
                allow_reentry=allow_reentry,
            )

            if not errors:
                break
            if attempt < self.MAX_SYNTHESIS_ATTEMPTS:
                feedback = self._format_verification_feedback(errors)
                self.memory.request_revision(feedback)
                self.trace.append(
                    event_type="revision_requested",
                    data={"attempt": attempt, "error_count": len(errors)},
                    parent_step_id="run",
                )

        verification_report = {
            "status": "failed" if errors else "passed",
            "checks": [result.to_dict() for result in verify_results],
            "error_count": len(errors),
            "total_checks": len(verify_results),
        }

        def finalize(_: PlanStep) -> None:
            with self._step("finalize"):
                for name, content in artifacts.items():
                    self.writer.write(name, content)
                self.writer.write_json("verification_report.json", verification_report)
                self.memory.record_artifacts(
                    list(artifacts.keys()) + ["verification_report.json"]
                )

        self._execute_plan_step(plan_executor, "finalize", finalize)
        self.writer.write_json("plan.json", self.plan.model_dump(mode="json"))
        self.memory.record_artifacts(["plan.json"])

        if errors:
            self.run.failure_reason = f"Verification failed: {len(errors)} error(s)"
            self.run.transition(RunState.FAILED)
            self.memory.set_run_status("failed", "VerificationError")
            status_data = {"status": "failed", "reason": self.run.failure_reason}
        else:
            self.run.transition(RunState.PASSED)
            self.memory.set_run_status("passed")
            status_data = {"status": "passed"}
        self.trace.append(
            event_type="run_completed",
            data=status_data,
            parent_step_id="run",
        )

    def _execute_plan_step(
        self,
        executor: PlanExecutor,
        step_id: str,
        handler: Callable[[PlanStep], Any],
        *,
        allow_reentry: bool = False,
    ) -> Any:
        step = executor.plan.get_step(step_id)
        next_attempt = step.attempts + 1
        self.trace.append(
            event_type="plan_step_started",
            data={
                "plan_id": executor.plan.plan_id,
                "plan_step_id": step_id,
                "tool": step.tool,
                "attempt": next_attempt,
            },
            parent_step_id="run",
        )
        try:
            result = executor.execute_step(
                step_id,
                handler,
                allow_reentry=allow_reentry,
            )
        except Exception as exc:
            error_type = self._error_type(exc)
            self.trace.append(
                event_type="plan_step_failed",
                data={
                    "plan_id": executor.plan.plan_id,
                    "plan_step_id": step_id,
                    "tool": step.tool,
                    "attempt": step.attempts,
                    "error_type": error_type,
                    "error": str(exc),
                },
                parent_step_id="run",
            )
            raise
        self.trace.append(
            event_type="plan_step_completed",
            data={
                "plan_id": executor.plan.plan_id,
                "plan_step_id": step_id,
                "tool": step.tool,
                "attempt": step.attempts,
            },
            parent_step_id="run",
        )
        return result

    @contextmanager
    def _step(
        self,
        name: str,
        *,
        state: RunState | None = None,
    ) -> Iterator[str]:
        sequence = self.budget.consume_step(name)
        step_id = f"step_{sequence:04d}"
        if state is not None:
            self.run.transition(state)
        self.memory.start_step(step_id, name, sequence)
        self.memory.update_budget(self.budget.snapshot())
        started = monotonic()
        self.trace.append(
            event_type="step_started",
            data={"step_name": name, "step_sequence": sequence},
            step_id=step_id,
            parent_step_id="run",
        )
        try:
            yield step_id
            self.budget.check_time()
        except Exception as exc:
            error_type = self._error_type(exc)
            self.memory.fail_step(step_id, error_type)
            self.memory.update_budget(self.budget.snapshot())
            self.trace.append(
                event_type="step_failed",
                data={
                    "step_name": name,
                    "latency_ms": round((monotonic() - started) * 1000, 3),
                    "error": str(exc),
                    "error_type": error_type,
                },
                step_id=step_id,
                parent_step_id="run",
            )
            raise
        else:
            self.memory.complete_step(step_id)
            self.memory.update_budget(self.budget.snapshot())
            self.trace.append(
                event_type="step_completed",
                data={
                    "step_name": name,
                    "latency_ms": round((monotonic() - started) * 1000, 3),
                },
                step_id=step_id,
                parent_step_id="run",
            )

    def _observe_model_call(self, generation: GenerationResult, step_id: str) -> None:
        self.trace.append(
            event_type="model_call_completed",
            data={
                "provider": generation.provider,
                "model": generation.model,
                "input_tokens": generation.input_tokens,
                "output_tokens": generation.output_tokens,
                "total_tokens": generation.total_tokens,
                "latency_ms": round(generation.latency_ms, 3),
                "request_id": generation.request_id,
                "finish_reason": generation.finish_reason,
                "estimated_cost": generation.estimated_cost,
            },
            step_id=step_id,
            parent_step_id="run",
        )
        try:
            self.budget.consume_tokens(
                generation.input_tokens,
                generation.output_tokens,
            )
        finally:
            self.memory.update_budget(self.budget.snapshot())

    @staticmethod
    def _error_type(exc: Exception) -> str:
        declared_type = getattr(exc, "error_type", None)
        if declared_type is None:
            return type(exc).__name__
        return str(getattr(declared_type, "value", declared_type))

    @staticmethod
    def _format_verification_feedback(errors: list) -> str:
        lines = [
            "The previous output failed verification. Fix ONLY the following "
            "problems. Do not invent new citations; every [E-XXXX] reference "
            "must exist in the provided evidence.",
        ]
        for result in errors:
            location = f" ({result.location})" if result.location else ""
            lines.append(f"- {result.message}{location}")
        return "\n".join(lines)
