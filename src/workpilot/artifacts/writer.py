"""Artifact writer — writes run outputs to disk."""

from collections.abc import Callable
import json
import os
from pathlib import Path
import tempfile
from typing import Any


class ArtifactWriter:
    """Writes artifacts to the output directory."""

    def __init__(
        self,
        output_dir: Path,
        *,
        on_write: Callable[[Path], None] | None = None,
    ) -> None:
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.on_write = on_write

    def _resolve_path(self, filename: str) -> Path:
        relative = Path(filename)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("artifact path must stay within the output directory")
        path = (self.output_dir / relative).resolve()
        if path == self.output_dir or self.output_dir not in path.parents:
            raise ValueError("artifact path must stay within the output directory")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _write_bytes(self, filename: str, data: bytes) -> Path:
        path = self._resolve_path(filename)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        if self.on_write is not None:
            self.on_write(path)
        return path

    def write(self, filename: str, content: str | dict) -> Path:
        """Write a single artifact. Handles str and dict (as JSON)."""
        if isinstance(content, dict):
            rendered = json.dumps(content, ensure_ascii=False, indent=2) + "\n"
        else:
            rendered = content
        return self._write_bytes(filename, rendered.encode("utf-8"))

    def write_json(self, filename: str, data: Any) -> Path:
        """Write a JSON artifact."""
        rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        return self._write_bytes(
            filename,
            rendered.encode("utf-8"),
        )
