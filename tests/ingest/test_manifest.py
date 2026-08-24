from __future__ import annotations

import json
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pytest

from lynchpin.core.errors import MaterializationError
from lynchpin.ingest._manifest import (
    atomic_write_indexed_ndjson,
    atomic_write_ndjson,
    atomic_write_text,
    guard_incremental_shrinkage,
    replace_indexed_ndjson_tail,
    write_manifest,
)


def test_atomic_write_text_leaves_no_tmp_sibling(tmp_path) -> None:
    target = tmp_path / "out.json"
    atomic_write_text(target, "hello")

    assert target.read_text(encoding="utf-8") == "hello"
    assert not list(tmp_path.glob(".out.json.*.tmp"))


def test_atomic_write_text_replaces_existing_content_wholesale(tmp_path) -> None:
    target = tmp_path / "out.json"
    target.write_text("stale content that should not survive", encoding="utf-8")
    old_inode = target.stat().st_ino

    atomic_write_text(target, "fresh")

    assert target.read_text(encoding="utf-8") == "fresh"
    assert target.stat().st_ino != old_inode
    assert not list(tmp_path.glob(".out.json.*.tmp"))


def test_atomic_write_text_preserves_existing_mode(tmp_path) -> None:
    target = tmp_path / "out.json"
    target.write_text("stale", encoding="utf-8")
    target.chmod(0o640)

    atomic_write_text(target, "fresh")

    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_atomic_write_text_cleans_temp_after_data_fsync_failure(monkeypatch, tmp_path) -> None:
    target = tmp_path / "out.json"
    target.write_text('{"old": true}\n', encoding="utf-8")

    def fail_fsync(_fd: int) -> None:
        raise OSError("simulated data fsync failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="simulated data fsync failure"):
        atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == '{"old": true}\n'
    assert not list(tmp_path.glob(".out.json.*.tmp"))


def test_atomic_write_ndjson_writes_one_json_object_per_line(tmp_path) -> None:
    target = tmp_path / "rows.ndjson"
    rows = [{"a": 1}, {"a": 2}, {"a": 3}]
    target.write_text("stale\n", encoding="utf-8")
    old_inode = target.stat().st_ino

    atomic_write_ndjson(target, rows)

    lines = target.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == rows
    assert target.stat().st_ino != old_inode
    assert not list(tmp_path.glob(".rows.ndjson.*.tmp"))


def test_indexed_ndjson_publication_preserves_mode_and_offsets(tmp_path: Path) -> None:
    target = tmp_path / "rows.ndjson"
    target.write_text("stale\n", encoding="utf-8")
    target.chmod(0o640)
    rows = [
        {"date": "2026-08-20", "value": 1},
        {"date": "2026-08-20", "value": 2},
        {"date": "2026-08-21", "value": 3},
    ]

    offsets = atomic_write_indexed_ndjson(
        target,
        rows,
        date_getter=lambda row: date.fromisoformat(row["date"]),
    )

    encoded = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
    assert offsets == {
        "2026-08-20": 0,
        "2026-08-21": len(encoded[0].encode()) + len(encoded[1].encode()) + 2,
    }
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert not list(tmp_path.glob(".rows.ndjson.*.tmp"))


def test_indexed_tail_replacement_is_atomic_and_preserves_prefix(tmp_path: Path) -> None:
    target = tmp_path / "rows.ndjson"
    original = [
        {"date": "2026-08-20", "value": 1},
        {"date": "2026-08-21", "value": 2},
        {"date": "2026-08-22", "value": 3},
    ]
    offsets = atomic_write_indexed_ndjson(
        target,
        original,
        date_getter=lambda row: date.fromisoformat(row["date"]),
    )
    target.chmod(0o640)

    next_offsets = replace_indexed_ndjson_tail(
        target,
        ({"date": "2026-08-21", "value": 20},),
        start=date(2026, 8, 21),
        date_getter=lambda row: date.fromisoformat(row["date"]),
        offsets=offsets,
    )

    assert [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()] == [
        original[0],
        {"date": "2026-08-21", "value": 20},
    ]
    assert next_offsets["2026-08-20"] == 0
    assert next_offsets["2026-08-21"] == offsets["2026-08-21"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert not list(tmp_path.glob(".rows.ndjson.*.tmp"))


def test_atomic_write_ndjson_never_leaves_partial_content_on_write_failure(tmp_path) -> None:
    """Regression test for lynchpin-mxo.

    A crash mid-write (or two overlapping writers) against a direct
    ``path.open("w")`` truncates the destination before the new content is
    fully written -- a reader can observe a torn file. atomic_write_ndjson
    writes to a sibling temp file first, so a failure partway through never
    touches the real path at all.
    """

    target = tmp_path / "rows.ndjson"
    target.write_text('{"a": "original intact content"}\n', encoding="utf-8")

    class Boom(Exception):
        pass

    def rows_that_explode():
        yield {"a": 1}
        raise Boom("simulated crash mid-write")

    try:
        atomic_write_ndjson(target, rows_that_explode())
    except Boom:
        pass

    # The original file must be untouched, and the failed writer must clean up
    # the temp file that absorbed the partial write.
    assert target.read_text(encoding="utf-8") == '{"a": "original intact content"}\n'
    assert not list(tmp_path.glob(".rows.ndjson.*.tmp"))


@pytest.mark.parametrize("kind", ["text", "ndjson"])
def test_atomic_write_fsyncs_data_before_replace_and_directory_after(
    monkeypatch, tmp_path, kind
) -> None:
    target = tmp_path / f"out.{kind}"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o640)
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def record_fsync(fd: int) -> None:
        file_kind = "directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "data"
        if file_kind == "data":
            assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o640
        events.append(file_kind)
        real_fsync(fd)

    def record_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(os, "fsync", record_fsync)
    monkeypatch.setattr(os, "replace", record_replace)
    if kind == "text":
        atomic_write_text(target, "fresh")
    else:
        atomic_write_ndjson(target, [{"value": 1}])

    assert events == ["data", "replace", "directory"]


def test_atomic_write_reports_parent_fsync_failure_after_publication(monkeypatch, tmp_path) -> None:
    target = tmp_path / "out.json"
    target.write_text("old", encoding="utf-8")
    real_fsync = os.fsync

    def fail_directory_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("simulated parent fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_directory_fsync)
    with pytest.raises(OSError, match="simulated parent fsync failure"):
        atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert not list(tmp_path.glob(".out.json.*.tmp"))


def test_overlapping_writers_get_distinct_same_directory_temps(monkeypatch, tmp_path) -> None:
    target = tmp_path / "rows.ndjson"
    sources: list[Path] = []
    source_lock = threading.Lock()
    replace_barrier = threading.Barrier(2)
    real_replace = os.replace

    def record_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        with source_lock:
            sources.append(Path(source))
        replace_barrier.wait(timeout=5)
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", record_replace)
    rows_by_writer = (
        [{"writer": "a", "index": index} for index in range(100)],
        [{"writer": "b", "index": index} for index in range(100)],
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(atomic_write_ndjson, target, rows) for rows in rows_by_writer]
        for future in futures:
            future.result()

    assert len(sources) == 2
    assert len({source.name for source in sources}) == 2
    published = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert published in rows_by_writer
    assert not list(tmp_path.glob(".rows.ndjson.*.tmp"))


def test_failed_writer_cleans_only_its_temp_during_overlap(tmp_path) -> None:
    target = tmp_path / "rows.ndjson"
    started = threading.Barrier(2)

    def failing_rows():
        yield {"writer": "failed"}
        started.wait(timeout=5)
        raise RuntimeError("simulated writer failure")

    def successful_rows():
        yield {"writer": "successful", "index": 0}
        started.wait(timeout=5)
        yield from ({"writer": "successful", "index": index} for index in range(1, 100))

    with ThreadPoolExecutor(max_workers=2) as executor:
        failed = executor.submit(atomic_write_ndjson, target, failing_rows())
        successful = executor.submit(atomic_write_ndjson, target, successful_rows())
        with pytest.raises(RuntimeError, match="simulated writer failure"):
            failed.result()
        successful.result()

    published = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert published == [{"writer": "successful", "index": index} for index in range(100)]
    assert not list(tmp_path.glob(".rows.ndjson.*.tmp"))


def test_write_manifest_is_atomic_and_adds_materialized_at(tmp_path) -> None:
    target = tmp_path / "x.manifest.json"
    write_manifest(target, {"dataset": "x"})

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["dataset"] == "x"
    assert "materialized_at" in payload
    assert not (tmp_path / ".x.manifest.json.tmp").exists()


def test_guard_incremental_shrinkage_rejects_mass_row_loss(tmp_path) -> None:
    """Regression test for lynchpin-9tw.

    An incremental run keeps all rows outside its window, so total row
    count collapsing (here: 2,468,166 -> 206,098, the real incident
    numbers) means the existing-rows read was torn/truncated. The guard
    must refuse to persist that state.
    """

    manifest = tmp_path / "events.manifest.json"
    manifest.write_text(json.dumps({"row_count": 2_468_166}), encoding="utf-8")

    with pytest.raises(MaterializationError, match="shrink"):
        guard_incremental_shrinkage(manifest, 206_098, dataset="activitywatch.events")


def test_guard_incremental_shrinkage_allows_normal_growth_and_small_shrink(tmp_path) -> None:
    manifest = tmp_path / "events.manifest.json"
    manifest.write_text(json.dumps({"row_count": 10_000}), encoding="utf-8")

    guard_incremental_shrinkage(manifest, 12_000, dataset="x")  # growth
    guard_incremental_shrinkage(manifest, 9_000, dataset="x")  # mild shrink
    guard_incremental_shrinkage(manifest, 5_000, dataset="x")  # exactly at threshold


def test_guard_incremental_shrinkage_skips_small_or_absent_baselines(tmp_path) -> None:
    missing = tmp_path / "missing.manifest.json"
    guard_incremental_shrinkage(missing, 0, dataset="x")

    small = tmp_path / "small.manifest.json"
    small.write_text(json.dumps({"row_count": 500}), encoding="utf-8")
    guard_incremental_shrinkage(small, 0, dataset="x")


def test_guard_incremental_shrinkage_env_override(tmp_path, monkeypatch) -> None:
    manifest = tmp_path / "events.manifest.json"
    manifest.write_text(json.dumps({"row_count": 100_000}), encoding="utf-8")

    monkeypatch.setenv("LYNCHPIN_ALLOW_SHRINK", "1")
    guard_incremental_shrinkage(manifest, 10, dataset="x")
