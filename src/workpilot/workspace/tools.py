"""Workspace tools — safe file access within workspace boundary."""

from pathlib import Path


class WorkspaceTools:
    """Provides controlled file access restricted to workspace_root."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def _resolve_safe(self, relative_path: str) -> Path:
        """Resolve a relative path and verify it's within workspace."""
        resolved = (self.workspace_root / relative_path).resolve()
        if not str(resolved).startswith(str(self.workspace_root)):
            raise PermissionError(
                f"Path traversal detected: {relative_path} "
                f"resolves to {resolved}, outside workspace {self.workspace_root}"
            )
        return resolved

    def list_files(self) -> list[str]:
        """List all files in workspace (relative paths)."""
        files = []
        for path in self.workspace_root.rglob("*"):
            if path.is_file():
                files.append(str(path.relative_to(self.workspace_root)))
        return sorted(files)

    def read_file(self, relative_path: str) -> str:
        """Read a file by relative path. Raises on path traversal."""
        resolved = self._resolve_safe(relative_path)
        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {relative_path}")
        return resolved.read_text(encoding="utf-8")

    def search_text(self, keyword: str) -> list[dict]:
        """Search for keyword across all files. Returns matches with context.

        Returns:
            List of dicts with keys: file, line_number, line_text
        """
        results = []
        for rel_path in self.list_files():
            resolved = self._resolve_safe(rel_path)
            try:
                lines = resolved.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, PermissionError):
                continue
            for i, line in enumerate(lines, start=1):
                if keyword.lower() in line.lower():
                    results.append({
                        "file": rel_path,
                        "line_number": i,
                        "line_text": line.strip(),
                    })
        return results
