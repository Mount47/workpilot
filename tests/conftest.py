"""Test fixtures and shared configuration."""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
BASIC_WORKSPACE = FIXTURES_DIR / "workspaces" / "basic_project"


@pytest.fixture
def basic_workspace() -> Path:
    """Path to the basic_project fixture workspace."""
    return BASIC_WORKSPACE


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    """Temporary output directory for test runs."""
    output = tmp_path / "output"
    output.mkdir()
    return output
