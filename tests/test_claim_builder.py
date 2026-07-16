"""Tests for building project claims from validated evidence."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from workpilot.analysis.claim_builder import (
    ActionItemDraft,
    ClaimBuilder,
    ClaimDraft,
    ClaimDraftCollection,
    RiskDraft,
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
    system_prompt = provider.generate_structured.call_args.kwargs["system_prompt"]
    assert "Cross-source fields are allowed" in system_prompt
    assert "Before leaving an entity field null" in system_prompt


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


def test_action_claim_draft_requires_structured_fields() -> None:
    with pytest.raises(ValidationError, match="requires action_item fields"):
        ClaimDraft(
            text="李四：输出方案。",
            claim_type=ClaimType.EXPLICIT_FACT,
            category=ClaimCategory.ACTION_ITEM,
            evidence_refs=["E-0001"],
        )


def test_action_field_value_requires_its_evidence_refs() -> None:
    with pytest.raises(ValidationError, match="owner requires evidence references"):
        ActionItemDraft(owner="李四")


def test_model_facing_business_fields_use_flat_json_schema() -> None:
    schema = ClaimDraftCollection.model_json_schema()
    action_properties = schema["$defs"]["ActionItemDraft"]["properties"]
    risk_properties = schema["$defs"]["RiskDraft"]["properties"]

    assert "$ref" not in action_properties["owner"]
    assert "$ref" not in action_properties["due_date_text"]
    assert "$ref" not in risk_properties["owner"]
    assert "$ref" not in risk_properties["mitigation"]
    assert "owner_evidence_refs" in action_properties
    assert "mitigation_evidence_refs" in risk_properties


def test_provider_builder_materializes_supported_action_and_risk() -> None:
    store = EvidenceStore(run_id="run-1")
    store.insert(
        Evidence(
            evidence_id="E-0001",
            locator=SourceLocator.for_file_lines("meeting.md", 1, 1),
            quote="李四：2026-07-18 输出 API 设计文档。",
            evidence_type="action_item",
        )
    )
    store.insert(
        Evidence(
            evidence_id="E-0002",
            locator=SourceLocator.for_file_lines("meeting.md", 2, 2),
            quote="李四负责风险，缓解措施为降级发布。",
            evidence_type="risk",
        )
    )
    provider = MagicMock()
    provider.generate_structured.return_value = StructuredGenerationResult(
        value=ClaimDraftCollection(
            claims=[
                ClaimDraft(
                    text="李四：2026-07-18 输出 API 设计文档。",
                    claim_type=ClaimType.EXPLICIT_FACT,
                    category=ClaimCategory.ACTION_ITEM,
                    evidence_refs=["E-0001"],
                    action_item=ActionItemDraft(
                        owner="李四",
                        owner_evidence_refs=["E-0001"],
                        due_date_text="2026-07-18",
                        due_date_evidence_refs=["E-0001"],
                    ),
                ),
                ClaimDraft(
                    text="李四负责风险，缓解措施为降级发布。",
                    claim_type=ClaimType.EXPLICIT_FACT,
                    category=ClaimCategory.RISK,
                    evidence_refs=["E-0002"],
                    risk=RiskDraft(
                        owner="李四",
                        owner_evidence_refs=["E-0002"],
                        mitigation="降级发布",
                        mitigation_evidence_refs=["E-0002"],
                    ),
                ),
            ]
        ),
        generations=(GenerationResult(content="{}", provider="test", model="test"),),
    )

    snapshot = ClaimBuilder(provider).build(
        project_id="project-1",
        snapshot_id="snapshot-1",
        goal="生成项目报告",
        evidence_store=store,
    )

    assert snapshot.action_items[0].owner.value == "李四"
    assert snapshot.action_items[0].due_date_text.value == "2026-07-18"
    assert snapshot.action_items[0].due_date.isoformat() == "2026-07-18"
    assert snapshot.risks[0].owner.value == "李四"
    assert snapshot.risks[0].mitigation.value == "降级发布"


def test_dedicated_action_evidence_is_recovered_as_independent_claim() -> None:
    store = EvidenceStore(run_id="run-1")
    store.insert(
        Evidence(
            evidence_id="E-0001",
            locator=SourceLocator.for_file_lines("meeting.md", 1, 1),
            quote="李四需要本周给出支付方案。",
            evidence_type="action_item",
        )
    )
    store.insert(
        Evidence(
            evidence_id="E-0002",
            locator=SourceLocator.for_file_lines("meeting.md", 2, 2),
            quote="- 李四：输出支付 API 设计文档。",
            evidence_type="action_item",
        )
    )
    provider = MagicMock()
    provider.generate_structured.return_value = StructuredGenerationResult(
        value=ClaimDraftCollection(
            claims=[
                ClaimDraft(
                    text="李四需要本周给出支付方案。",
                    claim_type=ClaimType.EXPLICIT_FACT,
                    category=ClaimCategory.ACTION_ITEM,
                    evidence_refs=["E-0001", "E-0002"],
                    primary_evidence_ref="E-0001",
                    action_item=ActionItemDraft(
                        owner="李四",
                        owner_evidence_refs=["E-0001"],
                    ),
                )
            ]
        ),
        generations=(),
    )
    builder = ClaimBuilder(provider)

    snapshot = builder.build(
        project_id="project-1",
        snapshot_id="snapshot-1",
        goal="生成项目报告",
        evidence_store=store,
    )

    assert [claim.text for claim in snapshot.claims] == [
        "李四需要本周给出支付方案。",
        "- 李四：输出支付 API 设计文档。",
    ]
    assert snapshot.action_items[1].claim_id == snapshot.claims[1].claim_id
    assert builder.get_dedicated_action_recovery_count() == 1


def test_explicit_claim_restores_only_a_missing_source_list_marker() -> None:
    store = EvidenceStore(run_id="run-1")
    store.insert(
        Evidence(
            evidence_id="E-0001",
            locator=SourceLocator.for_file_lines("issues.md", 1, 1),
            quote="- PROJ-101: 等待 API 设计确认。",
            evidence_type="blocker",
        )
    )
    provider = MagicMock()
    provider.generate_structured.return_value = StructuredGenerationResult(
        value=ClaimDraftCollection(
            claims=[
                ClaimDraft(
                    text="PROJ-101: 等待 API 设计确认。",
                    claim_type=ClaimType.EXPLICIT_FACT,
                    category=ClaimCategory.BLOCKER,
                    evidence_refs=["E-0001"],
                    risk=RiskDraft(),
                )
            ]
        ),
        generations=(GenerationResult(content="{}", provider="test", model="test"),),
    )
    builder = ClaimBuilder(provider)

    snapshot = builder.build(
        project_id="project-1",
        snapshot_id="snapshot-1",
        goal="生成项目报告",
        evidence_store=store,
    )

    assert snapshot.claims[0].text == "- PROJ-101: 等待 API 设计确认。"
    assert snapshot.risks[0].description == snapshot.claims[0].text
    assert builder.get_claim_text_repair_count() == 1


def test_explicit_claim_does_not_repair_a_paraphrase() -> None:
    store = _evidence_store()
    store.insert(
        Evidence(
            evidence_id="E-0002",
            locator=SourceLocator.for_file_lines("meeting.md", 4, 4),
            quote="另一个项目事实。",
            evidence_type="context",
        )
    )
    provider = MagicMock()
    provider.generate_structured.return_value = StructuredGenerationResult(
        value=ClaimDraftCollection(
            claims=[
                ClaimDraft(
                    text="计划已经确定。",
                    claim_type=ClaimType.EXPLICIT_FACT,
                    category=ClaimCategory.DECISION,
                    evidence_refs=["E-0001", "E-0002"],
                )
            ]
        ),
        generations=(GenerationResult(content="{}", provider="test", model="test"),),
    )
    builder = ClaimBuilder(provider)

    snapshot = builder.build(
        project_id="project-1",
        snapshot_id="snapshot-1",
        goal="生成项目报告",
        evidence_store=store,
    )

    assert snapshot.claims[0].text == "计划已经确定。"
    assert builder.get_claim_text_repair_count() == 0


def test_explicit_claim_uses_selected_primary_evidence_quote() -> None:
    provider = MagicMock()
    provider.generate_structured.return_value = StructuredGenerationResult(
        value=ClaimDraftCollection(
            claims=[
                ClaimDraft(
                    text="模型改写的文本",
                    claim_type=ClaimType.EXPLICIT_FACT,
                    category=ClaimCategory.DECISION,
                    evidence_refs=["E-0001"],
                    primary_evidence_ref="E-0001",
                )
            ]
        ),
        generations=(GenerationResult(content="{}", provider="test", model="test"),),
    )
    builder = ClaimBuilder(provider)

    snapshot = builder.build(
        project_id="project-1",
        snapshot_id="snapshot-1",
        goal="生成项目报告",
        evidence_store=_evidence_store(),
    )

    assert snapshot.claims[0].text == "项目计划已经确认。"
    assert builder.get_claim_text_repair_count() == 1


def test_primary_evidence_must_belong_to_claim_refs() -> None:
    with pytest.raises(ValidationError, match="must belong"):
        ClaimDraft(
            text="项目计划已经确认。",
            claim_type=ClaimType.EXPLICIT_FACT,
            category=ClaimCategory.DECISION,
            evidence_refs=["E-0001"],
            primary_evidence_ref="E-9999",
        )
