"""Project evidence-backed business entities from an immutable Claim set."""

import re
from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from workpilot.analysis.claim_builder import ActionItemDraft, RiskDraft
from workpilot.domain import (
    ActionItem,
    ActionStatus,
    Claim,
    ClaimCategory,
    ProjectSnapshot,
    Risk,
    RiskLevel,
    RiskStatus,
    SupportedText,
)
from workpilot.evidence.store import EvidenceStore
from workpilot.providers.base import GenerationResult, LLMProvider
from workpilot.providers.stub import StubProvider


ENTITY_BUILDER_SYSTEM = """You are a project entity projection engine.
Claims are immutable evidence-backed facts. Project action items and risks from all
claims and evidence without rewriting, adding, or deleting Claims.

Rules:
- Use only supplied candidate Claim IDs and Evidence IDs.
- Every candidate must appear exactly once as selected or excluded. Never create an
  entity from a non-candidate Claim.
- Return every explicit action assignment or obligation, even when its Claim is
  categorized as blocker, decision, or context rather than action_item.
- Separate action records may remain separate when one states an obligation and
  another records a concrete assignment.
- For each field, inspect all Evidence about the same work item before returning null.
- Cross-Evidence enrichment is allowed only for the same project item or subject.
- A field value must be an exact substring of every cited field Evidence quote.
- Do not attach an owner merely because that person owns a related action.
- Return one risk per distinct business risk. When several Claims describe the same
  risk, select the strongest canonical risk or blocker Claim and do not duplicate it.
- A concrete response phrase such as "排查原因" may be mitigation when explicit.
- Field Evidence IDs are added to the entity source refs automatically.
- Unknown enum values must use unknown with no enum Evidence refs.
- Exclude a duplicate only when duplicate_of_claim_id identifies the selected
  canonical entity for the same business item.
"""


class ExclusionReason(str, Enum):
    """Auditable reason why an entity candidate was not selected."""

    NOT_ENTITY = "not_entity"
    DUPLICATE = "duplicate"


class ExcludedCandidateDraft(BaseModel):
    """Explicit disposition for a candidate not projected as an entity."""

    claim_id: str = Field(min_length=1)
    reason: ExclusionReason
    duplicate_of_claim_id: str | None = None

    @model_validator(mode="after")
    def validate_duplicate_target(self) -> "ExcludedCandidateDraft":
        if self.reason == ExclusionReason.DUPLICATE:
            if self.duplicate_of_claim_id is None:
                raise ValueError("duplicate exclusion requires canonical claim")
            if self.duplicate_of_claim_id == self.claim_id:
                raise ValueError("duplicate cannot reference itself")
        elif self.duplicate_of_claim_id is not None:
            raise ValueError(
                "non-duplicate exclusion cannot reference canonical claim"
            )
        return self


class ProjectedActionDraft(ActionItemDraft):
    """Action projection tied to an existing immutable Claim."""

    claim_id: str = Field(min_length=1)


class ProjectedRiskDraft(RiskDraft):
    """Risk projection tied to an existing immutable Claim."""

    claim_id: str = Field(min_length=1)


class EntityProjectionDraft(BaseModel):
    """Flat, provider-facing entity projection response."""

    action_items: list[ProjectedActionDraft] = Field(default_factory=list)
    action_exclusions: list[ExcludedCandidateDraft] = Field(default_factory=list)
    risks: list[ProjectedRiskDraft] = Field(default_factory=list)
    risk_exclusions: list[ExcludedCandidateDraft] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_claim_projection(self) -> "EntityProjectionDraft":
        for field_name in ("action_items", "risks"):
            claim_ids = [item.claim_id for item in getattr(self, field_name)]
            if len(claim_ids) != len(set(claim_ids)):
                raise ValueError(f"duplicate {field_name} claim projection")
        return self


class EntityCandidateSet(BaseModel):
    """Deterministically generated boundary for model entity decisions."""

    action_claim_ids: list[str] = Field(default_factory=list)
    risk_claim_ids: list[str] = Field(default_factory=list)


class EntityBuilder:
    """Build entities independently so Claim generation cannot silently omit them."""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self._model_calls: list[GenerationResult] = []
        self._field_downgrade_count = 0

    def build(
        self,
        *,
        goal: str,
        project_snapshot: ProjectSnapshot,
        evidence_store: EvidenceStore,
    ) -> ProjectSnapshot:
        self._model_calls = []
        self._field_downgrade_count = 0
        if not project_snapshot.claims or isinstance(self.provider, StubProvider):
            return project_snapshot

        candidates = self._build_candidates(project_snapshot.claims)
        response = self.provider.generate_structured(
            prompt=self._prompt(
                goal,
                project_snapshot.claims,
                evidence_store,
                candidates,
            ),
            response_model=EntityProjectionDraft,
            system_prompt=ENTITY_BUILDER_SYSTEM,
            temperature=0.0,
        )
        self._model_calls.extend(response.generations)
        if not isinstance(response.value, EntityProjectionDraft):
            raise TypeError("provider returned an unexpected entity projection type")
        self._validate_candidate_decisions(response.value, candidates)
        return self._materialize(
            project_snapshot,
            response.value,
            evidence_store,
        )

    def _materialize(
        self,
        snapshot: ProjectSnapshot,
        projection: EntityProjectionDraft,
        evidence_store: EvidenceStore,
    ) -> ProjectSnapshot:
        claims = {claim.claim_id: claim for claim in snapshot.claims}
        evidence_ids = {
            evidence.evidence_id for evidence in evidence_store.list_all()
        }

        def resolve_claim(claim_id: str) -> Claim:
            claim = claims.get(claim_id)
            if claim is None:
                raise ValueError(f"entity references unknown claim: {claim_id}")
            return claim

        def source_refs(claim: Claim, *groups: list[str]) -> list[str]:
            refs = list(dict.fromkeys([*claim.evidence_refs, *sum(groups, [])]))
            return refs

        def ensure_known_evidence(*groups: list[str]) -> None:
            unknown = set(sum(groups, [])) - evidence_ids
            if unknown:
                raise ValueError("entity references unknown evidence")

        actions: list[ActionItem] = []
        for draft in projection.action_items:
            claim = resolve_claim(draft.claim_id)
            ensure_known_evidence(
                draft.owner_evidence_refs,
                draft.due_date_evidence_refs,
                draft.status_evidence_refs,
            )
            owner, owner_refs = self._supported_text_or_unknown(
                draft.owner,
                draft.owner_evidence_refs,
                evidence_store,
            )
            due_date_text, due_date_refs = self._supported_text_or_unknown(
                draft.due_date_text,
                draft.due_date_evidence_refs,
                evidence_store,
            )
            status, status_refs = self._supported_enum_or_unknown(
                draft.status,
                draft.status_evidence_refs,
                evidence_store,
                ActionStatus.UNKNOWN,
            )
            refs = source_refs(
                claim,
                owner_refs,
                due_date_refs,
                status_refs,
            )
            actions.append(
                ActionItem(
                    action_id=f"A-{len(actions) + 1:04d}",
                    claim_id=claim.claim_id,
                    description=claim.text,
                    owner=SupportedText(
                        value=owner,
                        evidence_refs=owner_refs,
                    ),
                    due_date_text=SupportedText(
                        value=due_date_text,
                        evidence_refs=due_date_refs,
                    ),
                    due_date=EntityBuilder._normalize_absolute_date(
                        due_date_text
                    ),
                    status=status,
                    status_evidence_refs=status_refs,
                    source_refs=refs,
                )
            )

        risks: list[Risk] = []
        for draft in projection.risks:
            claim = resolve_claim(draft.claim_id)
            ensure_known_evidence(
                draft.owner_evidence_refs,
                draft.severity_evidence_refs,
                draft.status_evidence_refs,
                draft.mitigation_evidence_refs,
            )
            owner, owner_refs = self._supported_text_or_unknown(
                draft.owner,
                draft.owner_evidence_refs,
                evidence_store,
            )
            mitigation, mitigation_refs = self._supported_text_or_unknown(
                draft.mitigation,
                draft.mitigation_evidence_refs,
                evidence_store,
            )
            severity, severity_refs = self._supported_enum_or_unknown(
                draft.severity,
                draft.severity_evidence_refs,
                evidence_store,
                RiskLevel.UNKNOWN,
            )
            status, status_refs = self._supported_enum_or_unknown(
                draft.status,
                draft.status_evidence_refs,
                evidence_store,
                RiskStatus.UNKNOWN,
            )
            refs = source_refs(
                claim,
                owner_refs,
                severity_refs,
                status_refs,
                mitigation_refs,
            )
            risks.append(
                Risk(
                    risk_id=f"R-{len(risks) + 1:04d}",
                    claim_id=claim.claim_id,
                    description=claim.text,
                    owner=SupportedText(
                        value=owner,
                        evidence_refs=owner_refs,
                    ),
                    severity=severity,
                    severity_evidence_refs=severity_refs,
                    status=status,
                    status_evidence_refs=status_refs,
                    mitigation=SupportedText(
                        value=mitigation,
                        evidence_refs=mitigation_refs,
                    ),
                    source_refs=refs,
                )
            )
        return snapshot.model_copy(
            update={"action_items": actions, "risks": risks},
            deep=True,
        )

    @staticmethod
    def _build_candidates(claims: list[Claim]) -> EntityCandidateSet:
        action_ids = []
        risk_ids = []
        for claim in claims:
            if claim.category == ClaimCategory.ACTION_ITEM or (
                claim.category == ClaimCategory.BLOCKER
                and EntityBuilder._contains_action_obligation(claim.text)
            ):
                action_ids.append(claim.claim_id)
            if claim.category in {ClaimCategory.RISK, ClaimCategory.BLOCKER}:
                risk_ids.append(claim.claim_id)
        return EntityCandidateSet(
            action_claim_ids=action_ids,
            risk_claim_ids=risk_ids,
        )

    @staticmethod
    def _contains_action_obligation(text: str) -> bool:
        return bool(
            re.search(
                r"(?:需要|应该|应当|必须|负责|待\S{0,12}(?:处理|完成|确认|输出|排查))"
                r"|\b(?:must|should|needs?\s+to|assigned\s+to)\b",
                text,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _validate_candidate_decisions(
        projection: EntityProjectionDraft,
        candidates: EntityCandidateSet,
    ) -> None:
        EntityBuilder._validate_candidate_type(
            candidate_ids=candidates.action_claim_ids,
            selected_ids=[item.claim_id for item in projection.action_items],
            exclusions=projection.action_exclusions,
            entity_type="action",
        )
        EntityBuilder._validate_candidate_type(
            candidate_ids=candidates.risk_claim_ids,
            selected_ids=[item.claim_id for item in projection.risks],
            exclusions=projection.risk_exclusions,
            entity_type="risk",
        )

    @staticmethod
    def _validate_candidate_type(
        *,
        candidate_ids: list[str],
        selected_ids: list[str],
        exclusions: list[ExcludedCandidateDraft],
        entity_type: str,
    ) -> None:
        excluded_ids = [item.claim_id for item in exclusions]
        decisions = [*selected_ids, *excluded_ids]
        if len(decisions) != len(set(decisions)):
            raise ValueError(f"duplicate {entity_type} candidate decision")
        if set(decisions) != set(candidate_ids):
            raise ValueError(f"incomplete or unknown {entity_type} candidate decision")
        selected = set(selected_ids)
        for exclusion in exclusions:
            if (
                exclusion.reason == ExclusionReason.DUPLICATE
                and exclusion.duplicate_of_claim_id not in selected
            ):
                raise ValueError(
                    f"{entity_type} duplicate target must be a selected candidate"
                )

    def _supported_text_or_unknown(
        self,
        value: str | None,
        refs: list[str],
        evidence_store: EvidenceStore,
    ) -> tuple[str | None, list[str]]:
        if value is None:
            return None, []
        if self._value_supported(value, refs, evidence_store):
            return value, list(dict.fromkeys(refs))
        self._field_downgrade_count += 1
        return None, []

    def _supported_enum_or_unknown(
        self,
        value: Enum,
        refs: list[str],
        evidence_store: EvidenceStore,
        unknown: Enum,
    ) -> tuple[Enum, list[str]]:
        if value == unknown:
            return value, []
        if self._value_supported(
            str(value.value),
            refs,
            evidence_store,
            normalize_enum=True,
        ):
            return value, list(dict.fromkeys(refs))
        self._field_downgrade_count += 1
        return unknown, []

    @staticmethod
    def _value_supported(
        value: str,
        refs: list[str],
        evidence_store: EvidenceStore,
        *,
        normalize_enum: bool = False,
    ) -> bool:
        expected = (
            EntityBuilder._normalize_enum_text(value)
            if normalize_enum
            else value
        )
        for ref in refs:
            evidence = evidence_store.get_by_id(ref)
            if evidence is None:
                continue
            actual = (
                EntityBuilder._normalize_enum_text(evidence.quote)
                if normalize_enum
                else evidence.quote
            )
            if expected in actual:
                return True
        return False

    @staticmethod
    def _normalize_enum_text(value: str) -> str:
        return re.sub(r"[_\-\s]+", " ", value).strip().casefold()

    @staticmethod
    def _normalize_absolute_date(value: str | None) -> date | None:
        if value is None:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _prompt(
        goal: str,
        claims: list[Claim],
        evidence_store: EvidenceStore,
        candidates: EntityCandidateSet,
    ) -> str:
        claim_block = "\n".join(
            f"[{claim.claim_id}] category={claim.category.value} "
            f"evidence={claim.evidence_refs}\n{claim.text}"
            for claim in claims
        )
        evidence_block = "\n".join(
            f"[{evidence.evidence_id}] {evidence.quote}"
            for evidence in evidence_store.list_all()
        )
        return (
            f"Goal: {goal}\n\n"
            f"Action candidate Claim IDs: {candidates.action_claim_ids}\n"
            f"Risk candidate Claim IDs: {candidates.risk_claim_ids}\n\n"
            f"Claims:\n{claim_block}\n\n"
            f"Evidence:\n{evidence_block}\n\nReturn the entity projection."
        )

    def get_model_calls(self) -> list[GenerationResult]:
        return list(self._model_calls)

    def get_field_downgrade_count(self) -> int:
        return self._field_downgrade_count
