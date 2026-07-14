"""Tests for deterministic presentation without mutating evidence-backed claims."""

from workpilot.domain import (
    Claim,
    ClaimCategory,
    ClaimType,
    ProjectSnapshot,
    Risk,
)
from workpilot.providers.stub import StubProvider
from workpilot.synthesis.synthesizer import Synthesizer


def _claim(text: str, category: ClaimCategory) -> Claim:
    return Claim(
        claim_id="C-0001",
        text=text,
        claim_type=ClaimType.EXPLICIT_FACT,
        category=category,
        evidence_refs=["E-0001"],
    )


def test_weekly_report_removes_one_source_list_marker() -> None:
    snapshot = ProjectSnapshot(
        project_id="project",
        snapshot_id="snapshot",
        claims=[_claim("- 已完成设计评审。", ClaimCategory.PROGRESS)],
    )

    report = Synthesizer(StubProvider())._render_weekly_report(snapshot)

    assert "- 已完成设计评审。 [E-0001]" in report
    assert "- - 已完成设计评审" not in report
    assert snapshot.claims[0].text == "- 已完成设计评审。"


def test_display_text_removes_numeric_marker_but_not_business_text() -> None:
    assert Synthesizer._display_text("1. 风险增加。") == "风险增加。"
    assert Synthesizer._display_text("PROJ-101 blocked") == "PROJ-101 blocked"


def test_structured_titles_drop_marker_while_description_stays_exact() -> None:
    claim = _claim("- 服务被阻塞。", ClaimCategory.RISK)
    snapshot = ProjectSnapshot(
        project_id="project",
        snapshot_id="snapshot",
        claims=[claim],
        risks=[
            Risk(
                risk_id="R-0001",
                claim_id=claim.claim_id,
                description=claim.text,
                source_refs=claim.evidence_refs,
            )
        ],
    )

    risks = Synthesizer(StubProvider())._render_risks(snapshot)

    assert risks["risks"][0]["title"] == "服务被阻塞。"
    assert risks["risks"][0]["description"] == "- 服务被阻塞。"
