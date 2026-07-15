"""Tests for the independent business-entity projection stage."""

from unittest.mock import MagicMock

import pytest

from workpilot.analysis.entity_builder import (
    EntityBuilder,
    EntityProjectionDraft,
    ProjectedActionDraft,
    ProjectedRiskDraft,
)
from workpilot.domain import (
    Claim,
    ClaimCategory,
    ClaimType,
    Evidence,
    ProjectSnapshot,
    SourceLocator,
)
from workpilot.evidence.store import EvidenceStore
from workpilot.providers.base import GenerationResult, StructuredGenerationResult
from workpilot.providers.stub import StubProvider


def _fixture() -> tuple[ProjectSnapshot, EvidenceStore]:
    store = EvidenceStore(run_id="run-1")
    rows = [
        ("E-0001", "支付 API 未确定，李四需要本周给出方案。", "blocker"),
        ("E-0002", "- 李四：输出支付 API 设计文档。", "action_item"),
        ("E-0003", "- 支付方案截止日期定为本周五。", "decision"),
        ("E-0004", "告警增加 40%，需要排查原因。", "risk"),
    ]
    claims = []
    for index, (evidence_id, quote, category) in enumerate(rows, start=1):
        store.insert(
            Evidence(
                evidence_id=evidence_id,
                locator=SourceLocator.for_file_lines("meeting.md", index, index),
                quote=quote,
                evidence_type=category,
            )
        )
        claims.append(
            Claim(
                claim_id=f"C-{index:04d}",
                text=quote,
                claim_type=ClaimType.EXPLICIT_FACT,
                category=ClaimCategory(category),
                evidence_refs=[evidence_id],
            )
        )
    return (
        ProjectSnapshot(
            project_id="project-1",
            snapshot_id="snapshot-1",
            source_ids=["meeting.md"],
            claims=claims,
        ),
        store,
    )


def test_entity_builder_projects_embedded_and_cross_evidence_fields() -> None:
    snapshot, store = _fixture()
    provider = MagicMock()
    provider.generate_structured.return_value = StructuredGenerationResult(
        value=EntityProjectionDraft(
            action_items=[
                ProjectedActionDraft(
                    claim_id="C-0001",
                    owner="李四",
                    owner_evidence_refs=["E-0001"],
                    due_date_text="本周",
                    due_date_evidence_refs=["E-0001"],
                ),
                ProjectedActionDraft(
                    claim_id="C-0002",
                    owner="李四",
                    owner_evidence_refs=["E-0002"],
                    due_date_text="本周五",
                    due_date_evidence_refs=["E-0003"],
                ),
            ],
            risks=[
                ProjectedRiskDraft(
                    claim_id="C-0004",
                    mitigation="排查原因",
                    mitigation_evidence_refs=["E-0004"],
                )
            ],
        ),
        generations=(GenerationResult(content="{}", provider="test", model="test"),),
    )

    result = EntityBuilder(provider).build(
        goal="生成周报",
        project_snapshot=snapshot,
        evidence_store=store,
    )

    assert len(result.action_items) == 2
    assert result.action_items[0].claim_id == "C-0001"
    assert result.action_items[1].due_date_text.value == "本周五"
    assert result.action_items[1].source_refs == ["E-0002", "E-0003"]
    assert result.risks[0].mitigation.value == "排查原因"
    assert snapshot.action_items == []


def test_entity_builder_rejects_unknown_claim_reference() -> None:
    snapshot, store = _fixture()
    provider = MagicMock()
    provider.generate_structured.return_value = StructuredGenerationResult(
        value=EntityProjectionDraft(
            action_items=[ProjectedActionDraft(claim_id="C-9999")]
        ),
        generations=(),
    )

    with pytest.raises(ValueError, match="unknown claim"):
        EntityBuilder(provider).build(
            goal="生成周报",
            project_snapshot=snapshot,
            evidence_store=store,
        )


def test_stub_entity_builder_preserves_existing_projection() -> None:
    snapshot, store = _fixture()

    result = EntityBuilder(StubProvider()).build(
        goal="生成周报",
        project_snapshot=snapshot,
        evidence_store=store,
    )

    assert result is snapshot
