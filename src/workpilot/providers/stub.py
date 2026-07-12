"""Stub provider for testing — returns fixed fixture data."""

from pydantic import BaseModel

from workpilot.providers.base import (
    EvidenceCandidate,
    EvidenceExtractionResult,
    EvidenceType,
    GenerationResult,
    LLMProvider,
    StructuredGenerationResult,
)


class StubProvider(LLMProvider):
    """Returns deterministic fixture data. No network calls."""

    def generate_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> StructuredGenerationResult:
        raise NotImplementedError("StubProvider.generate_structured not used in Phase 1")

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
    ) -> GenerationResult:
        return GenerationResult(
            content="Stub response: no real LLM invoked.",
            provider="stub",
            model="stub",
        )

    def extract_evidence_from_file(
        self,
        file_path: str,
        content: str,
        goal: str,
    ) -> EvidenceExtractionResult:
        """Extract fixture evidence from a single file.

        Heuristic: scan lines for keywords to assign evidence types,
        extract meaningful lines rather than just the first line.
        """
        lines = content.strip().splitlines()
        if not lines:
            return EvidenceExtractionResult(candidates=[])

        evidences: list[EvidenceCandidate] = []
        evidence_counter = 0

        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            ev_type = self._classify_line(stripped)
            if ev_type is None:
                continue

            evidence_counter += 1
            evidences.append(
                EvidenceCandidate(
                    evidence_id="",  # filled by extractor
                    source_file=file_path,
                    quote=stripped,
                    start_line=i,
                    end_line=i,
                    evidence_type=ev_type,
                )
            )

        if not evidences:
            evidences.append(
                EvidenceCandidate(
                    evidence_id="",
                    source_file=file_path,
                    quote=lines[0].strip(),
                    start_line=1,
                    end_line=1,
                    evidence_type=EvidenceType.CONTEXT,
                )
            )

        return EvidenceExtractionResult(candidates=evidences)

    @staticmethod
    def _classify_line(line: str) -> str | None:
        """Classify a line by keyword matching. Returns evidence type or None."""
        lower = line.lower()

        risk_signals = ["blocked", "告警", "风险", "delay", "阻塞", "上升", "失败"]
        if any(s in lower for s in risk_signals):
            return EvidenceType.RISK

        decision_signals = ["决策", "决定", "确定", "定为", "提升为"]
        if any(s in lower for s in decision_signals):
            return EvidenceType.DECISION

        progress_signals = ["完成", "done", "已完成", "in progress", "进行中"]
        if any(s in lower for s in progress_signals):
            return EvidenceType.PROGRESS

        action_signals = ["下一步", "需要", "输出", "启动", "协调"]
        if any(s in lower for s in action_signals):
            return EvidenceType.ACTION_ITEM

        if line.startswith("- ") and ":" in line:
            return EvidenceType.CONTEXT

        return None
