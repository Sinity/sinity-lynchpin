"""Failure and concurrency contracts for Chisel tree publication."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import shutil
import threading
from pathlib import Path

import pytest

from lynchpin.sources.chisel_publication import (
    PublicationBusyError,
    PublicationValidationError,
    _validate_project,
    publish_candidate,
    staged_publication,
)
from lynchpin.sources.chisel_compact import compact_jsonl


def _project(root: Path, name: str, content: bytes, archive: bytes) -> None:
    project = root / name
    project.mkdir(parents=True)
    artifact = project / "source.xml"
    artifact.write_bytes(content)
    nested = project / "representations" / "guide.json"
    nested.parent.mkdir()
    nested.write_text('{"view":"raw"}\n', encoding="utf-8")
    _write_manifest(project, name)
    (root / f"{name}-all.tar.gz").write_bytes(archive)


def _write_manifest(project: Path, name: str) -> None:
    manifest_path = project / f"{name}-manifest.json"
    artifacts = [
        {"name": path.relative_to(project).as_posix(), "bytes": path.stat().st_size,
         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(project.rglob("*"))
        if path.is_file() and path != manifest_path
    ]
    payload = {"project": name, "artifacts": [
        *artifacts, {"name": manifest_path.name, "bytes": 0, "sha256": None},
    ]}
    _write_payload_manifest(manifest_path, payload)


def _write_payload_manifest(manifest_path: Path, payload: dict) -> None:
    self_row = next(row for row in payload["artifacts"]
                    if row["name"] == manifest_path.name)
    for _ in range(16):
        serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        size = len(serialized.encode("utf-8"))
        if size == self_row["bytes"]:
            break
        self_row["bytes"] = size
    manifest_path.write_text(serialized, encoding="utf-8")


def test_published_candidate_swaps_as_complete_tree_and_archives_old_tar(tmp_path: Path) -> None:
    output = tmp_path / "packages"
    output.mkdir()
    _project(output, "alpha", b"old", b"old-tar")
    (output / "untouched.txt").write_text("keep", encoding="utf-8")

    with staged_publication(output) as candidate:
        shutil.rmtree(candidate / "alpha")
        (candidate / "alpha-all.tar.gz").unlink()
        _project(candidate, "alpha", b"new", b"new-tar")
        (candidate / "alpha-all.tar.gz").write_bytes(b"new-tar")
        publish_candidate(candidate, output, ["alpha"])

    assert (output / "alpha" / "source.xml").read_bytes() == b"new"
    assert (output / "alpha-all.tar.gz").read_bytes() == b"new-tar"
    assert (output / "untouched.txt").read_text(encoding="utf-8") == "keep"
    archives = list((output / "archive").glob("*/*.tar.gz"))
    assert len(archives) == 1
    assert archives[0].read_bytes() == b"old-tar"


def test_unpublished_or_invalid_candidate_keeps_previous_output(tmp_path: Path) -> None:
    output = tmp_path / "packages"
    output.mkdir()
    _project(output, "alpha", b"good", b"old-tar")

    with staged_publication(output) as candidate:
        shutil.rmtree(candidate / "alpha")
        (candidate / "alpha-all.tar.gz").unlink()
        _project(candidate, "alpha", b"good", b"new-tar")
        (candidate / "alpha" / "source.xml").write_bytes(b"bad")
        with pytest.raises(PublicationValidationError, match="size mismatch|hash mismatch"):
            publish_candidate(candidate, output, ["alpha"])

    assert (output / "alpha" / "source.xml").read_bytes() == b"good"
    assert (output / "alpha-all.tar.gz").read_bytes() == b"old-tar"
    assert not (output / "archive").exists()


@pytest.mark.parametrize("failure", ["missing_hash", "undeclared_file"])
def test_manifest_rejects_missing_hash_and_undeclared_nested_files(
    tmp_path: Path, failure: str,
) -> None:
    output = tmp_path / "packages"
    output.mkdir()
    _project(output, "alpha", b"good", b"old-tar")

    with staged_publication(output) as candidate:
        shutil.rmtree(candidate / "alpha")
        (candidate / "alpha-all.tar.gz").unlink()
        _project(candidate, "alpha", b"good", b"new-tar")
        project = candidate / "alpha"
        if failure == "missing_hash":
            payload = json.loads((project / "alpha-manifest.json").read_text())
            nested = next(row for row in payload["artifacts"]
                          if row["name"] == "representations/guide.json")
            nested["sha256"] = None
            _write_payload_manifest(project / "alpha-manifest.json", payload)
        else:
            (project / "representations" / "undeclared.json").write_text("{}")
        with pytest.raises(PublicationValidationError):
            publish_candidate(candidate, output, ["alpha"])

    assert (output / "alpha" / "source.xml").read_bytes() == b"good"


def test_publication_checks_original_bytes_of_compacted_stream(tmp_path: Path) -> None:
    _project(tmp_path, "alpha", b"source", b"archive")
    project = tmp_path / "alpha"
    (project / "report").mkdir()
    (project / "report" / "references.jsonl").write_text('{"name":"target"}\n')
    compact_jsonl(project)
    _write_manifest(project, "alpha")
    _validate_project(tmp_path, "alpha")

    compression_path = project / "dataset-compression.json"
    compression = json.loads(compression_path.read_text())
    compression["streams"][0]["source_sha256"] = "0" * 64
    compression_path.write_text(json.dumps(compression))
    _write_manifest(project, "alpha")
    with pytest.raises(PublicationValidationError, match="compressed stream content mismatch"):
        _validate_project(tmp_path, "alpha")


def test_lock_rejects_concurrent_thread_and_releases_after_candidate_failure(
    tmp_path: Path,
) -> None:
    output = tmp_path / "packages"
    output.mkdir()
    observed: list[BaseException] = []
    with staged_publication(output):
        def attempt() -> None:
            try:
                with staged_publication(output):
                    pass
            except BaseException as exc:
                observed.append(exc)

        thread = threading.Thread(target=attempt)
        thread.start()
        thread.join()
        with pytest.raises(PublicationBusyError):
            with staged_publication(output):
                pass
    assert len(observed) == 1
    assert isinstance(observed[0], PublicationBusyError)
    with staged_publication(output):
        pass


def _process_lock_attempt(output: str, result: multiprocessing.Queue) -> None:
    try:
        with staged_publication(Path(output)):
            pass
    except BaseException as exc:
        result.put(type(exc).__name__)
    else:
        result.put("acquired")


def test_lock_rejects_concurrent_process(tmp_path: Path) -> None:
    output = tmp_path / "packages"
    output.mkdir()
    context = multiprocessing.get_context("fork")
    result = context.Queue()
    with staged_publication(output):
        process = context.Process(target=_process_lock_attempt, args=(str(output), result))
        process.start()
        process.join(timeout=10)
        assert process.exitcode == 0
        assert result.get(timeout=1) == "PublicationBusyError"


def test_failed_candidate_cannot_mutate_published_growth_or_logs(tmp_path):
    from lynchpin.sources.chisel_publication import staged_publication
    root = tmp_path / "published"
    for area in ("growth", "logs"):
        (root / area).mkdir(parents=True)
        (root / area / "record.txt").write_text("published")
    with staged_publication(root) as candidate:
        for area in ("growth", "logs"):
            (candidate / area / "record.txt").write_text("candidate")
            assert (root / area / "record.txt").read_text() == "published"
    assert (root / "growth/record.txt").read_text() == "published"
