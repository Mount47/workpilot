"""Registry of high-level tools available to structured plans."""

from workpilot.planning.models import ToolSpec


class ToolRegistry:
    """Store immutable tool specifications used for plan validation."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Tool {spec.name} is already registered")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def list_all(self) -> list[ToolSpec]:
        return list(self._specs.values())


def create_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for spec in [
        ToolSpec(
            name="workspace.scan",
            description="List sources inside the authorized workspace.",
        ),
        ToolSpec(
            name="evidence.extract",
            description="Extract and validate citable evidence from sources.",
        ),
        ToolSpec(
            name="claims.build",
            description="Build structured project claims from evidence.",
            reentrant=True,
            evidence_required=True,
        ),
        ToolSpec(
            name="artifacts.render",
            description="Render artifacts from the current project snapshot.",
            reentrant=True,
            evidence_required=True,
        ),
        ToolSpec(
            name="verification.run",
            description="Verify claim support and artifact citations.",
            reentrant=True,
            evidence_required=True,
        ),
        ToolSpec(
            name="artifacts.finalize",
            description="Persist final artifacts and verification results.",
        ),
    ]:
        registry.register(spec)
    return registry
