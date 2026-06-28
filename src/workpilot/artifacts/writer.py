"""Artifact writer — writes run outputs to disk."""

import json
from pathlib import Path
from typing import Any


class ArtifactWriter:
    """Writes artifacts to the output directory."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, filename: str, content: str | dict) -> Path:
        """Write a single artifact. Handles str and dict (as JSON)."""
        path = self.output_dir / filename
        if isinstance(content, dict):
            path.write_text(
                json.dumps(content, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def write_json(self, filename: str, data: Any) -> Path:
        """Write a JSON artifact."""
        path = self.output_dir / filename
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path
