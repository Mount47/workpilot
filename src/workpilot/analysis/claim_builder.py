"""Build a structured project snapshot from validated evidence."""

from pydantic import BaseModel, Field, model_validator

from workpilot.domain import (
    Claim,
    ClaimCategory,
    ClaimType,
    ProjectSnapshot,
)
from workpilot.evidence.store import EvidenceStore
from workpilot.providers.base import GenerationResult, LLMProvider
from workpilot.providers.stub import StubProvider


CLAIM_BUILDER_SYSTEM = """You are a project analysis engine.
Convert the supplied evidence into the smallest independently verifiable claims.

Rules:
- Use only the supplied evidence; never add facts from general knowledge.
- Every non-unknown claim must cite one or more supplied evidence IDs.
- explicit_fact text must be an exact evidence quote, without paraphrasing.
- Use derived_fact only when derivation describes a deterministic calculation.
- Use analytical_judgement only for a clearly labelled analysis supported by evidence.
- Do not emit duplicate claims.
- Categories: progress, decision, risk, blocker, action_item, context, requirement_change.
- If evidence is insufficient, omit the claim instead of inventing it.
"""


class ClaimDraft(BaseModel):
    """Model-produced claim before WorkPilot assigns a stable ID."""

    text: str = Field(min_length=1)
    claim_type: ClaimType
    category: ClaimCategory
    evidence_refs: list[str] = Field(default_factory=list)
    derivation: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_support_contract(self) -> "ClaimDraft":
        """Reject malformed model output while the Provider can still retry."""
        if self.claim_type == ClaimType.UNKNOWN:
            if self.evidence_refs or self.derivation is not None:
                raise ValueError("unknown claim cannot have support or derivation")
            return self
        if not self.evidence_refs:
            raise ValueError("non-unknown claim requires evidence references")
        if self.claim_type == ClaimType.DERIVED_FACT and not self.derivation:
            raise ValueError("derived fact requires a derivation")
        return self


class ClaimDraftCollection(BaseModel):
    """Structured provider response for claim building."""

    claims: list[ClaimDraft] = Field(default_factory=list)


class ClaimBuilder:
    """Create the single structured source of truth used by artifacts."""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self._model_calls: list[GenerationResult] = []

    def build(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        goal: str,
        evidence_store: EvidenceStore,
        feedback: str | None = None,
    ) -> ProjectSnapshot:
        """Build a ProjectSnapshot from validated evidence."""
        self._model_calls = []
        evidences = evidence_store.list_all()
        source_ids = sorted({evidence.locator.source_id for evidence in evidences})

        if not evidences:
            return ProjectSnapshot(
                project_id=project_id,
                snapshot_id=snapshot_id,
                source_ids=source_ids,
                claims=[],
            )

        if isinstance(self.provider, StubProvider):
            claims = [
                Claim(
                    claim_id=f"C-{index:04d}",
                    text=evidence.quote,
                    claim_type=ClaimType.EXPLICIT_FACT,
                    category=ClaimCategory(evidence.evidence_type),
                    evidence_refs=[evidence.evidence_id],
                    confidence=1.0,
                )
                for index, evidence in enumerate(evidences, start=1)
            ]
        else:
            claims = self._build_with_provider(
                goal=goal,
                evidence_store=evidence_store,
                feedback=feedback,
            )

        return ProjectSnapshot(
            project_id=project_id,
            snapshot_id=snapshot_id,
            source_ids=source_ids,
            claims=claims,
        )

    def _build_with_provider(
        self,
        *,
        goal: str,
        evidence_store: EvidenceStore,
        feedback: str | None,
    ) -> list[Claim]:
        evidence_block = self._format_evidence(evidence_store)
        feedback_block = f"\nCorrection feedback:\n{feedback}\n" if feedback else ""
        prompt = (
            f"Goal: {goal}\n\n"
            f"Evidence:\n{evidence_block}\n"
            f"{feedback_block}\n"
            "Return the supported project claims."
        )
        response = self.provider.generate_structured(
            prompt=prompt,
            response_model=ClaimDraftCollection,
            system_prompt=CLAIM_BUILDER_SYSTEM,
            temperature=0.0,
        )
        self._model_calls.extend(response.generations)
        if not isinstance(response.value, ClaimDraftCollection):
            raise TypeError("provider returned an unexpected claim response type")

        return [
            Claim(
                claim_id=f"C-{index:04d}",
                text=draft.text,
                claim_type=draft.claim_type,
                category=draft.category,
                evidence_refs=list(dict.fromkeys(draft.evidence_refs)),
                derivation=draft.derivation,
                confidence=draft.confidence,
            )
            for index, draft in enumerate(response.value.claims, start=1)
        ]

    def get_model_calls(self) -> list[GenerationResult]:
        """Return physical provider calls made by the latest build."""
        return list(self._model_calls)

    @staticmethod
    def _format_evidence(evidence_store: EvidenceStore) -> str:
        return "\n\n".join(
            (
                f"[{evidence.evidence_id}] category={evidence.evidence_type} "
                f"source={evidence.locator.display()}\n"
                f'"{evidence.quote}"'
            )
            for evidence in evidence_store.list_all()
        )
