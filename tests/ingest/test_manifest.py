from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from lynchpin.core.errors import MaterializationError
from lynchpin.ingest._manifest import (
    atomic_write_ndjson,
    atomic_write_text,
    guard_incremental_shrinkage,
    write_manifest,
)


def test_atomic_write_text_leaves_no_tmp_sibling(tmp_path) -> None:
    target = tmp_path / "out.json"
    atomic_write_text(target, "hello")

    assert target.read_text(encoding="utf-8") == "hello"
    assert not (tmp_path / ".out.json.tmp").exists()


def test_atomic_write_text_replaces_existing_content_wholesale(tmp_path) -> None:
    target = tmp_path / "out.json"
    target.write_text("stale content that should not survive", encoding="utf-8")
    old_inode = target.stat().st_ino

    atomic_write_text(target, "fresh")

    assert target.read_text(encoding="utf-8") == "fresh"
    assert target.stat().st_ino != old_inode
    assert not (tmp_path / ".out.json.tmp").exists()


def test_atomic_write_text_preserves_existing_mode(tmp_path) -> None:
    target = tmp_path / "out.json"
    target.write_text("stale", encoding="utf-8")
    target.chmod(0o640)

    atomic_write_text(target, "fresh")

    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_write_manifest_leaves_existing_output_on_text_write_failure(monkeypatch, tmp_path) -> None:
    target = tmp_path / "out.json"
    target.write_text('{"old": true}\n', encoding="utf-8")

    original_write_text = Path.write_text

    def fail_temp_write(path: Path, text: str, *args, **kwargs) -> int:
        if path == tmp_path / ".out.json.tmp":
            raise OSError("simulated crash mid-write")
        return original_write_text(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_temp_write)
    with pytest.raises(OSError, match="simulated crash"):
        write_manifest(target, {"new": True})

    assert target.read_text(encoding="utf-8") == '{"old": true}\n'


def test_atomic_write_ndjson_writes_one_json_object_per_line(tmp_path) -> None:
    target = tmp_path / "rows.ndjson"
    rows = [{"a": 1}, {"a": 2}, {"a": 3}]
    target.write_text("stale\n", encoding="utf-8")
    old_inode = target.stat().st_ino

    atomic_write_ndjson(target, rows)

    lines = target.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == rows
    assert target.stat().st_ino != old_inode
    assert not (tmp_path / ".rows.ndjson.tmp").exists()


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

    # The original file must be untouched -- the temp file that absorbed the
    # partial write was never renamed into place. A leftover ``.tmp`` sibling
    # is acceptable (matches the codebase's existing tmp_output patterns,
    # e.g. machine_materialize.py); what matters is that ``target`` itself
    # was never truncated.
    assert target.read_text(encoding="utf-8") == '{"a": "original intact content"}\n'


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
