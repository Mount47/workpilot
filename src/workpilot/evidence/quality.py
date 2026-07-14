"""Deterministic quality gates for source coverage and evidence acceptance."""

from pydantic import BaseModel, Field


class SourceExtractionReport(BaseModel):
    """Content-free extraction statistics for one source and one attempt."""

    source_id: str
    status: str = "processed"
    candidate_count: int = Field(default=0, ge=0)
    accepted_count: int = Field(default=0, ge=0)
    discarded_count: int = Field(default=0, ge=0)
    locator_repaired_count: int = Field(default=0, ge=0)
    discard_reason_counts: dict[str, int] = Field(default_factory=dict)
    provider_error_type: str | None = None
    read_error_type: str | None = None


class EvidenceQualityCheck(BaseModel):
    """One deterministic, safe-to-export gate result."""

    check_id: str
    status: str
    severity: str
    source_id: str | None = None
    message: str


class EvidenceQualityReport(BaseModel):
    """Aggregate result used to decide whether synthesis may start."""

    passed: bool
    scanned_source_count: int = Field(ge=0)
    reported_source_count: int = Field(ge=0)
    accepted_evidence_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    discarded_count: int = Field(ge=0)
    locator_repaired_count: int = Field(ge=0)
    discard_reason_counts: dict[str, int] = Field(default_factory=dict)
    repair_source_ids: list[str] = Field(default_factory=list)
    checks: list[EvidenceQualityCheck] = Field(default_factory=list)


class EvidenceQualityError(RuntimeError):
    """Raised when bounded evidence repair cannot satisfy the quality gate."""

    error_type = "evidence_quality_failed"
    retryable = False

    def __init__(self, report: EvidenceQualityReport) -> None:
        self.report = report
        failed = [
            check.check_id
            for check in report.checks
            if check.status == "failed" and check.severity == "error"
        ]
        super().__init__("Evidence quality gate failed: " + ", ".join(failed))


class EvidenceQualityPolicy:
    """Evaluate extraction completeness without semantic LLM judgement."""

    def __init__(self, *, discard_warning_ratio: float = 0.5) -> None:
        if not 0.0 <= discard_warning_ratio <= 1.0:
            raise ValueError("discard_warning_ratio must be between 0 and 1")
        self.discard_warning_ratio = discard_warning_ratio

    def evaluate(
        self,
        *,
        scanned_source_ids: list[str],
        source_reports: list[SourceExtractionReport],
        accepted_evidence_count: int,
    ) -> EvidenceQualityReport:
        """Fail closed on unread sources, provider errors and invalid candidates."""
        latest = {report.source_id: report for report in source_reports}
        checks: list[EvidenceQualityCheck] = []
        repair_sources: set[str] = set()

        for source_id in scanned_source_ids:
            report = latest.get(source_id)
            if report is None:
                repair_sources.add(source_id)
                checks.append(
                    EvidenceQualityCheck(
                        check_id="source.coverage.missing_report",
                        status="failed",
                        severity="error",
                        source_id=source_id,
                        message="Source has no extraction report.",
                    )
                )
                continue
            if report.read_error_type:
                repair_sources.add(source_id)
                checks.append(
                    EvidenceQualityCheck(
                        check_id="source.coverage.read_failed",
                        status="failed",
                        severity="error",
                        source_id=source_id,
                        message=f"Source read failed: {report.read_error_type}.",
                    )
                )
                continue
            if report.provider_error_type:
                repair_sources.add(source_id)
                checks.append(
                    EvidenceQualityCheck(
                        check_id="source.coverage.provider_failed",
                        status="failed",
                        severity="error",
                        source_id=source_id,
                        message=(
                            "Evidence provider returned an explicit failure: "
                            f"{report.provider_error_type}."
                        ),
                    )
                )
                continue
            if report.candidate_count > 0 and report.accepted_count == 0:
                repair_sources.add(source_id)
                checks.append(
                    EvidenceQualityCheck(
                        check_id="evidence.acceptance.all_discarded",
                        status="failed",
                        severity="error",
                        source_id=source_id,
                        message="All model-produced evidence candidates were discarded.",
                    )
                )
                continue
            if report.candidate_count == 0:
                checks.append(
                    EvidenceQualityCheck(
                        check_id="evidence.acceptance.empty_source_result",
                        status="passed",
                        severity="warning",
                        source_id=source_id,
                        message="Source was processed but produced no relevant candidates.",
                    )
                )
            else:
                checks.append(
                    EvidenceQualityCheck(
                        check_id="source.coverage.processed",
                        status="passed",
                        severity="info",
                        source_id=source_id,
                        message="Source produced at least one validated Evidence record.",
                    )
                )

        candidates = sum(report.candidate_count for report in latest.values())
        discarded = sum(report.discarded_count for report in latest.values())
        locator_repaired = sum(
            report.locator_repaired_count for report in latest.values()
        )
        discard_reasons: dict[str, int] = {}
        for report in latest.values():
            for reason, count in report.discard_reason_counts.items():
                discard_reasons[reason] = discard_reasons.get(reason, 0) + count
        if accepted_evidence_count == 0:
            repair_sources.update(
                report.source_id
                for report in latest.values()
                if report.read_error_type is None
            )
            checks.append(
                EvidenceQualityCheck(
                    check_id="evidence.acceptance.non_empty",
                    status="failed",
                    severity="error",
                    message="No validated Evidence record is available for synthesis.",
                )
            )
        else:
            checks.append(
                EvidenceQualityCheck(
                    check_id="evidence.acceptance.non_empty",
                    status="passed",
                    severity="info",
                    message="Validated Evidence is available for synthesis.",
                )
            )

        if candidates and discarded / candidates >= self.discard_warning_ratio:
            checks.append(
                EvidenceQualityCheck(
                    check_id="evidence.acceptance.high_discard_ratio",
                    status="passed",
                    severity="warning",
                    message=(
                        f"Discarded {discarded} of {candidates} evidence candidates; "
                        "review extraction quality."
                    ),
                )
            )

        passed = not any(
            check.status == "failed" and check.severity == "error"
            for check in checks
        )
        return EvidenceQualityReport(
            passed=passed,
            scanned_source_count=len(scanned_source_ids),
            reported_source_count=len(latest),
            accepted_evidence_count=accepted_evidence_count,
            candidate_count=candidates,
            discarded_count=discarded,
            locator_repaired_count=locator_repaired,
            discard_reason_counts=discard_reasons,
            repair_source_ids=sorted(repair_sources),
            checks=checks,
        )
