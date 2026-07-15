"""Deterministic field-level support checks for structured project entities."""

from collections.abc import Iterable
from typing import Any

from workpilot.domain import ProjectSnapshot, SupportedText
from workpilot.evidence.store import EvidenceStore
from workpilot.verification.base import Verifier, VerifyResult


class EntityFieldVerifier(Verifier):
    """Verify entity linkage and every populated business field."""

    def __init__(self, evidence_store: EvidenceStore) -> None:
        self.evidence_store = evidence_store

    def verify(
        self,
        project_snapshot: ProjectSnapshot,
        **kwargs: Any,
    ) -> list[VerifyResult]:
        claims = {claim.claim_id: claim for claim in project_snapshot.claims}
        results: list[VerifyResult] = []

        for action in project_snapshot.action_items:
            location = f"action_items.{action.action_id}"
            results.extend(
                self._verify_entity_contract(
                    claim=claims.get(action.claim_id),
                    description=action.description,
                    source_refs=action.source_refs,
                    field_refs=[
                        *action.owner.evidence_refs,
                        *action.due_date_text.evidence_refs,
                        *action.status_evidence_refs,
                    ],
                    location=location,
                )
            )
            results.extend(
                self._verify_supported_text(
                    action.owner,
                    field_name="owner",
                    parent_refs=action.source_refs,
                    location=location,
                )
            )
            results.extend(
                self._verify_supported_text(
                    action.due_date_text,
                    field_name="due_date_text",
                    parent_refs=action.source_refs,
                    location=location,
                )
            )
            results.extend(
                self._verify_enum_field(
                    value=action.status.value,
                    evidence_refs=action.status_evidence_refs,
                    field_name="status",
                    parent_refs=action.source_refs,
                    location=location,
                )
            )

        for risk in project_snapshot.risks:
            location = f"risks.{risk.risk_id}"
            results.extend(
                self._verify_entity_contract(
                    claim=claims.get(risk.claim_id),
                    description=risk.description,
                    source_refs=risk.source_refs,
                    field_refs=[
                        *risk.owner.evidence_refs,
                        *risk.severity_evidence_refs,
                        *risk.status_evidence_refs,
                        *risk.mitigation.evidence_refs,
                    ],
                    location=location,
                )
            )
            results.extend(
                self._verify_supported_text(
                    risk.owner,
                    field_name="owner",
                    parent_refs=risk.source_refs,
                    location=location,
                )
            )
            results.extend(
                self._verify_enum_field(
                    value=risk.severity.value,
                    evidence_refs=risk.severity_evidence_refs,
                    field_name="severity",
                    parent_refs=risk.source_refs,
                    location=location,
                )
            )
            results.extend(
                self._verify_enum_field(
                    value=risk.status.value,
                    evidence_refs=risk.status_evidence_refs,
                    field_name="status",
                    parent_refs=risk.source_refs,
                    location=location,
                )
            )
            results.extend(
                self._verify_supported_text(
                    risk.mitigation,
                    field_name="mitigation",
                    parent_refs=risk.source_refs,
                    location=location,
                )
            )
        return results

    @staticmethod
    def _verify_entity_contract(
        *,
        claim,
        description: str,
        source_refs: list[str],
        field_refs: list[str],
        location: str,
    ) -> list[VerifyResult]:
        if claim is None:
            return [
                VerifyResult(
                    check_id="entity.claim_exists",
                    status="failed",
                    severity="error",
                    artifact="project_snapshot.json",
                    location=location,
                    message="Structured entity references a missing Claim.",
                )
            ]
        expected_refs = {*claim.evidence_refs, *field_refs}
        passed = description == claim.text and set(source_refs) == expected_refs
        return [
            VerifyResult(
                check_id="entity.claim_contract",
                status="passed" if passed else "failed",
                severity="info" if passed else "error",
                artifact="project_snapshot.json",
                location=location,
                message=(
                    "Entity description and source refs match its Claim and fields."
                    if passed
                    else "Entity description or source refs diverge from its Claim/fields."
                ),
            )
        ]

    def _verify_supported_text(
        self,
        field: SupportedText,
        *,
        field_name: str,
        parent_refs: list[str],
        location: str,
    ) -> list[VerifyResult]:
        if field.value is None:
            return [self._unknown_result(field_name, location)]
        return self._verify_value(
            value=field.value,
            evidence_refs=field.evidence_refs,
            field_name=field_name,
            parent_refs=parent_refs,
            location=location,
        )

    def _verify_enum_field(
        self,
        *,
        value: str,
        evidence_refs: list[str],
        field_name: str,
        parent_refs: list[str],
        location: str,
    ) -> list[VerifyResult]:
        if value == "unknown":
            return [self._unknown_result(field_name, location)]
        return self._verify_value(
            value=value,
            evidence_refs=evidence_refs,
            field_name=field_name,
            parent_refs=parent_refs,
            location=location,
            case_insensitive=True,
        )

    def _verify_value(
        self,
        *,
        value: str,
        evidence_refs: list[str],
        field_name: str,
        parent_refs: list[str],
        location: str,
        case_insensitive: bool = False,
    ) -> list[VerifyResult]:
        if not set(evidence_refs).issubset(parent_refs):
            return [
                VerifyResult(
                    check_id="entity.field_parent_support",
                    status="failed",
                    severity="error",
                    artifact="project_snapshot.json",
                    location=f"{location}.{field_name}",
                    message=(
                        f"{field_name} cites Evidence outside the parent Claim."
                    ),
                )
            ]
        evidences, missing = self._resolve(evidence_refs)
        if missing:
            return [
                VerifyResult(
                    check_id="entity.field_evidence_exists",
                    status="failed",
                    severity="error",
                    artifact="project_snapshot.json",
                    location=f"{location}.{field_name}",
                    message=f"{field_name} references missing Evidence.",
                )
            ]
        expected = value.casefold() if case_insensitive else value
        supported = any(
            expected
            in (
                evidence.quote.casefold()
                if case_insensitive
                else evidence.quote
            )
            for evidence in evidences
        )
        return [
            VerifyResult(
                check_id="entity.field_supported",
                status="passed" if supported else "failed",
                severity="info" if supported else "error",
                artifact="project_snapshot.json",
                location=f"{location}.{field_name}",
                message=(
                    f"{field_name} is an exact Evidence substring."
                    if supported
                    else f"{field_name} is not supported by its Evidence text."
                ),
            )
        ]

    def _resolve(self, evidence_refs: Iterable[str]):
        evidences = []
        missing = []
        for evidence_id in evidence_refs:
            evidence = self.evidence_store.get_by_id(evidence_id)
            if evidence is None:
                missing.append(evidence_id)
            else:
                evidences.append(evidence)
        return evidences, missing

    @staticmethod
    def _unknown_result(field_name: str, location: str) -> VerifyResult:
        return VerifyResult(
            check_id="entity.field_unknown",
            status="passed",
            severity="info",
            artifact="project_snapshot.json",
            location=f"{location}.{field_name}",
            message=f"{field_name} is explicitly unknown.",
        )
