"""Build a structured project snapshot from validated evidence."""

import re
from datetime import date

from pydantic import BaseModel, Field, model_validator

from workpilot.domain import (
    ActionItem,
    ActionStatus,
    Claim,
    ClaimCategory,
    ClaimType,
    ProjectSnapshot,
    Risk,
    RiskLevel,
    RiskStatus,
    SupportedText,
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
- For explicit_fact, set primary_evidence_ref to the Evidence ID whose quote is the claim text.
- Use derived_fact only when derivation describes a deterministic calculation.
- Use analytical_judgement only for a clearly labelled analysis supported by evidence.
- Do not emit duplicate claims.
- Categories: progress, decision, risk, blocker, action_item, context, requirement_change.
- For every supported action_item claim, populate action_item fields in the same object.
- For every supported risk or blocker claim, populate risk fields in the same object.
- owner, due_date_text, and mitigation are strings or null, never nested objects.
- Every non-null business field must include its matching *_evidence_refs list.
- owner, due_date_text, and mitigation values must be exact substrings of their cited evidence.
- Field evidence refs must also appear in the parent claim evidence_refs.
- Before leaving an entity field null, inspect every supplied Evidence item for the same project item or subject.
- Cross-source fields are allowed: add the supporting Evidence ID to both the parent claim evidence_refs and the field refs.
- Prefer a dedicated action record as the primary action Claim, then enrich it with owner or due-date Evidence about the same work item.
- A concrete response phrase such as "排查原因" or "降级发布" may be risk mitigation when explicitly written.
- Do not merge evidence for different project IDs, work items, or owners.
- If a field is not explicit in evidence, set its value to null and refs to [].
- Use status/severity=unknown unless the exact normalized value is explicit in evidence.
- If evidence is insufficient, omit the claim instead of inventing it.
"""


class ActionItemDraft(BaseModel):
    """Model-produced action fields before stable entity IDs are assigned."""

    owner: str | None = None
    owner_evidence_refs: list[str] = Field(default_factory=list)
    due_date_text: str | None = None
    due_date_evidence_refs: list[str] = Field(default_factory=list)
    status: ActionStatus = ActionStatus.UNKNOWN
    status_evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_field_support(self) -> "ActionItemDraft":
        self.owner = self._validate_text_pair(
            self.owner,
            self.owner_evidence_refs,
            "owner",
        )
        self.due_date_text = self._validate_text_pair(
            self.due_date_text,
            self.due_date_evidence_refs,
            "due_date_text",
        )
        if self.status == ActionStatus.UNKNOWN and self.status_evidence_refs:
            raise ValueError("unknown action status cannot cite evidence")
        if self.status != ActionStatus.UNKNOWN and not self.status_evidence_refs:
            raise ValueError("known action status requires evidence references")
        return self

    @staticmethod
    def _validate_text_pair(
        value: str | None,
        evidence_refs: list[str],
        field_name: str,
    ) -> str | None:
        if value is None:
            if evidence_refs:
                raise ValueError(f"null {field_name} cannot cite evidence")
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} cannot be blank")
        if not evidence_refs:
            raise ValueError(f"non-null {field_name} requires evidence references")
        return normalized


class RiskDraft(BaseModel):
    """Model-produced risk fields before stable entity IDs are assigned."""

    owner: str | None = None
    owner_evidence_refs: list[str] = Field(default_factory=list)
    severity: RiskLevel = RiskLevel.UNKNOWN
    severity_evidence_refs: list[str] = Field(default_factory=list)
    status: RiskStatus = RiskStatus.UNKNOWN
    status_evidence_refs: list[str] = Field(default_factory=list)
    mitigation: str | None = None
    mitigation_evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_enum_support(self) -> "RiskDraft":
        self.owner = ActionItemDraft._validate_text_pair(
            self.owner,
            self.owner_evidence_refs,
            "owner",
        )
        self.mitigation = ActionItemDraft._validate_text_pair(
            self.mitigation,
            self.mitigation_evidence_refs,
            "mitigation",
        )
        if self.severity == RiskLevel.UNKNOWN and self.severity_evidence_refs:
            raise ValueError("unknown risk severity cannot cite evidence")
        if self.severity != RiskLevel.UNKNOWN and not self.severity_evidence_refs:
            raise ValueError("known risk severity requires evidence references")
        if self.status == RiskStatus.UNKNOWN and self.status_evidence_refs:
            raise ValueError("unknown risk status cannot cite evidence")
        if self.status != RiskStatus.UNKNOWN and not self.status_evidence_refs:
            raise ValueError("known risk status requires evidence references")
        return self


class ClaimDraft(BaseModel):
    """Model-produced claim before WorkPilot assigns a stable ID."""

    text: str = Field(min_length=1)
    claim_type: ClaimType
    category: ClaimCategory
    evidence_refs: list[str] = Field(default_factory=list)
    primary_evidence_ref: str | None = None
    derivation: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    action_item: ActionItemDraft | None = None
    risk: RiskDraft | None = None

    @model_validator(mode="after")
    def validate_support_contract(self) -> "ClaimDraft":
        """Reject malformed model output while the Provider can still retry."""
        if self.claim_type == ClaimType.UNKNOWN:
            if (
                self.evidence_refs
                or self.primary_evidence_ref is not None
                or self.derivation is not None
                or self.action_item is not None
                or self.risk is not None
            ):
                raise ValueError("unknown claim cannot have support or derivation")
            return self
        if not self.evidence_refs:
            raise ValueError("non-unknown claim requires evidence references")
        if self.claim_type == ClaimType.EXPLICIT_FACT:
            if (
                self.primary_evidence_ref is not None
                and self.primary_evidence_ref not in self.evidence_refs
            ):
                raise ValueError(
                    "primary evidence reference must belong to claim evidence"
                )
        elif self.primary_evidence_ref is not None:
            raise ValueError("only explicit fact can select primary evidence")
        if self.claim_type == ClaimType.DERIVED_FACT and not self.derivation:
            raise ValueError("derived fact requires a derivation")
        if self.category == ClaimCategory.ACTION_ITEM and self.action_item is None:
            raise ValueError("action_item claim requires action_item fields")
        if self.category != ClaimCategory.ACTION_ITEM and self.action_item is not None:
            raise ValueError("only action_item claim can contain action_item fields")
        if self.category in {ClaimCategory.RISK, ClaimCategory.BLOCKER}:
            if self.risk is None:
                raise ValueError("risk or blocker claim requires risk fields")
        elif self.risk is not None:
            raise ValueError("only risk or blocker claim can contain risk fields")
        return self


class ClaimDraftCollection(BaseModel):
    """Structured provider response for claim building."""

    claims: list[ClaimDraft] = Field(default_factory=list)


class ClaimBuilder:
    """Create the single structured source of truth used by artifacts."""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self._model_calls: list[GenerationResult] = []
        self._claim_text_repair_count = 0

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
        self._claim_text_repair_count = 0
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
            action_items, risks = self._empty_entities_from_claims(claims)
        else:
            drafts = self._build_with_provider(
                goal=goal,
                evidence_store=evidence_store,
                feedback=feedback,
            )
            claims, action_items, risks = self._materialize_drafts(
                drafts,
                evidence_store,
            )

        return ProjectSnapshot(
            project_id=project_id,
            snapshot_id=snapshot_id,
            source_ids=source_ids,
            claims=claims,
            action_items=action_items,
            risks=risks,
        )

    def _build_with_provider(
        self,
        *,
        goal: str,
        evidence_store: EvidenceStore,
        feedback: str | None,
    ) -> list[ClaimDraft]:
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

        return response.value.claims

    def _materialize_drafts(
        self,
        drafts: list[ClaimDraft],
        evidence_store: EvidenceStore,
    ) -> tuple[list[Claim], list[ActionItem], list[Risk]]:
        claims: list[Claim] = []
        action_items: list[ActionItem] = []
        risks: list[Risk] = []
        for index, draft in enumerate(drafts, start=1):
            claim_id = f"C-{index:04d}"
            evidence_refs = list(dict.fromkeys(draft.evidence_refs))
            claim_text = self._canonical_explicit_text(
                draft,
                evidence_store,
            )
            claim = Claim(
                claim_id=claim_id,
                text=claim_text,
                claim_type=draft.claim_type,
                category=draft.category,
                evidence_refs=evidence_refs,
                derivation=draft.derivation,
                confidence=draft.confidence,
            )
            claims.append(claim)
            if draft.action_item is not None:
                action_items.append(
                    ActionItem(
                        action_id=f"A-{len(action_items) + 1:04d}",
                        claim_id=claim_id,
                        description=claim_text,
                        owner=SupportedText(
                            value=draft.action_item.owner,
                            evidence_refs=draft.action_item.owner_evidence_refs,
                        ),
                        due_date_text=SupportedText(
                            value=draft.action_item.due_date_text,
                            evidence_refs=(
                                draft.action_item.due_date_evidence_refs
                            ),
                        ),
                        due_date=self._normalize_absolute_date(
                            draft.action_item.due_date_text
                        ),
                        status=draft.action_item.status,
                        status_evidence_refs=list(
                            dict.fromkeys(draft.action_item.status_evidence_refs)
                        ),
                        source_refs=evidence_refs,
                    )
                )
            if draft.risk is not None:
                risks.append(
                    Risk(
                        risk_id=f"R-{len(risks) + 1:04d}",
                        claim_id=claim_id,
                        description=claim_text,
                        owner=SupportedText(
                            value=draft.risk.owner,
                            evidence_refs=draft.risk.owner_evidence_refs,
                        ),
                        severity=draft.risk.severity,
                        severity_evidence_refs=list(
                            dict.fromkeys(draft.risk.severity_evidence_refs)
                        ),
                        status=draft.risk.status,
                        status_evidence_refs=list(
                            dict.fromkeys(draft.risk.status_evidence_refs)
                        ),
                        mitigation=SupportedText(
                            value=draft.risk.mitigation,
                            evidence_refs=(
                                draft.risk.mitigation_evidence_refs
                            ),
                        ),
                        source_refs=evidence_refs,
                    )
                )
        return claims, action_items, risks

    def _canonical_explicit_text(
        self,
        draft: ClaimDraft,
        evidence_store: EvidenceStore,
    ) -> str:
        """Restore an Evidence list marker without accepting a paraphrase."""
        if draft.claim_type != ClaimType.EXPLICIT_FACT:
            return draft.text
        selected_ref = draft.primary_evidence_ref
        if selected_ref is None and len(set(draft.evidence_refs)) == 1:
            selected_ref = draft.evidence_refs[0]
        if selected_ref is not None:
            evidence = evidence_store.get_by_id(selected_ref)
            if evidence is not None:
                if evidence.quote != draft.text:
                    self._claim_text_repair_count += 1
                return evidence.quote
        normalized = self._without_list_marker(draft.text)
        matches = []
        for evidence_id in dict.fromkeys(draft.evidence_refs):
            evidence = evidence_store.get_by_id(evidence_id)
            if (
                evidence is not None
                and self._without_list_marker(evidence.quote) == normalized
            ):
                matches.append(evidence.quote)
        if len(matches) != 1 or matches[0] == draft.text:
            return draft.text
        self._claim_text_repair_count += 1
        return matches[0]

    @staticmethod
    def _without_list_marker(text: str) -> str:
        return re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", text, count=1)

    @staticmethod
    def _empty_entities_from_claims(
        claims: list[Claim],
    ) -> tuple[list[ActionItem], list[Risk]]:
        action_items = [
            ActionItem(
                action_id=f"A-{index:04d}",
                claim_id=claim.claim_id,
                description=claim.text,
                source_refs=claim.evidence_refs,
            )
            for index, claim in enumerate(
                (
                    claim
                    for claim in claims
                    if claim.category == ClaimCategory.ACTION_ITEM
                ),
                start=1,
            )
        ]
        risks = [
            Risk(
                risk_id=f"R-{index:04d}",
                claim_id=claim.claim_id,
                description=claim.text,
                source_refs=claim.evidence_refs,
            )
            for index, claim in enumerate(
                (
                    claim
                    for claim in claims
                    if claim.category in {ClaimCategory.RISK, ClaimCategory.BLOCKER}
                ),
                start=1,
            )
        ]
        return action_items, risks

    @staticmethod
    def _normalize_absolute_date(value: str | None) -> date | None:
        """Normalize only an explicit ISO date; relative dates remain textual."""
        if value is None:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    def get_model_calls(self) -> list[GenerationResult]:
        """Return physical provider calls made by the latest build."""
        return list(self._model_calls)

    def get_claim_text_repair_count(self) -> int:
        """Return deterministic marker-only repairs made by the latest build."""
        return self._claim_text_repair_count

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
