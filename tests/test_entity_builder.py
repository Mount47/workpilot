"""Tests for the independent business-entity projection stage."""

from unittest.mock import MagicMock

import pytest

from workpilot.analysis.entity_builder import (
    EntityBuilder,
    EntityProjectionDraft,
    DuplicateCandidateDraft,
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
                    claim_id="C-0001",
                    owner="李四",
                    owner_evidence_refs=["E-0001"],
                ),
                ProjectedRiskDraft(
                    claim_id="C-0004",
                    mitigation="排查原因",
                    mitigation_evidence_refs=["E-0004"],
                )
            ],
        ),
        generations=(GenerationResult(content="{}", provider="test", model="test"),),
    )

    builder = EntityBuilder(provider)
    result = builder.build(
        goal="生成周报",
        project_snapshot=snapshot,
        evidence_store=store,
    )

    assert len(result.action_items) == 2
    assert result.action_items[0].claim_id == "C-0001"
    assert result.action_items[1].due_date_text.value == "本周五"
    assert result.action_items[1].source_refs == ["E-0002", "E-0003"]
    assert result.risks[1].mitigation.value == "排查原因"
    assert snapshot.action_items == []
    assert builder.get_decision_summary()["action_candidate_count"] == 2
    assert builder.get_decision_summary()["risk_candidate_count"] == 2


def test_entity_builder_rejects_non_candidate_claim_reference() -> None:
    snapshot, store = _fixture()
    provider = MagicMock()
    provider.generate_structured.return_value = StructuredGenerationResult(
        value=EntityProjectionDraft(
            action_items=[ProjectedActionDraft(claim_id="C-9999")]
        ),
        generations=(),
    )

    with pytest.raises(ValueError, match="unknown action candidate"):
        EntityBuilder(provider).build(
            goal="生成周报",
            project_snapshot=snapshot,
            evidence_store=store,
        )


def test_entity_builder_requires_a_decision_for_every_candidate() -> None:
    snapshot, store = _fixture()
    provider = MagicMock()
    provider.generate_structured.return_value = StructuredGenerationResult(
        value=EntityProjectionDraft(
            action_items=[ProjectedActionDraft(claim_id="C-0002")],
            risks=[ProjectedRiskDraft(claim_id="C-0004")],
        ),
        generations=(),
    )

    with pytest.raises(ValueError, match="incomplete or unknown action"):
        EntityBuilder(provider).build(
            goal="生成周报",
            project_snapshot=snapshot,
            evidence_store=store,
        )


def test_duplicate_exclusion_must_target_selected_candidate() -> None:
    snapshot, store = _fixture()
    provider = MagicMock()
    provider.generate_structured.return_value = StructuredGenerationResult(
        value=EntityProjectionDraft(
            action_items=[
                ProjectedActionDraft(claim_id="C-0001"),
                ProjectedActionDraft(claim_id="C-0002"),
            ],
            risks=[ProjectedRiskDraft(claim_id="C-0004")],
            risk_duplicates=[
                DuplicateCandidateDraft(
                    claim_id="C-0001",
                    duplicate_of_claim_id="C-9999",
                )
            ],
        ),
        generations=(),
    )

    with pytest.raises(ValueError, match="duplicate target must be a selected"):
        EntityBuilder(provider).build(
            goal="生成周报",
            project_snapshot=snapshot,
            evidence_store=store,
        )


def test_unsupported_enum_is_downgraded_to_unknown() -> None:
    snapshot, store = _fixture()
    provider = MagicMock()
    provider.generate_structured.return_value = StructuredGenerationResult(
        value=EntityProjectionDraft(
            action_items=[
                ProjectedActionDraft(claim_id="C-0001"),
                ProjectedActionDraft(claim_id="C-0002"),
            ],
            risks=[
                ProjectedRiskDraft(claim_id="C-0001"),
                ProjectedRiskDraft(
                    claim_id="C-0004",
                    severity="medium",
                    severity_evidence_refs=["E-0004"],
                )
            ],
        ),
        generations=(),
    )
    builder = EntityBuilder(provider)

    result = builder.build(
        goal="生成周报",
        project_snapshot=snapshot,
        evidence_store=store,
    )

    assert result.risks[0].severity.value == "unknown"
    assert result.risks[0].severity_evidence_refs == []
    assert builder.get_field_downgrade_count() == 1


def test_unrelated_risks_cannot_be_marked_duplicate() -> None:
    snapshot, store = _fixture()
    provider = MagicMock()
    provider.generate_structured.return_value = StructuredGenerationResult(
        value=EntityProjectionDraft(
            action_items=[
                ProjectedActionDraft(claim_id="C-0001"),
                ProjectedActionDraft(claim_id="C-0002"),
            ],
            risks=[ProjectedRiskDraft(claim_id="C-0004")],
            risk_duplicates=[
                DuplicateCandidateDraft(
                    claim_id="C-0001",
                    duplicate_of_claim_id="C-0004",
                )
            ],
        ),
        generations=(),
    )

    with pytest.raises(ValueError, match="lack deterministic overlap"):
        EntityBuilder(provider).build(
            goal="生成周报",
            project_snapshot=snapshot,
            evidence_store=store,
        )


def test_duplicate_text_gate_accepts_same_business_subject() -> None:
    assert EntityBuilder._duplicate_text_supported(
        "PROJ-101 支付重试逻辑 blocked，等待 API 设计确认",
        "支付重试逻辑 API 设计仍未确定，需要给出方案",
    )
    assert not EntityBuilder._duplicate_text_supported(
        "支付重试逻辑等待 API 设计",
        "线上告警增加，需要排查原因",
    )


def test_pending_issue_without_action_signal_is_not_a_candidate() -> None:
    claim = Claim(
        claim_id="C-0100",
        text="- PROJ-104: 订单导出功能 (未分配, P2, pending)",
        claim_type=ClaimType.EXPLICIT_FACT,
        category=ClaimCategory.ACTION_ITEM,
        evidence_refs=["E-0100"],
    )

    candidates = EntityBuilder._build_candidates([claim])

    assert candidates.action_claim_ids == []


def test_local_due_date_overrides_cross_evidence_date() -> None:
    snapshot, store = _fixture()
    provider = MagicMock()
    provider.generate_structured.return_value = StructuredGenerationResult(
        value=EntityProjectionDraft(
            action_items=[
                ProjectedActionDraft(
                    claim_id="C-0001",
                    due_date_text="本周五",
                    due_date_evidence_refs=["E-0003"],
                ),
                ProjectedActionDraft(claim_id="C-0002"),
            ],
            risks=[
                ProjectedRiskDraft(claim_id="C-0001"),
                ProjectedRiskDraft(claim_id="C-0004"),
            ],
        ),
        generations=(),
    )
    builder = EntityBuilder(provider)

    result = builder.build(
        goal="生成周报",
        project_snapshot=snapshot,
        evidence_store=store,
    )

    assert result.action_items[0].due_date_text.value == "本周"
    assert result.action_items[0].due_date_text.evidence_refs == ["E-0001"]
    assert builder.get_field_canonicalization_count() == 1


def test_blocker_dependency_is_not_accepted_as_mitigation() -> None:
    snapshot, store = _fixture()
    provider = MagicMock()
    provider.generate_structured.return_value = StructuredGenerationResult(
        value=EntityProjectionDraft(
            action_items=[
                ProjectedActionDraft(claim_id="C-0001"),
                ProjectedActionDraft(claim_id="C-0002"),
            ],
            risks=[
                ProjectedRiskDraft(
                    claim_id="C-0001",
                    mitigation="API 未确定",
                    mitigation_evidence_refs=["E-0001"],
                ),
                ProjectedRiskDraft(claim_id="C-0004"),
            ],
        ),
        generations=(),
    )
    builder = EntityBuilder(provider)

    result = builder.build(
        goal="生成周报",
        project_snapshot=snapshot,
        evidence_store=store,
    )

    assert result.risks[0].mitigation.value is None
    assert result.risks[0].mitigation.evidence_refs == []
    assert builder.get_field_downgrade_count() == 1


def test_unknown_field_evidence_is_rejected_before_downgrade() -> None:
    snapshot, store = _fixture()
    provider = MagicMock()
    provider.generate_structured.return_value = StructuredGenerationResult(
        value=EntityProjectionDraft(
            action_items=[
                ProjectedActionDraft(
                    claim_id="C-0001",
                    owner="李四",
                    owner_evidence_refs=["E-9999"],
                ),
                ProjectedActionDraft(claim_id="C-0002"),
            ],
            risks=[
                ProjectedRiskDraft(claim_id="C-0001"),
                ProjectedRiskDraft(claim_id="C-0004"),
            ],
        ),
        generations=(),
    )

    with pytest.raises(ValueError, match="unknown evidence"):
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
