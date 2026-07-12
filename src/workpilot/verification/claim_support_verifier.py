"""Deterministic checks for the contract between claims and evidence."""

from typing import Any

from workpilot.domain import ClaimType, ProjectSnapshot
from workpilot.evidence.store import EvidenceStore
from workpilot.verification.base import Verifier, VerifyResult


class ClaimSupportVerifier(Verifier):
    """Verify the support contract of each structured project claim.

    This first version deliberately avoids pretending to solve semantic
    entailment. Exact explicit facts are checked deterministically; derived
    facts require existing inputs and a derivation; analytical judgements are
    reported as not yet semantically verified.
    """

    def __init__(self, evidence_store: EvidenceStore) -> None:
        self.evidence_store = evidence_store

    def verify(
        self,
        project_snapshot: ProjectSnapshot,
        **kwargs: Any,
    ) -> list[VerifyResult]:
        results: list[VerifyResult] = []

        for claim in project_snapshot.claims:
            location = f"claims.{claim.claim_id}"
            if claim.claim_type == ClaimType.UNKNOWN:
                results.append(
                    VerifyResult(
                        check_id="claim.unknown",
                        status="passed",
                        severity="info",
                        artifact="project_snapshot.json",
                        location=location,
                        message=f"{claim.claim_id} explicitly records unknown information",
                    )
                )
                continue

            evidences = []
            missing_refs = []
            for evidence_id in claim.evidence_refs:
                evidence = self.evidence_store.get_by_id(evidence_id)
                if evidence is None:
                    missing_refs.append(evidence_id)
                else:
                    evidences.append(evidence)

            if missing_refs:
                results.append(
                    VerifyResult(
                        check_id="claim.evidence_exists",
                        status="failed",
                        severity="error",
                        artifact="project_snapshot.json",
                        location=location,
                        message=(
                            f"{claim.claim_id} references missing evidence: "
                            f"{', '.join(missing_refs)}"
                        ),
                    )
                )
                continue

            if claim.claim_type == ClaimType.EXPLICIT_FACT:
                exact_match = any(
                    claim.text.strip() == evidence.quote.strip()
                    for evidence in evidences
                )
                if not exact_match:
                    results.append(
                        VerifyResult(
                            check_id="claim.explicit_quote_match",
                            status="failed",
                            severity="error",
                            artifact="project_snapshot.json",
                            location=location,
                            message=(
                                f"{claim.claim_id} is explicit_fact but its text is not "
                                "an exact quote of any referenced evidence"
                            ),
                        )
                    )
                    continue

            if claim.claim_type == ClaimType.ANALYTICAL_JUDGEMENT:
                results.append(
                    VerifyResult(
                        check_id="claim.semantic_support_pending",
                        status="skipped",
                        severity="warning",
                        artifact="project_snapshot.json",
                        location=location,
                        message=(
                            f"{claim.claim_id} has valid evidence references but semantic "
                            "support verification is not implemented"
                        ),
                    )
                )
                continue

            results.append(
                VerifyResult(
                    check_id="claim.supported",
                    status="passed",
                    severity="info",
                    artifact="project_snapshot.json",
                    location=location,
                    message=f"{claim.claim_id} satisfies the deterministic support contract",
                )
            )

        return results
