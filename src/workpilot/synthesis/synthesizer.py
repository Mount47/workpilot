"""Synthesizer — generates artifacts from evidence."""

import json

from workpilot.contracts import MissionContract
from workpilot.evidence.store import EvidenceStore
from workpilot.providers.base import EvidenceType, LLMProvider
from workpilot.providers.stub import StubProvider


SYNTHESIS_SYSTEM = """You are a project report writer. Given a set of evidence items extracted from project files, generate a structured weekly report.

Rules:
- Every conclusion MUST reference its source evidence using [E-XXXX] citation format.
- If there is no evidence for a section, write "No data. [unknown]" — do NOT fabricate.
- Group evidence logically: progress, decisions, risks/blockers, action items.
- Be concise and factual. Summarize related items; don't just list raw quotes.
- Write in the same language as the evidence (Chinese evidence → Chinese report).
- The report should be useful to a project manager scanning for status.

Output format (markdown):
## 本周进展
- <summary of progress items> [E-XXXX]

## 关键决策
- <decisions made> [E-XXXX]

## 风险与阻塞
- <risks and blockers> [E-XXXX]

## 下周计划
- <action items and next steps> [E-XXXX]"""

RISKS_SYSTEM = """You are a risk analyst. Given project evidence, identify and structure risks.

Rules:
- Only report risks that have explicit evidence. Do NOT infer risks.
- Each risk must have source_refs pointing to real evidence IDs.
- Assess severity as: critical, high, medium, low.
- If no risks are found, return an empty risks array.

Respond with ONLY valid JSON matching this structure:
{
  "schema_version": "0.1",
  "risks": [
    {
      "risk_id": "R-0001",
      "title": "short title",
      "description": "what the risk is",
      "severity": "high",
      "status": "open",
      "source_refs": ["E-0001"],
      "mitigation": "suggested action or null"
    }
  ]
}"""

ACTION_ITEMS_SYSTEM = """You are a project coordinator. Given project evidence, extract action items.

Rules:
- Only include action items explicitly stated in the evidence. Do NOT infer tasks.
- Each item must have source_refs pointing to real evidence IDs.
- Extract owner if mentioned; otherwise set to null.
- Extract due date if mentioned; otherwise set to null.

Respond with ONLY valid JSON matching this structure:
{
  "schema_version": "0.1",
  "action_items": [
    {
      "action_id": "A-0001",
      "title": "short description",
      "owner": "name or null",
      "due_date": "YYYY-MM-DD or null",
      "source_refs": ["E-0001"],
      "status": "open"
    }
  ]
}"""


class Synthesizer:
    """Generates artifacts based on contract and evidence.

    Uses LLM provider for real generation when available,
    falls back to deterministic template for StubProvider.
    """

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def generate(
        self,
        contract: MissionContract,
        evidence_store: EvidenceStore,
        feedback: str | None = None,
    ) -> dict[str, str | dict]:
        """Generate all artifacts. Returns {filename: content}.

        If feedback is provided (from a failed verification pass), it is
        injected into each prompt so the model can correct its output.
        StubProvider ignores feedback — it is deterministic and cannot revise.
        """
        if isinstance(self.provider, StubProvider):
            return self._generate_stub(contract, evidence_store)
        return self._generate_with_llm(contract, evidence_store, feedback)

    def _generate_with_llm(
        self,
        contract: MissionContract,
        evidence_store: EvidenceStore,
        feedback: str | None = None,
    ) -> dict[str, str | dict]:
        evidence_block = self._format_evidence_block(evidence_store)
        feedback_block = self._format_feedback_block(feedback)

        report_prompt = (
            f"Goal: {contract.goal}\n\n"
            f"Evidence:\n{evidence_block}\n\n"
            f"{feedback_block}"
            "Generate the weekly report."
        )
        weekly_report = self.provider.generate_text(
            prompt=report_prompt,
            system_prompt=SYNTHESIS_SYSTEM,
            temperature=0.0,
        )

        risks_prompt = (
            f"Goal: {contract.goal}\n\n"
            f"Evidence:\n{evidence_block}\n\n"
            f"{feedback_block}"
            "Identify risks and return structured JSON."
        )
        risks_raw = self.provider.generate_text(
            prompt=risks_prompt,
            system_prompt=RISKS_SYSTEM,
            temperature=0.0,
        )
        risks = self._parse_json_safe(risks_raw, {
            "schema_version": "0.1",
            "risks": [],
        })

        actions_prompt = (
            f"Goal: {contract.goal}\n\n"
            f"Evidence:\n{evidence_block}\n\n"
            f"{feedback_block}"
            "Extract action items and return structured JSON."
        )
        actions_raw = self.provider.generate_text(
            prompt=actions_prompt,
            system_prompt=ACTION_ITEMS_SYSTEM,
            temperature=0.0,
        )
        action_items = self._parse_json_safe(actions_raw, {
            "schema_version": "0.1",
            "action_items": [],
        })

        return {
            "weekly_report.md": weekly_report,
            "risks.json": risks,
            "action_items.json": action_items,
        }

    @staticmethod
    def _format_feedback_block(feedback: str | None) -> str:
        """Render verification feedback into a prompt block, or empty string."""
        if not feedback:
            return ""
        return (
            "IMPORTANT — your previous attempt failed verification. "
            "Fix these issues and do NOT repeat them:\n"
            f"{feedback}\n\n"
        )

    def _generate_stub(
        self,
        contract: MissionContract,
        evidence_store: EvidenceStore,
    ) -> dict[str, str | dict]:
        """Deterministic generation for StubProvider — no LLM calls."""
        evidences = evidence_store.list_all()
        evidence_refs = [ev.evidence_id for ev in evidences]

        progress = [ev for ev in evidences if ev.evidence_type == EvidenceType.PROGRESS]
        decisions = [ev for ev in evidences if ev.evidence_type == EvidenceType.DECISION]
        risks = [ev for ev in evidences if ev.evidence_type in (EvidenceType.RISK, EvidenceType.BLOCKER)]
        actions = [ev for ev in evidences if ev.evidence_type == EvidenceType.ACTION_ITEM]

        lines = ["## 本周进展\n"]
        if progress:
            for ev in progress:
                lines.append(f"- {ev.quote} [{ev.evidence_id}]\n")
        else:
            lines.append("- No data. [unknown]\n")

        lines.append("\n## 关键决策\n")
        if decisions:
            for ev in decisions:
                lines.append(f"- {ev.quote} [{ev.evidence_id}]\n")
        else:
            lines.append("- No data. [unknown]\n")

        lines.append("\n## 风险与阻塞\n")
        if risks:
            for ev in risks:
                lines.append(f"- {ev.quote} [{ev.evidence_id}]\n")
        else:
            lines.append("- No data. [unknown]\n")

        lines.append("\n## 下周计划\n")
        if actions:
            for ev in actions:
                lines.append(f"- {ev.quote} [{ev.evidence_id}]\n")
        else:
            lines.append("- No data. [unknown]\n")

        weekly_report = "".join(lines)

        risks_json = {
            "schema_version": "0.1",
            "risks": [
                {
                    "risk_id": f"R-{i+1:04d}",
                    "title": ev.quote[:50],
                    "description": ev.quote,
                    "severity": "unknown",
                    "status": "open",
                    "source_refs": [ev.evidence_id],
                    "mitigation": None,
                }
                for i, ev in enumerate(risks)
            ],
        }

        action_items_json = {
            "schema_version": "0.1",
            "action_items": [
                {
                    "action_id": f"A-{i+1:04d}",
                    "title": ev.quote[:50],
                    "owner": None,
                    "due_date": None,
                    "source_refs": [ev.evidence_id],
                    "status": "open",
                }
                for i, ev in enumerate(actions)
            ],
        }

        return {
            "weekly_report.md": weekly_report,
            "risks.json": risks_json,
            "action_items.json": action_items_json,
        }

    @staticmethod
    def _format_evidence_block(evidence_store: EvidenceStore) -> str:
        lines = []
        for ev in evidence_store.list_all():
            lines.append(
                f"[{ev.evidence_id}] ({ev.evidence_type}) "
                f"from {ev.source_file}:{ev.start_line}-{ev.end_line}\n"
                f"  \"{ev.quote}\""
            )
        return "\n\n".join(lines)

    @staticmethod
    def _parse_json_safe(raw: str, fallback: dict) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            first_nl = text.index("\n") if "\n" in text else len(text)
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return fallback
