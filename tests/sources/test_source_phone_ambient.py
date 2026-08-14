"""Tests for the phone_ambient source."""

import json
from datetime import date

from lynchpin.sources import phone_ambient as pa


def _write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def test_readiness_missing_file(tmp_path):
    r = pa.readiness(tmp_path / "nope.jsonl")
    assert r.status == "missing"


def test_readiness_ok(tmp_path):
    path = tmp_path / "ambient-levels.jsonl"
    _write(path, [
        {"chunk": "ambient-20260813T000236Z.m4a", "duration_seconds": 300.0,
         "sample_rate": 8000, "mean_db": -50.0, "max_db": -10.0, "captured_nothing": False},
    ])
    r = pa.readiness(path)
    assert r.status == "ok"
    assert r.row_count == 1


def test_ambient_levels_dedupes_repeated_chunk_keeping_last(tmp_path):
    path = tmp_path / "ambient-levels.jsonl"
    _write(path, [
        {"chunk": "ambient-20260812T234714Z.m4a", "duration_seconds": None,
         "sample_rate": 8000, "mean_db": -91.0, "max_db": -91.0, "captured_nothing": True},
        {"chunk": "ambient-20260812T234714Z.m4a", "bytes": 12345, "duration_seconds": 300.0,
         "sample_rate": 48000, "mean_db": -40.0, "max_db": -5.0, "captured_nothing": False},
    ])
    levels = list(pa.ambient_levels(path=path))
    assert len(levels) == 1
    level = levels[0]
    # Later line (the repaired transfer) wins.
    assert level.captured_nothing is False
    assert level.mean_db == -40.0
    assert level.bytes == 12345


def test_ambient_level_date_from_chunk_name(tmp_path):
    # Midday UTC so the local-tz conversion can't cross a calendar day.
    path = tmp_path / "ambient-levels.jsonl"
    _write(path, [
        {"chunk": "ambient-20260813T120000Z.m4a", "duration_seconds": 300.0,
         "sample_rate": 8000, "mean_db": -50.0, "max_db": -10.0, "captured_nothing": False},
    ])
    levels = list(pa.ambient_levels(path=path))
    assert levels[0].date == date(2026, 8, 13)


def test_daily_ambient_aggregates_and_skips_missing_days(tmp_path):
    path = tmp_path / "ambient-levels.jsonl"
    _write(path, [
        {"chunk": "ambient-20260813T000000Z.m4a", "duration_seconds": 300.0,
         "sample_rate": 8000, "mean_db": -40.0, "max_db": -10.0, "captured_nothing": False},
        {"chunk": "ambient-20260813T060000Z.m4a", "duration_seconds": 300.0,
         "sample_rate": 8000, "mean_db": -60.0, "max_db": -20.0, "captured_nothing": True},
    ])
    days = pa.daily_ambient(path=path, start=date(2026, 8, 12), end=date(2026, 8, 14))
    assert len(days) == 1
    row = days[0]
    assert row.date == date(2026, 8, 13)
    assert row.chunk_count == 2
    assert row.mean_db_mean == -50.0
    assert row.max_db_max == -10.0
    assert row.silent_chunk_count == 1
