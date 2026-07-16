"""Runtime runner — orchestrates one bounded and observable agent run."""

import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Iterator

from workpilot.analysis import ClaimBuilder, EntityBuilder
from workpilot.artifacts.writer import ArtifactWriter
from workpilot.contracts import MissionContract
from workpilot.domain import ProjectSnapshot
from workpilot.evidence.extraction import EvidenceExtractor
from workpilot.evidence.quality import EvidenceQualityError, EvidenceQualityPolicy
from workpilot.evidence.store import EvidenceStore
from workpilot.memory import WorkingMemory
from workpilot.planning import (
    ConstrainedLLMPlanner,
    DeterministicPlanner,
    FallbackPlanner,
    Plan,
    PlanExecutor,
    PlanStep,
    PlanValidator,
    RuntimePlanPolicy,
    SerialDAGScheduler,
    StepResultStore,
    CallableToolHandler,
    ToolInput,
    ToolResult,
    create_default_registry,
)
from workpilot.providers.base import (
    GenerationResult,
    LLMProvider,
    ProviderResponseError,
)
from workpilot.providers.stub import StubProvider
from workpilot.providers.routing import (
    ModelRouter,
    RoutingEvent,
    TaskRoutedProvider,
    TaskType,
)
from workpilot.runtime import Run, RunState
from workpilot.runtime.budget import ExecutionBudget
from workpilot.synthesis.synthesizer import Synthesizer
from workpilot.trace.journal import TraceJournal
from workpilot.verification.citation_verifier import CitationVerifier
from workpilot.verification.claim_support_verifier import ClaimSupportVerifier
from workpilot.verification.entity_field_verifier import EntityFieldVerifier
from workpilot.verification.source_coverage_verifier import SourceCoverageVerifier
from workpilot.workspace.tools import WorkspaceTools


class Runtime:
    """Drive a single run while enforcing the Mission Contract."""

    MAX_SYNTHESIS_ATTEMPTS = 3
    MAX_EVIDENCE_EXTRACTION_ATTEMPTS = 2

    def __init__(
        self,
        workspace_root: Path,
        goal: str,
        output_dir: Path,
        provider: LLMProvider,
        model_router: ModelRouter | None = None,
        max_steps: int = 30,
        time_budget_seconds: int = 300,
        token_budget: int = 100_000,
        enable_entity_projection: bool = False,
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
        self._active_step_id: str | None = None
        self.workspace = WorkspaceTools(workspace_root=self.contract.workspace_root)
        self.tool_registry = create_default_registry()
        self.planner = DeterministicPlanner()
        self.plan_validator = PlanValidator(self.tool_registry)
        self.plan: Plan | None = None
        self.writer = ArtifactWriter(output_dir=output_dir)
        self.project_snapshot: ProjectSnapshot | None = None
        self._latest_verification_results: list = []
        self.budget = ExecutionBudget(
            max_steps=self.contract.max_steps,
            token_budget=self.contract.token_budget,
            time_budget_seconds=self.contract.time_budget_seconds,
        )
        self.model_router = model_router
        self.enable_entity_projection = enable_entity_projection
        if model_router is None:
            self.evidence_provider = provider
            analysis_provider = provider
            revision_provider = provider
        else:
            self._attach_model_router(model_router)
            self.evidence_provider = TaskRoutedProvider(
                model_router,
                TaskType.EVIDENCE_EXTRACTION,
            )
            analysis_provider = TaskRoutedProvider(
                model_router,
                TaskType.ANALYSIS,
            )
            revision_provider = TaskRoutedProvider(
                model_router,
                TaskType.REVISION,
            )
            if model_router.has_route(TaskType.PLANNING):
                planning_provider = TaskRoutedProvider(
                    model_router,
                    TaskType.PLANNING,
                )
                self.planner = FallbackPlanner(
                    primary=ConstrainedLLMPlanner(
                        provider=planning_provider,
                        registry=self.tool_registry,
                    ),
                    fallback=DeterministicPlanner(),
                    validator=self.plan_validator,
                    runtime_policy=RuntimePlanPolicy(),
                )
        self.claim_builder = ClaimBuilder(provider=analysis_provider)
        self.revision_claim_builder = ClaimBuilder(provider=revision_provider)
        self.entity_builder = EntityBuilder(provider=analysis_provider)
        self.revision_entity_builder = EntityBuilder(provider=revision_provider)
        self.synthesizer = Synthesizer(provider=analysis_provider)

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
            verification_path = self.output_dir / "verification_report.json"
            if self._latest_verification_results and not verification_path.exists():
                verification_errors = [
                    result
                    for result in self._latest_verification_results
                    if result.status == "failed" and result.severity == "error"
                ]
                self.writer.write_json(
                    "verification_report.json",
                    {
                        "status": "incomplete",
                        "checks": [
                            result.to_dict()
                            for result in self._latest_verification_results
                        ],
                        "error_count": len(verification_errors),
                        "total_checks": len(self._latest_verification_results),
                    },
                )
                self.memory.record_artifacts(["verification_report.json"])
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
            try:
                self.plan = self.planner.create_plan(self.contract)
            except Exception as exc:
                self._record_planner_observability(step_id, error=exc)
                raise
            self._record_planner_observability(step_id)
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
        result_store = StepResultStore()
        execution_context: dict[str, Any] = {
            "attempt": 1,
            "verification_report": None,
        }

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

        self._bind_runtime_handler(
            "workspace.scan",
            scan_workspace,
            summarize=lambda files: {"file_count": len(files)},
        )

        def extract_evidence(_: PlanStep) -> dict[str, int]:
            with self._step("retrieve.evidence") as step_id:
                if self.model_router is None and not isinstance(self.provider, StubProvider):
                    self.budget.check_model_call_allowed()
                files = result_store.get("scan_workspace").output
                quality_policy = EvidenceQualityPolicy()
                source_reports = []
                discarded_count = 0
                provider_error_count = 0
                model_call_count = 0
                repair_count = 0
                extraction_files = files
                quality_report = None
                repair_instruction: str | None = None

                for extraction_attempt in range(
                    1,
                    self.MAX_EVIDENCE_EXTRACTION_ATTEMPTS + 1,
                ):
                    goal = self.contract.goal
                    if repair_instruction is not None:
                        goal += repair_instruction
                    extractor = EvidenceExtractor(
                        provider=self.evidence_provider,
                        workspace=self.workspace,
                        goal=goal,
                        on_model_call=lambda call: self._observe_model_call(
                            call,
                            step_id,
                        ),
                        starting_index=self.evidence_store.count(),
                    )
                    evidences = extractor.extract_all(extraction_files)
                    for evidence in evidences:
                        self.memory.add_evidence(evidence)
                    source_reports.extend(extractor.get_source_reports())
                    discarded_count += len(extractor.get_discarded())
                    provider_error_count += len(extractor.get_provider_errors())
                    model_call_count += len(extractor.get_model_calls())

                    quality_report = quality_policy.evaluate(
                        scanned_source_ids=files,
                        source_reports=source_reports,
                        accepted_evidence_count=self.evidence_store.count(),
                    )
                    self.trace.append(
                        event_type="evidence_quality_evaluated",
                        data={
                            "attempt": extraction_attempt,
                            "passed": quality_report.passed,
                            "scanned_source_count": (
                                quality_report.scanned_source_count
                            ),
                            "reported_source_count": (
                                quality_report.reported_source_count
                            ),
                            "accepted_evidence_count": (
                                quality_report.accepted_evidence_count
                            ),
                            "candidate_count": quality_report.candidate_count,
                            "discarded_count": quality_report.discarded_count,
                            "locator_repaired_count": (
                                quality_report.locator_repaired_count
                            ),
                            "discard_reason_counts": (
                                quality_report.discard_reason_counts
                            ),
                            "repair_source_count": len(
                                quality_report.repair_source_ids
                            ),
                            "failed_check_ids": [
                                check.check_id
                                for check in quality_report.checks
                                if check.status == "failed"
                            ],
                            "warning_check_ids": [
                                check.check_id
                                for check in quality_report.checks
                                if check.severity == "warning"
                            ],
                        },
                        step_id=step_id,
                        parent_step_id="run",
                    )
                    if quality_report.passed:
                        break
                    extraction_files = quality_report.repair_source_ids
                    if not extraction_files:
                        break
                    if extraction_attempt < self.MAX_EVIDENCE_EXTRACTION_ATTEMPTS:
                        repair_instruction = (
                            self._format_evidence_repair_instruction(quality_report)
                        )
                        repair_count += 1
                        self.trace.append(
                            event_type="evidence_repair_requested",
                            data={
                                "attempt": extraction_attempt,
                                "source_count": len(extraction_files),
                                "reason_check_ids": sorted(
                                    {
                                        check.check_id
                                        for check in quality_report.checks
                                        if check.status == "failed"
                                    }
                                ),
                            },
                            step_id=step_id,
                            parent_step_id="run",
                        )

                if quality_report is None or not quality_report.passed:
                    if quality_report is None:
                        raise RuntimeError("Evidence quality report was not produced")
                    raise EvidenceQualityError(quality_report)
                self.trace.append(
                    event_type="evidence_extracted",
                    data={
                        "count": self.evidence_store.count(),
                        "discarded": discarded_count,
                        "model_call_count": model_call_count,
                        "provider_error_count": provider_error_count,
                        "repair_count": repair_count,
                        "locator_repaired_count": (
                            quality_report.locator_repaired_count
                        ),
                        "quality_gate": "passed",
                    },
                    step_id=step_id,
                    parent_step_id="run",
                )
                return {
                    "evidence_count": self.evidence_store.count(),
                    "discarded_count": discarded_count,
                    "provider_error_count": provider_error_count,
                    "repair_count": repair_count,
                    "locator_repaired_count": quality_report.locator_repaired_count,
                    "source_count": quality_report.scanned_source_count,
                }

        self._bind_runtime_handler(
            "evidence.extract",
            extract_evidence,
            summarize=lambda output: dict(output),
        )
        citation_verifier = CitationVerifier(
            evidence_store=self.evidence_store,
            workspace=self.workspace,
        )
        claim_support_verifier = ClaimSupportVerifier(
            evidence_store=self.evidence_store,
        )
        source_coverage_verifier = SourceCoverageVerifier(
            evidence_store=self.evidence_store,
        )
        entity_field_verifier = EntityFieldVerifier(
            evidence_store=self.evidence_store,
        )

        def build_claims(_: PlanStep) -> ProjectSnapshot:
            attempt = execution_context["attempt"]
            claim_state = (
                RunState.SYNTHESIZING if attempt == 1 else RunState.REVISING
            )
            step_name = "synthesize.claims" if attempt == 1 else "revise.claims"
            with self._step(step_name, state=claim_state) as step_id:
                if self.model_router is None and not isinstance(
                    self.provider,
                    StubProvider,
                ):
                    self.budget.check_model_call_allowed()
                builder = (
                    self.claim_builder
                    if attempt == 1
                    else self.revision_claim_builder
                )
                try:
                    self.project_snapshot = builder.build(
                        project_id=self.contract.workspace_root.name,
                        snapshot_id=f"{self.run_id}-attempt-{attempt}",
                        goal=self.contract.goal,
                        evidence_store=self.evidence_store,
                        feedback=self.memory.revision_feedback,
                    )
                    entity_builder = (
                        self.entity_builder
                        if attempt == 1
                        else self.revision_entity_builder
                    )
                    if self.enable_entity_projection:
                        self.project_snapshot = entity_builder.build(
                            goal=self.contract.goal,
                            project_snapshot=self.project_snapshot,
                            evidence_store=self.evidence_store,
                        )
                except ProviderResponseError as exc:
                    for generation in exc.generations:
                        self._observe_model_call(generation, step_id)
                    raise
                for generation in builder.get_model_calls():
                    self._observe_model_call(generation, step_id)
                for generation in entity_builder.get_model_calls():
                    self._observe_model_call(generation, step_id)
                self.memory.set_project_snapshot(self.project_snapshot)
                self.trace.append(
                    event_type="claims_built",
                    data={
                        "attempt": attempt,
                        "claim_count": len(self.project_snapshot.claims),
                        "claim_text_repair_count": (
                            builder.get_claim_text_repair_count()
                        ),
                        "action_item_count": len(
                            self.project_snapshot.action_items
                        ),
                        "risk_count": len(self.project_snapshot.risks),
                        "entity_projection_enabled": self.enable_entity_projection,
                        "entity_projection_model_call_count": (
                            len(entity_builder.get_model_calls())
                            if self.enable_entity_projection
                            else 0
                        ),
                        "entity_field_downgrade_count": (
                            entity_builder.get_field_downgrade_count()
                            if self.enable_entity_projection
                            else 0
                        ),
                        "entity_projection_decisions": (
                            entity_builder.get_decision_summary()
                            if self.enable_entity_projection
                            else {}
                        ),
                    },
                    step_id=step_id,
                    parent_step_id="run",
                )
                return self.project_snapshot

        self._bind_runtime_handler(
            "claims.build",
            build_claims,
            summarize=lambda snapshot: {
                "claim_count": len(snapshot.claims),
                "source_count": len(snapshot.source_ids),
            },
        )

        def render_artifacts(_: PlanStep) -> dict:
            attempt = execution_context["attempt"]
            with self._step("synthesize.artifacts") as step_id:
                project_snapshot = result_store.get("build_claims").output
                rendered = self.synthesizer.generate(
                    contract=self.contract,
                    project_snapshot=project_snapshot,
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

        self._bind_runtime_handler(
            "artifacts.render",
            render_artifacts,
            summarize=lambda rendered: {
                "artifact_names": sorted(rendered),
                "artifact_count": len(rendered),
            },
        )

        def verify(_: PlanStep) -> tuple[list, list]:
            attempt = execution_context["attempt"]
            with self._step("verify", state=RunState.VERIFYING) as step_id:
                project_snapshot = result_store.get("build_claims").output
                artifacts = result_store.get("render_artifacts").output
                claim_results = claim_support_verifier.verify(
                    project_snapshot=project_snapshot,
                )
                source_coverage_results = source_coverage_verifier.verify(
                    project_snapshot=project_snapshot,
                )
                entity_field_results = entity_field_verifier.verify(
                    project_snapshot=project_snapshot,
                )
                citation_results = citation_verifier.verify(artifacts=artifacts)
                results = (
                    claim_results
                    + source_coverage_results
                    + entity_field_results
                    + citation_results
                )
                self._latest_verification_results = results
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
                        "source_coverage_check_count": len(
                            source_coverage_results
                        ),
                        "entity_field_check_count": len(entity_field_results),
                        "citation_check_count": len(citation_results),
                    },
                    step_id=step_id,
                    parent_step_id="run",
                )
                return results, current_errors

        self._bind_runtime_handler(
            "verification.run",
            verify,
            summarize=lambda output: {
                "check_count": len(output[0]),
                "error_count": len(output[1]),
            },
        )

        def finalize(_: PlanStep) -> None:
            with self._step("finalize"):
                artifacts = result_store.get("render_artifacts").output
                for name, content in artifacts.items():
                    self.writer.write(name, content)
                self.writer.write_json(
                    "verification_report.json",
                    execution_context["verification_report"],
                )
                self.memory.record_artifacts(
                    list(artifacts.keys()) + ["verification_report.json"]
                )

        self._bind_runtime_handler(
            "artifacts.finalize",
            finalize,
            summarize=lambda _: {"status": "persisted"},
        )

        scheduler = SerialDAGScheduler(
            plan_executor,
            result_store=result_store,
            execute_step=lambda step_id: self._execute_plan_step_result(
                plan_executor,
                step_id,
            ),
            on_event=self._record_scheduler_event,
        )
        initial_result = scheduler.run(stop_before_step_ids={"finalize"})
        if initial_result.failed_step_ids or initial_result.blocked_step_ids:
            scheduler.raise_first_failure()
            raise RuntimeError(
                "Plan execution failed before finalization: "
                f"failed={initial_result.failed_step_ids}, "
                f"blocked={initial_result.blocked_step_ids}"
            )
        if not initial_result.paused:
            raise RuntimeError("Scheduler did not pause before finalization")

        verify_results, errors = result_store.get("verify").output
        while errors and execution_context["attempt"] < self.MAX_SYNTHESIS_ATTEMPTS:
            attempt = execution_context["attempt"]
            feedback = self._format_verification_feedback(errors)
            self.memory.request_revision(feedback)
            self.trace.append(
                event_type="revision_requested",
                data={"attempt": attempt, "error_count": len(errors)},
                parent_step_id="run",
            )
            execution_context["attempt"] = attempt + 1
            for step_id in ("build_claims", "render_artifacts", "verify"):
                result = self._execute_plan_step_result(
                    plan_executor,
                    step_id,
                    allow_reentry=True,
                )
                result_store.put(step_id, result, allow_overwrite=True)
            verify_results, errors = result_store.get("verify").output

        execution_context["verification_report"] = {
            "status": "failed" if errors else "passed",
            "checks": [result.to_dict() for result in verify_results],
            "error_count": len(errors),
            "total_checks": len(verify_results),
        }
        final_result = scheduler.run()
        if not final_result.succeeded:
            raise RuntimeError(
                "Plan execution did not reach a successful terminal state: "
                f"failed={final_result.failed_step_ids}, "
                f"blocked={final_result.blocked_step_ids}, "
                f"pending={final_result.pending_step_ids}"
            )
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
        *,
        allow_reentry: bool = False,
    ) -> Any:
        return self._execute_plan_step_result(
            executor,
            step_id,
            allow_reentry=allow_reentry,
        ).output

    def _execute_plan_step_result(
        self,
        executor: PlanExecutor,
        step_id: str,
        *,
        allow_reentry: bool = False,
    ) -> ToolResult:
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
        spec = self.tool_registry.get(step.tool)
        tool_started = monotonic()
        self.trace.append(
            event_type="tool_call_started",
            data={
                "plan_step_id": step_id,
                "tool": step.tool,
                "tool_version": spec.version if spec is not None else None,
                "input_fields": sorted(step.inputs),
                "attempt": next_attempt,
            },
            parent_step_id="run",
        )
        try:
            result = executor.execute_registered_step(
                step_id,
                allow_reentry=allow_reentry,
            )
        except Exception as exc:
            error_type = self._error_type(exc)
            success_evaluation = (
                step.success_evaluation.model_dump(mode="json")
                if step.success_evaluation is not None
                else None
            )
            self.trace.append(
                event_type="tool_call_failed",
                data={
                    "plan_step_id": step_id,
                    "tool": step.tool,
                    "tool_version": spec.version if spec is not None else None,
                    "attempt": step.attempts,
                    "latency_ms": round((monotonic() - tool_started) * 1000, 3),
                    "error_type": error_type,
                    "success_evaluation": success_evaluation,
                },
                parent_step_id="run",
            )
            self.trace.append(
                event_type="plan_step_failed",
                data={
                    "plan_id": executor.plan.plan_id,
                    "plan_step_id": step_id,
                    "tool": step.tool,
                    "attempt": step.attempts,
                    "error_type": error_type,
                    "error": str(exc),
                    "success_evaluation": success_evaluation,
                },
                parent_step_id="run",
            )
            raise
        self.trace.append(
            event_type="tool_call_completed",
            data={
                "plan_step_id": step_id,
                "tool": step.tool,
                "tool_version": spec.version if spec is not None else None,
                "attempt": step.attempts,
                "latency_ms": round((monotonic() - tool_started) * 1000, 3),
                "output_summary": result.output_summary,
                "evidence_ids": result.evidence_ids,
                "success_evaluation": (
                    step.success_evaluation.model_dump(mode="json")
                    if step.success_evaluation is not None
                    else None
                ),
            },
            parent_step_id="run",
        )
        self.trace.append(
            event_type="plan_step_completed",
            data={
                "plan_id": executor.plan.plan_id,
                "plan_step_id": step_id,
                "tool": step.tool,
                "attempt": step.attempts,
                "success_evaluation": (
                    step.success_evaluation.model_dump(mode="json")
                    if step.success_evaluation is not None
                    else None
                ),
            },
            parent_step_id="run",
        )
        return result

    def _record_scheduler_event(
        self,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        self.trace.append(
            event_type=event_type,
            data=data,
            parent_step_id="run",
        )

    def _bind_runtime_handler(
        self,
        tool_name: str,
        callback: Callable[[PlanStep], Any],
        *,
        summarize: Callable[[Any], dict[str, Any]],
    ) -> None:
        spec = self.tool_registry.get(tool_name)
        if spec is None:
            raise ValueError(f"Cannot bind unregistered tool {tool_name}")
        self.tool_registry.bind(
            CallableToolHandler(
                spec=spec,
                input_model=ToolInput,
                callback=lambda _input, step: callback(step),
                summarize=summarize,
            )
        )

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
        previous_active_step = self._active_step_id
        self._active_step_id = step_id
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
        finally:
            self._active_step_id = previous_active_step

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
                "task_type": generation.task_type,
                "prompt_version": generation.prompt_version,
                "schema_name": generation.schema_name,
                "schema_version": generation.schema_version,
                "route_target_index": generation.route_target_index,
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

    def _attach_model_router(self, router: ModelRouter) -> None:
        previous_before_attempt = router.before_attempt
        previous_on_event = router.on_event

        def before_attempt(attempt: int) -> None:
            if previous_before_attempt is not None:
                previous_before_attempt(attempt)
            self.budget.check_model_call_allowed()

        def on_event(event: RoutingEvent) -> None:
            if previous_on_event is not None:
                previous_on_event(event)
            event_data = asdict(event)
            event_type = event_data.pop("event_type")
            self.trace.append(
                event_type=event_type,
                data=event_data,
                step_id=self._active_step_id,
                parent_step_id="run",
            )

        router.before_attempt = before_attempt
        router.on_event = on_event

    def _record_planner_observability(
        self,
        step_id: str,
        *,
        error: Exception | None = None,
    ) -> None:
        get_calls = getattr(self.planner, "get_model_calls", None)
        if callable(get_calls):
            for generation in get_calls():
                self._observe_model_call(generation, step_id)

        decision = getattr(self.planner, "last_decision", None)
        if decision is None:
            data: dict[str, Any] = {
                "requested": "deterministic",
                "selected": "deterministic",
                "fallback_reason": None,
            }
        else:
            data = decision.to_dict()
        if error is not None and data["selected"] == "pending":
            data["selected"] = "failed"
            data["fallback_reason"] = self._error_type(error)
        data["plan_id"] = self.plan.plan_id if self.plan is not None else None
        self.trace.append(
            event_type="planner_decision",
            data=data,
            step_id=step_id,
            parent_step_id="run",
        )

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

    @staticmethod
    def _format_evidence_repair_instruction(report) -> str:
        """Create content-free corrective feedback from deterministic failures."""
        reason_counts = report.discard_reason_counts
        rendered_reasons = (
            ", ".join(
                f"{reason}={count}"
                for reason, count in sorted(reason_counts.items())
            )
            if reason_counts
            else "no candidate-level reason was recorded"
        )
        return (
            "\n\nEvidence repair instruction (system-generated):\n"
            "The previous extraction failed deterministic validation. "
            f"Failure counts: {rendered_reasons}.\n"
            "Return exact verbatim source text, including Markdown list or numeric "
            "markers. Use the `N |` prefixes only to calculate start_line and "
            "end_line; never include `N |` in quote. Do not paraphrase, merge "
            "separate lines, or remove punctuation."
        )
