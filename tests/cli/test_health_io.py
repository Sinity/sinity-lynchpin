from __future__ import annotations

import json
import hashlib
import pytest

from lynchpin.cli import health_io


def test_write_jsonl_emits_product_manifest(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(health_io, "PROCESSED", tmp_path)

    count = health_io.write_jsonl(
        [
            {"date": "2026-06-02", "value": 1},
            {"start_time": "2026-06-01T23:00:00+02:00", "value": 2},
        ],
        "health_fixture.jsonl",
        "Fixture",
        dry_run=False,
    )

    product = tmp_path / "health_fixture.jsonl"
    manifest = tmp_path / "health_fixture.manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert count == 2
    assert product.read_text(encoding="utf-8").count("\n") == 2
    assert payload["dataset"] == "health.health_fixture"
    assert payload["label"] == "Fixture"
    assert payload["row_count"] == 2
    assert payload["first_date"] == "2026-06-01"
    assert payload["last_date"] == "2026-06-02"
    assert payload["materialized_at"]
    assert payload["sha256"] == hashlib.sha256(product.read_bytes()).hexdigest()


def test_write_jsonl_dry_run_does_not_write_manifest(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(health_io, "PROCESSED", tmp_path)

    count = health_io.write_jsonl(
        [{"date": "2026-06-02"}],
        "health_fixture.jsonl",
        "Fixture",
        dry_run=True,
    )

    assert count == 1
    assert not (tmp_path / "health_fixture.jsonl").exists()
    assert not (tmp_path / "health_fixture.manifest.json").exists()


@pytest.mark.parametrize("failure", ["serialization", "manifest", "pointer"])
def test_failed_publication_keeps_complete_previous_generation(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(health_io, "PROCESSED", tmp_path)
    health_io.write_jsonl([{"date": "2026-06-01", "value": 1}], "health_fixture.jsonl", "Fixture", False)
    data = tmp_path / "health_fixture.jsonl"
    manifest = data.with_suffix(".manifest.json")
    before = data.read_bytes(), manifest.read_bytes(), data.resolve()
    rows = [{"date": "2026-06-02", "value": 2}]
    if failure == "serialization":
        rows.append({"bad": object()})
        error = TypeError
    elif failure == "manifest":
        def reject(*args):
            raise OSError("synthetic metadata write failure")
        monkeypatch.setattr(health_io, "_write_synced", reject)
        error = OSError
    else:
        original = health_io._replace_link
        def interrupt(path, target):
            if path.name == "current":
                raise KeyboardInterrupt()
            original(path, target)
        monkeypatch.setattr(health_io, "_replace_link", interrupt)
        error = KeyboardInterrupt
    with pytest.raises(error):
        health_io.write_jsonl(rows, data.name, "Fixture", False)
    assert (data.read_bytes(), manifest.read_bytes(), data.resolve()) == before


def test_legacy_preimages_and_sleep_companions_publish_together(tmp_path, monkeypatch):
    monkeypatch.setattr(health_io, "PROCESSED", tmp_path)
    primary = tmp_path / "sleep_all_nights.jsonl"
    alias = tmp_path / "sleep_merged.jsonl"
    primary.write_text('{"old":1}\n')
    alias.write_text('{"distinct_old":2}\n')
    health_io.write_jsonl([{"date": "2026-06-01"}], primary.name, "Fixture", False,
                         aliases=(alias.name,), sidecars={"stats.json": '{"count":1}\n'})
    assert primary.read_bytes() == alias.read_bytes()
    assert primary.resolve().parent == alias.resolve().parent == (tmp_path / "stats.json").resolve().parent
    generations = tmp_path / ".sleep_all_nights.generations"
    assert any((p / alias.name).exists() and (p / alias.name).read_text() == '{"distinct_old":2}\n'
               for p in generations.iterdir() if p.is_dir())


def test_open_reader_and_selected_manifest_keep_their_generation(tmp_path, monkeypatch):
    from lynchpin.sources import health_loaders
    monkeypatch.setattr(health_io, "PROCESSED", tmp_path)
    monkeypatch.setattr(health_loaders, "_PROCESSED", tmp_path)
    health_io.write_jsonl([{"value": 1}, {"value": 2}], "health_fixture.jsonl", "Fixture", False)
    manifest = json.loads((tmp_path / "health_fixture.manifest.json").read_text())
    reader = health_loaders.load_jsonl("health_fixture.jsonl")
    assert next(reader)["value"] == 1
    health_io.write_jsonl([{"value": 3}], "health_fixture.jsonl", "Fixture", False)
    assert [row["value"] for row in reader] == [2]
    assert len(open(manifest["path"]).readlines()) == manifest["row_count"] == 2
