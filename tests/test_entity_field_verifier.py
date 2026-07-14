"""Tests for deterministic field-level Evidence verification."""

from workpilot.domain import (
    ActionItem,
    Claim,
    ClaimCategory,
    ClaimType,
    Evidence,
    ProjectSnapshot,
    SupportedText,
    SourceLocator,
)
from workpilot.evidence.store import EvidenceStore
from workpilot.verification import EntityFieldVerifier


def _store() -> EvidenceStore:
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
            quote="王五负责另一个事项。",
            evidence_type="action_item",
        )
    )
    return store


def _snapshot(owner: SupportedText) -> ProjectSnapshot:
    text = "李四：2026-07-18 输出 API 设计文档。"
    return ProjectSnapshot(
        project_id="project-1",
        snapshot_id="snapshot-1",
        source_ids=["meeting.md"],
        claims=[
            Claim(
                claim_id="C-0001",
                text=text,
                claim_type=ClaimType.EXPLICIT_FACT,
                category=ClaimCategory.ACTION_ITEM,
                evidence_refs=["E-0001"],
            )
        ],
        action_items=[
            ActionItem(
                action_id="A-0001",
                claim_id="C-0001",
                description=text,
                owner=owner,
                due_date_text=SupportedText(
                    value="2026-07-18",
                    evidence_refs=["E-0001"],
                ),
                source_refs=["E-0001"],
            )
        ],
    )


def test_supported_entity_fields_pass_exact_substring_verification() -> None:
    results = EntityFieldVerifier(_store()).verify(
        _snapshot(SupportedText(value="李四", evidence_refs=["E-0001"]))
    )

    assert not [result for result in results if result.status == "failed"]
    assert sum(result.check_id == "entity.field_supported" for result in results) == 2
    assert sum(result.check_id == "entity.field_unknown" for result in results) == 1


def test_invented_field_value_fails_verification() -> None:
    results = EntityFieldVerifier(_store()).verify(
        _snapshot(SupportedText(value="赵六", evidence_refs=["E-0001"]))
    )

    failure = next(result for result in results if result.status == "failed")
    assert failure.check_id == "entity.field_supported"
    assert failure.location == "action_items.A-0001.owner"


def test_field_cannot_cite_outside_parent_claim() -> None:
    results = EntityFieldVerifier(_store()).verify(
        _snapshot(SupportedText(value="王五", evidence_refs=["E-0002"]))
    )

    failure = next(result for result in results if result.status == "failed")
    assert failure.check_id == "entity.field_parent_support"


def test_unknown_optional_field_is_explicitly_observable() -> None:
    results = EntityFieldVerifier(_store()).verify(_snapshot(SupportedText()))

    owner = next(result for result in results if result.location.endswith(".owner"))
    assert owner.check_id == "entity.field_unknown"
    assert owner.status == "passed"
