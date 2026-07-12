"""Tests for deterministic claim/evidence support checks."""

from workpilot.domain import (
    Claim,
    ClaimCategory,
    ClaimType,
    Evidence,
    ProjectSnapshot,
    SourceLocator,
)
from workpilot.evidence.store import EvidenceStore
from workpilot.verification.claim_support_verifier import ClaimSupportVerifier


def _store() -> EvidenceStore:
    store = EvidenceStore(run_id="run-1")
    store.insert(
        Evidence(
            evidence_id="E-0001",
            locator=SourceLocator.for_file_lines("meeting.md", 1, 1),
            quote="项目计划已经确认。",
            evidence_type="decision",
        )
    )
    return store


def _snapshot(claim: Claim) -> ProjectSnapshot:
    return ProjectSnapshot(
        project_id="project-1",
        snapshot_id="snapshot-1",
        claims=[claim],
    )


def test_exact_explicit_fact_passes() -> None:
    claim = Claim(
        claim_id="C-0001",
        text="项目计划已经确认。",
        claim_type=ClaimType.EXPLICIT_FACT,
        category=ClaimCategory.DECISION,
        evidence_refs=["E-0001"],
    )

    results = ClaimSupportVerifier(_store()).verify(_snapshot(claim))

    assert results[0].check_id == "claim.supported"
    assert results[0].status == "passed"


def test_missing_evidence_fails() -> None:
    claim = Claim(
        claim_id="C-0001",
        text="项目计划已经确认。",
        claim_type=ClaimType.EXPLICIT_FACT,
        category=ClaimCategory.DECISION,
        evidence_refs=["E-9999"],
    )

    results = ClaimSupportVerifier(_store()).verify(_snapshot(claim))

    assert results[0].check_id == "claim.evidence_exists"
    assert results[0].status == "failed"


def test_paraphrased_explicit_fact_fails() -> None:
    claim = Claim(
        claim_id="C-0001",
        text="计划已确定。",
        claim_type=ClaimType.EXPLICIT_FACT,
        category=ClaimCategory.DECISION,
        evidence_refs=["E-0001"],
    )

    results = ClaimSupportVerifier(_store()).verify(_snapshot(claim))

    assert results[0].check_id == "claim.explicit_quote_match"
    assert results[0].status == "failed"


def test_analytical_judgement_is_explicitly_unverified() -> None:
    claim = Claim(
        claim_id="C-0001",
        text="该事项可能影响交付。",
        claim_type=ClaimType.ANALYTICAL_JUDGEMENT,
        category=ClaimCategory.RISK,
        evidence_refs=["E-0001"],
    )

    results = ClaimSupportVerifier(_store()).verify(_snapshot(claim))

    assert results[0].check_id == "claim.semantic_support_pending"
    assert results[0].status == "skipped"


def test_unknown_claim_passes_without_evidence() -> None:
    claim = Claim(
        claim_id="C-0001",
        text="负责人未知。",
        claim_type=ClaimType.UNKNOWN,
        category=ClaimCategory.ACTION_ITEM,
    )

    results = ClaimSupportVerifier(_store()).verify(_snapshot(claim))

    assert results[0].check_id == "claim.unknown"
    assert results[0].status == "passed"
