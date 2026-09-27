from __future__ import annotations

import json
import os
import signal
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


@dataclass(frozen=True)
class _Sample:
    observed_at: datetime
    value: str


def _timestamp(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 1, day, hour, tzinfo=timezone.utc)


def _setup_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, samples: list[_Sample]) -> None:
    from lynchpin.ingest import machine_materialize as module

    monkeypatch.setattr(module, "get_config", lambda: SimpleNamespace(machine_telemetry_db=tmp_path / "absent.sqlite"))
    monkeypatch.setattr(module, "canonical_machine_table_path", lambda table: tmp_path / f"{table}.ndjson")

    def metric_rows(*, start=None, end=None, path=None):
        del path
        return (sample for sample in samples if (start is None or sample.observed_at.date() >= start) and (end is None or sample.observed_at.date() <= end))

    monkeypatch.setattr(module, "metric_samples", metric_rows)
    for name in (
        "gpu_samples", "network_samples", "service_states", "block_device_samples",
        "service_cgroup_io_samples", "service_cgroup_pressure_samples",
        "process_io_delta_samples", "process_memory_samples", "cgroup_memory_samples", "kill_events",
    ):
        monkeypatch.setattr(module, name, lambda **_kwargs: iter(()))


def test_machine_tail_reuses_history_and_replaces_overlap(monkeypatch, tmp_path):
    from lynchpin import materialization
    from lynchpin.ingest.machine_materialize import materialize_machine_telemetry
    from lynchpin.sources.machine import _load_machine_rows
    from lynchpin.sources.machine_package import iter_machine_table_rows

    samples = [
        *(_Sample(_timestamp(1, hour), f"historical-{hour}") for hour in range(1, 20)),
        _Sample(_timestamp(2), "old-overlap"),
    ]
    _setup_source(monkeypatch, tmp_path, samples)
    first = materialize_machine_telemetry(start=date(2026, 1, 1), end=date(2026, 1, 3))
    table = tmp_path / "metric_sample.ndjson"
    historical = tmp_path / first["tables"]["metric_sample"]["partitions"]["2026-01-01"]["path"]
    historical_identity = (historical.stat().st_ino, historical.read_bytes())
    first_bytes = sum(part["size_bytes"] for part in first["tables"]["metric_sample"]["partitions"].values())

    samples[:] = [
        _Sample(_timestamp(2), "new-overlap"),
        _Sample(_timestamp(3), "new-tail"),
    ]
    second = materialize_machine_telemetry(start=date(2026, 1, 2), end=date(2026, 1, 4))
    assert (historical.stat().st_ino, historical.read_bytes()) == historical_identity
    assert second["tables"]["metric_sample"]["partitions"]["2026-01-01"] == first["tables"]["metric_sample"]["partitions"]["2026-01-01"]
    assert [row["value"] for row in iter_machine_table_rows(table, start=None, end=None)] == [
        *(f"historical-{hour}" for hour in range(1, 20)), "new-overlap", "new-tail"
    ]
    monkeypatch.setattr(materialization, "ensure_materialized", lambda *_args, **_kwargs: None)
    assert [row["value"] for row in _load_machine_rows(table, start=date(2026, 1, 2), end=date(2026, 1, 3))] == ["new-overlap", "new-tail"]
    changed = second["tables"]["metric_sample"]["partitions"]
    tail_bytes = changed["2026-01-02"]["size_bytes"] + changed["2026-01-03"]["size_bytes"]
    assert tail_bytes < first_bytes / 3
    assert second["row_count"] == 21


def test_machine_tail_reads_legacy_package_without_rewriting_it(monkeypatch, tmp_path):
    from lynchpin.ingest.machine_materialize import materialize_machine_telemetry
    from lynchpin.sources.machine_package import iter_machine_table_rows

    _setup_source(monkeypatch, tmp_path, [_Sample(_timestamp(2), "new")])
    table = tmp_path / "metric_sample.ndjson"
    table.write_text("".join(json.dumps({"observed_at": _timestamp(day).isoformat(), "value": value}) + "\n" for day, value in ((1, "before"), (2, "old"), (3, "after"))), encoding="utf-8")
    original = (table.stat().st_ino, table.read_bytes())
    (tmp_path / "manifest.json").write_text(json.dumps({"schema_version": 1, "tables": {"metric_sample": {"row_count": 3, "covered_dates": ["2026-01-01", "2026-01-02", "2026-01-03"]}}}), encoding="utf-8")

    report = materialize_machine_telemetry(start=date(2026, 1, 2), end=date(2026, 1, 3))

    assert (table.stat().st_ino, table.read_bytes()) == original
    assert report["tables"]["metric_sample"]["base_path"] == table.name
    assert report["row_count"] is None
    assert [row["value"] for row in iter_machine_table_rows(table, start=None, end=None)] == ["before", "new", "after"]


def test_machine_full_rebuild_keeps_existing_legacy_evidence(monkeypatch, tmp_path):
    from lynchpin.core.errors import MaterializationError
    from lynchpin.ingest.machine_materialize import materialize_machine_telemetry

    _setup_source(monkeypatch, tmp_path, [_Sample(_timestamp(2), "new")])
    legacy = tmp_path / "metric_sample.ndjson"
    legacy.write_text("historical evidence\n", encoding="utf-8")
    with pytest.raises(MaterializationError, match="verified migration"):
        materialize_machine_telemetry()
    assert legacy.read_text(encoding="utf-8") == "historical evidence\n"


def test_machine_sigterm_removes_scratch_and_preserves_serving_manifest(monkeypatch, tmp_path):
    from lynchpin.ingest import machine_materialize as module
    from lynchpin.sources.machine_package import iter_machine_table_rows

    samples = [_Sample(_timestamp(1), "before")]
    _setup_source(monkeypatch, tmp_path, samples)
    module.materialize_machine_telemetry(start=date(2026, 1, 1), end=date(2026, 1, 2))
    manifest = tmp_path / "manifest.json"
    serving = manifest.read_bytes()

    def interrupted(*, start=None, end=None, path=None):
        del start, end, path
        yield _Sample(_timestamp(1), "partial")
        os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(module, "metric_samples", interrupted)
    with pytest.raises(SystemExit, match="143"):
        module.materialize_machine_telemetry(start=date(2026, 1, 1), end=date(2026, 1, 2))
    assert manifest.read_bytes() == serving
    assert [row["value"] for row in iter_machine_table_rows(tmp_path / "metric_sample.ndjson", start=None, end=None)] == ["before"]
    assert not list(tmp_path.rglob("*.tmp"))


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
