"""Render verified project claims into user-facing artifacts."""

import re

from workpilot.contracts import MissionContract
from workpilot.domain import Claim, ClaimCategory, ClaimType, ProjectSnapshot
from workpilot.providers.base import LLMProvider


class Synthesizer:
    """Deterministically render artifacts from a structured snapshot.

    Language understanding happens while ClaimBuilder creates claims. Keeping
    rendering deterministic ensures Markdown and JSON cannot introduce facts
    that bypass the Claim/Evidence verification boundary.
    """

    def __init__(self, provider: LLMProvider) -> None:
        # Kept for API compatibility and future presentation-only rewriting.
        self.provider = provider

    def generate(
        self,
        contract: MissionContract,
        project_snapshot: ProjectSnapshot,
    ) -> dict[str, str | dict]:
        """Render all artifacts from the same structured claims."""
        del contract  # Contract controls execution; rendering is deterministic.
        return {
            "weekly_report.md": self._render_weekly_report(project_snapshot),
            "risks.json": self._render_risks(project_snapshot),
            "action_items.json": self._render_action_items(project_snapshot),
            "project_snapshot.json": project_snapshot.model_dump(mode="json"),
        }

    def _render_weekly_report(self, snapshot: ProjectSnapshot) -> str:
        sections = [
            ("本周进展", {ClaimCategory.PROGRESS}),
            ("关键决策", {ClaimCategory.DECISION, ClaimCategory.REQUIREMENT_CHANGE}),
            ("风险与阻塞", {ClaimCategory.RISK, ClaimCategory.BLOCKER}),
            ("下周计划", {ClaimCategory.ACTION_ITEM}),
        ]
        parts: list[str] = []
        for title, categories in sections:
            parts.append(f"## {title}\n\n")
            claims = [claim for claim in snapshot.claims if claim.category in categories]
            if not claims:
                parts.append("- No data. [unknown]\n")
            else:
                for claim in claims:
                    parts.append(f"- {self._render_claim(claim)}\n")
            parts.append("\n")
        return "".join(parts).rstrip() + "\n"

    @staticmethod
    def _render_claim(claim: Claim) -> str:
        display_text = Synthesizer._display_text(claim.text)
        if claim.claim_type == ClaimType.UNKNOWN:
            return f"{display_text} [unknown]"
        refs = " ".join(f"[{ref}]" for ref in claim.evidence_refs)
        return f"{display_text} {refs}".rstrip()

    @staticmethod
    def _display_text(text: str) -> str:
        """Remove one source list marker when rendering inside a new list."""
        return re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", text, count=1)

    @staticmethod
    def _render_risks(snapshot: ProjectSnapshot) -> dict:
        risk_claims = [
            claim
            for claim in snapshot.claims
            if claim.category in {ClaimCategory.RISK, ClaimCategory.BLOCKER}
        ]
        return {
            "schema_version": "0.2",
            "risks": [
                {
                    "risk_id": f"R-{index:04d}",
                    "claim_id": claim.claim_id,
                    "title": Synthesizer._display_text(claim.text)[:50],
                    "description": claim.text,
                    "severity": "unknown",
                    "status": "open",
                    "source_refs": claim.evidence_refs,
                    "mitigation": None,
                }
                for index, claim in enumerate(risk_claims, start=1)
            ],
        }

    @staticmethod
    def _render_action_items(snapshot: ProjectSnapshot) -> dict:
        action_claims = [
            claim
            for claim in snapshot.claims
            if claim.category == ClaimCategory.ACTION_ITEM
        ]
        return {
            "schema_version": "0.2",
            "action_items": [
                {
                    "action_id": f"A-{index:04d}",
                    "claim_id": claim.claim_id,
                    "title": Synthesizer._display_text(claim.text)[:50],
                    "owner": None,
                    "due_date": None,
                    "source_refs": claim.evidence_refs,
                    "status": "open",
                }
                for index, claim in enumerate(action_claims, start=1)
            ],
        }
