"""Deterministic coverage checks from accepted Evidence to final Claims."""

from typing import Any

from workpilot.domain import ProjectSnapshot
from workpilot.evidence.store import EvidenceStore
from workpilot.verification.base import Verifier, VerifyResult


class SourceCoverageVerifier(Verifier):
    """Require each source with accepted Evidence to contribute a final Claim."""

    def __init__(self, evidence_store: EvidenceStore) -> None:
        self.evidence_store = evidence_store

    def verify(
        self,
        project_snapshot: ProjectSnapshot,
        **kwargs: Any,
    ) -> list[VerifyResult]:
        evidence_by_id = {
            evidence.evidence_id: evidence
            for evidence in self.evidence_store.list_all()
        }
        expected_sources = sorted(
            {evidence.locator.source_id for evidence in evidence_by_id.values()}
        )
        covered_sources = {
            evidence_by_id[evidence_id].locator.source_id
            for claim in project_snapshot.claims
            for evidence_id in claim.evidence_refs
            if evidence_id in evidence_by_id
        }
        return [
            VerifyResult(
                check_id="source.claim_coverage",
                status="passed" if source_id in covered_sources else "failed",
                severity="info" if source_id in covered_sources else "error",
                artifact="project_snapshot.json",
                location=f"sources.{source_id}",
                message=(
                    f"Accepted Evidence from {source_id} is represented by a Claim."
                    if source_id in covered_sources
                    else (
                        f"Accepted Evidence from {source_id} is omitted from all Claims; "
                        "add a supported Claim or explicitly explain the omission."
                    )
                ),
            )
            for source_id in expected_sources
        ]
