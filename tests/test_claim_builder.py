"""Tests for building project claims from validated evidence."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from workpilot.analysis.claim_builder import (
    ClaimBuilder,
    ClaimDraft,
    ClaimDraftCollection,
)
from workpilot.domain import (
    ClaimCategory,
    ClaimType,
    Evidence,
    SourceLocator,
)
from workpilot.evidence.store import EvidenceStore
from workpilot.providers.base import GenerationResult, StructuredGenerationResult
from workpilot.providers.stub import StubProvider


def _evidence_store() -> EvidenceStore:
    store = EvidenceStore(run_id="run-1")
    store.insert(
        Evidence(
            evidence_id="E-0001",
            locator=SourceLocator.for_file_lines("meeting.md", 3, 3),
            quote="项目计划已经确认。",
            evidence_type="decision",
        )
    )
    return store


def test_stub_builder_creates_exact_supported_claims() -> None:
    snapshot = ClaimBuilder(StubProvider()).build(
        project_id="project-1",
        snapshot_id="snapshot-1",
        goal="生成项目报告",
        evidence_store=_evidence_store(),
    )

    assert len(snapshot.claims) == 1
    claim = snapshot.claims[0]
    assert claim.claim_id == "C-0001"
    assert claim.text == "项目计划已经确认。"
    assert claim.category == ClaimCategory.DECISION
    assert claim.evidence_refs == ["E-0001"]


def test_provider_builder_assigns_ids_and_deduplicates_refs() -> None:
    provider = MagicMock()
    provider.generate_structured.return_value = StructuredGenerationResult(
        value=ClaimDraftCollection(
            claims=[
                ClaimDraft(
                    text="项目计划已经确认。",
                    claim_type=ClaimType.EXPLICIT_FACT,
                    category=ClaimCategory.DECISION,
                    evidence_refs=["E-0001", "E-0001"],
                    confidence=0.9,
                )
            ]
        ),
        generations=(
            GenerationResult(content="{}", provider="test", model="test"),
        ),
    )

    builder = ClaimBuilder(provider)
    snapshot = builder.build(
        project_id="project-1",
        snapshot_id="snapshot-1",
        goal="生成项目报告",
        evidence_store=_evidence_store(),
    )

    assert snapshot.claims[0].claim_id == "C-0001"
    assert snapshot.claims[0].evidence_refs == ["E-0001"]
    assert len(builder.get_model_calls()) == 1
    provider.generate_structured.assert_called_once()


def test_empty_evidence_does_not_call_provider() -> None:
    provider = MagicMock()
    store = EvidenceStore(run_id="run-1")

    snapshot = ClaimBuilder(provider).build(
        project_id="project-1",
        snapshot_id="snapshot-1",
        goal="生成项目报告",
        evidence_store=store,
    )

    assert snapshot.claims == []
    provider.generate_structured.assert_not_called()


def test_claim_draft_rejects_unsupported_non_unknown_claim() -> None:
    with pytest.raises(ValidationError):
        ClaimDraft(
            text="没有证据的结论",
            claim_type=ClaimType.EXPLICIT_FACT,
            category=ClaimCategory.PROGRESS,
        )
