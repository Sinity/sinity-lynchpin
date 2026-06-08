from __future__ import annotations

from types import SimpleNamespace


def test_materialize_machine_telemetry_records_input_high_water(monkeypatch, tmp_path):
    from lynchpin.ingest import machine_materialize
    from lynchpin.ingest.machine_materialize import MACHINE_TELEMETRY_SCHEMA_VERSION

    db = tmp_path / "telemetry.sqlite"
    db.write_text("fixture", encoding="utf-8")
    cfg = SimpleNamespace(machine_telemetry_db=db)

    monkeypatch.setattr(machine_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(
        machine_materialize,
        "canonical_machine_table_path",
        lambda table: tmp_path / f"{table}.ndjson",
    )
    monkeypatch.setattr(
        machine_materialize,
        "_materialize_table",
        lambda name, _rows_fn, **_kw: {
            "path": str(tmp_path / f"{name}.ndjson"),
            "row_count": 1,
            "first_date": "2026-01-01",
            "last_date": "2026-01-01",
        },
    )

    manifest = machine_materialize.materialize_machine_telemetry()

    assert manifest["row_count"] == len(manifest["tables"])
    assert manifest["schema_version"] == MACHINE_TELEMETRY_SCHEMA_VERSION
    assert manifest["input_file_count"] == 1
    assert manifest["input_latest_mtime"] is not None
