"""Project evidence-backed business entities from an immutable Claim set."""

from datetime import date

from pydantic import BaseModel, Field, model_validator

from workpilot.analysis.claim_builder import ActionItemDraft, RiskDraft
from workpilot.domain import ActionItem, Claim, ProjectSnapshot, Risk, SupportedText
from workpilot.evidence.store import EvidenceStore
from workpilot.providers.base import GenerationResult, LLMProvider
from workpilot.providers.stub import StubProvider


ENTITY_BUILDER_SYSTEM = """You are a project entity projection engine.
Claims are immutable evidence-backed facts. Project action items and risks from all
claims and evidence without rewriting, adding, or deleting Claims.

Rules:
- Use only supplied Claim IDs and Evidence IDs.
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
"""


class ProjectedActionDraft(ActionItemDraft):
    """Action projection tied to an existing immutable Claim."""

    claim_id: str = Field(min_length=1)


class ProjectedRiskDraft(RiskDraft):
    """Risk projection tied to an existing immutable Claim."""

    claim_id: str = Field(min_length=1)


class EntityProjectionDraft(BaseModel):
    """Flat, provider-facing entity projection response."""

    action_items: list[ProjectedActionDraft] = Field(default_factory=list)
    risks: list[ProjectedRiskDraft] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_claim_projection(self) -> "EntityProjectionDraft":
        for field_name in ("action_items", "risks"):
            claim_ids = [item.claim_id for item in getattr(self, field_name)]
            if len(claim_ids) != len(set(claim_ids)):
                raise ValueError(f"duplicate {field_name} claim projection")
        return self


class EntityBuilder:
    """Build entities independently so Claim generation cannot silently omit them."""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self._model_calls: list[GenerationResult] = []

    def build(
        self,
        *,
        goal: str,
        project_snapshot: ProjectSnapshot,
        evidence_store: EvidenceStore,
    ) -> ProjectSnapshot:
        self._model_calls = []
        if not project_snapshot.claims or isinstance(self.provider, StubProvider):
            return project_snapshot

        response = self.provider.generate_structured(
            prompt=self._prompt(goal, project_snapshot.claims, evidence_store),
            response_model=EntityProjectionDraft,
            system_prompt=ENTITY_BUILDER_SYSTEM,
            temperature=0.0,
        )
        self._model_calls.extend(response.generations)
        if not isinstance(response.value, EntityProjectionDraft):
            raise TypeError("provider returned an unexpected entity projection type")
        return self._materialize(
            project_snapshot,
            response.value,
            evidence_store,
        )

    @staticmethod
    def _materialize(
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
            unknown = set(refs) - evidence_ids
            if unknown:
                raise ValueError("entity references unknown evidence")
            return refs

        actions: list[ActionItem] = []
        for draft in projection.action_items:
            claim = resolve_claim(draft.claim_id)
            refs = source_refs(
                claim,
                draft.owner_evidence_refs,
                draft.due_date_evidence_refs,
                draft.status_evidence_refs,
            )
            actions.append(
                ActionItem(
                    action_id=f"A-{len(actions) + 1:04d}",
                    claim_id=claim.claim_id,
                    description=claim.text,
                    owner=SupportedText(
                        value=draft.owner,
                        evidence_refs=draft.owner_evidence_refs,
                    ),
                    due_date_text=SupportedText(
                        value=draft.due_date_text,
                        evidence_refs=draft.due_date_evidence_refs,
                    ),
                    due_date=EntityBuilder._normalize_absolute_date(
                        draft.due_date_text
                    ),
                    status=draft.status,
                    status_evidence_refs=draft.status_evidence_refs,
                    source_refs=refs,
                )
            )

        risks: list[Risk] = []
        for draft in projection.risks:
            claim = resolve_claim(draft.claim_id)
            refs = source_refs(
                claim,
                draft.owner_evidence_refs,
                draft.severity_evidence_refs,
                draft.status_evidence_refs,
                draft.mitigation_evidence_refs,
            )
            risks.append(
                Risk(
                    risk_id=f"R-{len(risks) + 1:04d}",
                    claim_id=claim.claim_id,
                    description=claim.text,
                    owner=SupportedText(
                        value=draft.owner,
                        evidence_refs=draft.owner_evidence_refs,
                    ),
                    severity=draft.severity,
                    severity_evidence_refs=draft.severity_evidence_refs,
                    status=draft.status,
                    status_evidence_refs=draft.status_evidence_refs,
                    mitigation=SupportedText(
                        value=draft.mitigation,
                        evidence_refs=draft.mitigation_evidence_refs,
                    ),
                    source_refs=refs,
                )
            )
        return snapshot.model_copy(
            update={"action_items": actions, "risks": risks},
            deep=True,
        )

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
            f"Goal: {goal}\n\nClaims:\n{claim_block}\n\n"
            f"Evidence:\n{evidence_block}\n\nReturn the entity projection."
        )

    def get_model_calls(self) -> list[GenerationResult]:
        return list(self._model_calls)
