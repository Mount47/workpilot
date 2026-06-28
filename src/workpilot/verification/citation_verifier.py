"""Citation verifier — checks that all artifact references point to real evidence."""

import re
from typing import Any

from workpilot.evidence.store import EvidenceStore
from workpilot.verification.base import Verifier, VerifyResult
from workpilot.workspace.tools import WorkspaceTools


class CitationVerifier(Verifier):
    """Verifies that all citations in artifacts reference valid evidence.

    Checks:
    - Every source_ref in artifacts exists in evidence store
    - Evidence source_file exists in workspace
    - Evidence quote can be found in source file content
    - If line range specified, quote appears within those lines
    """

    CITATION_PATTERN = re.compile(r"\[E-\d{4}\]")

    def __init__(
        self,
        evidence_store: EvidenceStore,
        workspace: WorkspaceTools,
    ) -> None:
        self.evidence_store = evidence_store
        self.workspace = workspace

    def verify(self, artifacts: dict[str, Any], **kwargs: Any) -> list[VerifyResult]:
        """Run all citation checks against artifacts."""
        results: list[VerifyResult] = []

        for artifact_name, content in artifacts.items():
            if isinstance(content, str):
                results.extend(self._check_markdown(artifact_name, content))
            elif isinstance(content, dict):
                results.extend(self._check_structured(artifact_name, content))

        return results

    def _check_markdown(self, artifact_name: str, content: str) -> list[VerifyResult]:
        """Check citation references in markdown artifacts."""
        results: list[VerifyResult] = []
        lines = content.splitlines()

        for line_num, line in enumerate(lines, start=1):
            refs = self.CITATION_PATTERN.findall(line)
            for ref in refs:
                evidence_id = ref.strip("[]")
                result = self._verify_evidence_ref(
                    evidence_id=evidence_id,
                    artifact=artifact_name,
                    location=f"line {line_num}",
                )
                results.append(result)

        return results

    def _check_structured(self, artifact_name: str, content: dict) -> list[VerifyResult]:
        """Check source_refs in structured JSON artifacts."""
        results: list[VerifyResult] = []

        source_refs = self._collect_source_refs(content)
        for ref, path in source_refs:
            result = self._verify_evidence_ref(
                evidence_id=ref,
                artifact=artifact_name,
                location=path,
            )
            results.append(result)

        return results

    def _collect_source_refs(self, obj: Any, path: str = "") -> list[tuple[str, str]]:
        """Recursively collect all source_refs from a nested dict/list."""
        refs: list[tuple[str, str]] = []

        if isinstance(obj, dict):
            if "source_refs" in obj and isinstance(obj["source_refs"], list):
                for ref in obj["source_refs"]:
                    refs.append((ref, path + ".source_refs"))
            for key, value in obj.items():
                refs.extend(self._collect_source_refs(value, f"{path}.{key}"))
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                refs.extend(self._collect_source_refs(item, f"{path}[{i}]"))

        return refs

    def _verify_evidence_ref(
        self, evidence_id: str, artifact: str, location: str
    ) -> VerifyResult:
        """Verify a single evidence reference."""
        evidence = self.evidence_store.get_by_id(evidence_id)

        if evidence is None:
            return VerifyResult(
                check_id="citation.exists",
                status="failed",
                severity="error",
                artifact=artifact,
                location=location,
                message=f"Evidence ref {evidence_id} does not exist in store",
            )

        try:
            source_content = self.workspace.read_file(evidence.source_file)
        except FileNotFoundError:
            return VerifyResult(
                check_id="citation.source_exists",
                status="failed",
                severity="error",
                artifact=artifact,
                location=location,
                message=f"Source file '{evidence.source_file}' not found in workspace",
            )
        except PermissionError:
            return VerifyResult(
                check_id="citation.source_exists",
                status="failed",
                severity="error",
                artifact=artifact,
                location=location,
                message=f"Source file '{evidence.source_file}' is outside workspace",
            )

        if evidence.quote not in source_content:
            return VerifyResult(
                check_id="citation.quote_match",
                status="failed",
                severity="error",
                artifact=artifact,
                location=location,
                message=(
                    f"Quote for {evidence_id} not found in "
                    f"{evidence.source_file}: '{evidence.quote[:60]}...'"
                ),
            )

        lines = source_content.splitlines()
        if evidence.start_line >= 1 and evidence.end_line <= len(lines):
            window = "\n".join(lines[evidence.start_line - 1 : evidence.end_line])
            if evidence.quote not in window:
                return VerifyResult(
                    check_id="citation.line_range",
                    status="failed",
                    severity="warning",
                    artifact=artifact,
                    location=location,
                    message=(
                        f"Quote for {evidence_id} exists in file but not within "
                        f"lines {evidence.start_line}-{evidence.end_line}"
                    ),
                )

        return VerifyResult(
            check_id="citation.valid",
            status="passed",
            severity="info",
            artifact=artifact,
            location=location,
            message=f"{evidence_id} verified",
        )
