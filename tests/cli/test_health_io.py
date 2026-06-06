from __future__ import annotations

import json

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
