from __future__ import annotations

import json

from lynchpin.ingest._manifest import atomic_write_ndjson, atomic_write_text, write_manifest


def test_atomic_write_text_leaves_no_tmp_sibling(tmp_path) -> None:
    target = tmp_path / "out.json"
    atomic_write_text(target, "hello")

    assert target.read_text(encoding="utf-8") == "hello"
    assert not (tmp_path / ".out.json.tmp").exists()


def test_atomic_write_text_replaces_existing_content_wholesale(tmp_path) -> None:
    target = tmp_path / "out.json"
    target.write_text("stale content that should not survive", encoding="utf-8")

    atomic_write_text(target, "fresh")

    assert target.read_text(encoding="utf-8") == "fresh"


def test_atomic_write_ndjson_writes_one_json_object_per_line(tmp_path) -> None:
    target = tmp_path / "rows.ndjson"
    rows = [{"a": 1}, {"a": 2}, {"a": 3}]

    atomic_write_ndjson(target, rows)

    lines = target.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == rows
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
