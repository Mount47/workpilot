"""Tests for atomic Artifact writes and durable content metadata."""

import hashlib
from pathlib import Path

import pytest

from workpilot.artifacts.writer import ArtifactWriter
from workpilot.persistence import InMemoryRunRepository, RunRecord
from workpilot.persistence.execution_observer import RepositoryExecutionObserver


def _repository(tmp_path: Path) -> InMemoryRunRepository:
    repository = InMemoryRunRepository()
    repository.create(
        RunRecord(
            run_id="run_artifacts",
            goal="周报",
            workspace="basic_project",
            provider="stub",
            output_dir=tmp_path,
        )
    )
    return repository


def test_writer_replaces_atomically_and_calls_back_after_write(
    tmp_path: Path,
) -> None:
    observations: list[tuple[Path, bytes]] = []
    writer = ArtifactWriter(
        tmp_path,
        on_write=lambda path: observations.append((path, path.read_bytes())),
    )

    writer.write("report.md", "first")
    writer.write("report.md", "second")

    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "second"
    assert [content for _, content in observations] == [b"first", b"second"]
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("filename", ["../escape.json", "/absolute.json"])
def test_writer_rejects_paths_outside_output_dir(
    tmp_path: Path,
    filename: str,
) -> None:
    writer = ArtifactWriter(tmp_path)

    with pytest.raises(ValueError, match="output directory"):
        writer.write_json(filename, {"unsafe": True})


def test_repository_observer_hashes_actual_bytes_and_versions_writes(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    observer = RepositoryExecutionObserver(
        repository,
        run_id="run_artifacts",
        output_dir=tmp_path,
    )
    writer = ArtifactWriter(tmp_path, on_write=observer.artifact_recorded)

    writer.write_json("trace.json", {"sequence": 1})
    first_bytes = (tmp_path / "trace.json").read_bytes()
    writer.write_json("trace.json", {"sequence": 2})
    second_bytes = (tmp_path / "trace.json").read_bytes()

    records = repository.list_artifacts("run_artifacts")
    assert [record.version for record in records] == [1, 2]
    assert [record.relative_path.as_posix() for record in records] == [
        "trace.json",
        "trace.json",
    ]
    assert records[0].sha256 == hashlib.sha256(first_bytes).hexdigest()
    assert records[0].byte_size == len(first_bytes)
    assert records[1].sha256 == hashlib.sha256(second_bytes).hexdigest()
    assert records[1].byte_size == len(second_bytes)
    assert {record.media_type for record in records} == {"application/json"}


def test_repository_observer_rejects_file_outside_run_output(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    repository = _repository(output_dir)
    observer = RepositoryExecutionObserver(
        repository,
        run_id="run_artifacts",
        output_dir=output_dir,
    )

    with pytest.raises(ValueError, match="outside Run output directory"):
        observer.artifact_recorded(outside)
