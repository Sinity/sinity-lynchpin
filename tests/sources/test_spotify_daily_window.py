from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from lynchpin import materialization
from lynchpin.core.errors import MaterializationError, SourceUnavailableError
from lynchpin.core.input_snapshot import input_snapshot
from lynchpin.sources.personal_signals import spotify_daily_window


def test_window_uses_captured_export_manifest(monkeypatch, tmp_path) -> None:
    from lynchpin.ingest.exports_materialize import SPOTIFY_STREAMS_SCHEMA_VERSION

    path = tmp_path / "streams.ndjson"
    path.write_text("{}\n", encoding="utf-8")
    manifest = path.with_suffix(".manifest.json")
    meta = {
        "row_count": 1, "first_date": "2030-01-01", "last_date": "2030-01-03",
        "input_file_count": 0, "input_versions": [],
        "schema_version": SPOTIFY_STREAMS_SCHEMA_VERSION,
    }
    manifest.write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(materialization, "spotify_streams_path", lambda: path)
    monkeypatch.setattr(materialization, "_spotify_input_files", lambda cfg: ())
    with input_snapshot() as snapshot:
        assert spotify_daily_window(start=date(2030, 1, 2), end=date(2030, 1, 9)) == (date(2030, 1, 2), date(2030, 1, 4))
        assert manifest in snapshot.files
        assert path not in snapshot.files
        meta["last_date"] = "2030-01-10"
        manifest.write_text(json.dumps(meta), encoding="utf-8")
        assert spotify_daily_window(start=date(2030, 1, 2), end=date(2030, 1, 9)) == (date(2030, 1, 2), date(2030, 1, 4))
        row = materialization.audit_dataset("spotify")
        assert row.materialized_paths == (path, manifest)
    assert spotify_daily_window(start=date(2030, 1, 2), end=date(2030, 1, 9)) == (date(2030, 1, 2), date(2030, 1, 9))


@pytest.mark.parametrize("status", ["partial", "error"])
def test_unknown_export_coverage_is_not_accepted(monkeypatch, status) -> None:
    monkeypatch.setattr(materialization, "audit_dataset", lambda name: SimpleNamespace(status=status))
    with pytest.raises(MaterializationError):
        spotify_daily_window(start=date(2030, 1, 1), end=date(2030, 1, 5))


def test_daily_hole_inside_export_still_refuses_prepared_read(monkeypatch) -> None:
    row = materialization.MaterializedDataset(
        name="spotify_daily", status="ready", authority="fixture", query_surface="fixture",
        materialized_paths=(), raw_roots=(), row_count=2,
        first_date=date(2030, 1, 1), last_date=date(2030, 1, 3),
        materialization_hint="fixture", reason="fixture",
        covered_dates=(date(2030, 1, 1), date(2030, 1, 3)),
    )
    monkeypatch.setattr(materialization, "_audit_one", lambda name, cfg: row)
    monkeypatch.setattr(materialization, "audit_dataset", lambda name: SimpleNamespace(
        status="ready", first_date=date(2030, 1, 1), last_date=date(2030, 1, 3),
    ))
    window = spotify_daily_window(start=date(2030, 1, 1), end=date(2030, 1, 5))
    assert window == (date(2030, 1, 1), date(2030, 1, 4))
    with materialization.prepared_product_reads():
        result = materialization.ensure_materialized("spotify_daily", window=window)
    assert result.status == "failed"


@pytest.mark.parametrize("retained", [False, True])
def test_absent_export_is_distinct_from_broken_retained_product(monkeypatch, tmp_path, retained) -> None:
    artifact = tmp_path / "streams.manifest.json"
    if retained:
        artifact.write_text("{invalid", encoding="utf-8")
    monkeypatch.setattr(materialization, "audit_dataset", lambda name: SimpleNamespace(
        status="missing", materialized_paths=(artifact,), first_date=None, last_date=None,
    ))
    expected = MaterializationError if retained else SourceUnavailableError
    with pytest.raises(expected):
        spotify_daily_window(start=date(2030, 1, 1), end=date(2030, 1, 5))
