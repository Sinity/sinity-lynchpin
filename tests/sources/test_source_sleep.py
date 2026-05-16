"""Tests for sources/sleep.py."""

import json
from datetime import date
from types import SimpleNamespace

from lynchpin.sources.sleep import (
    SleepEntry,
    SleepMetrics,
    entries,
    sleep_architecture,
    sleep_stages,
    _parse_dt,
    _safe_float,
)


class TestSleepEntry:
    def test_quality_labels(self):
        good = SleepEntry(date=date(2026, 3, 15), total_minutes=480, segments=(), avg_score=85)
        assert good.quality_label == "good"

        fair = SleepEntry(date=date(2026, 3, 15), total_minutes=360, segments=(), avg_score=65)
        assert fair.quality_label == "fair"

        poor = SleepEntry(date=date(2026, 3, 15), total_minutes=240, segments=(), avg_score=40)
        assert poor.quality_label == "poor"

        unknown = SleepEntry(date=date(2026, 3, 15), total_minutes=0, segments=(), avg_score=None)
        assert unknown.quality_label == "unknown"


class TestHelpers:
    def test_parse_dt(self):
        assert _parse_dt("2026-03-15T10:00:00+01:00") is not None
        assert _parse_dt(None) is None
        assert _parse_dt("") is None

    def test_safe_float(self):
        assert _safe_float(3.14) == 3.14
        assert _safe_float("55.0") == 55.0
        assert _safe_float(None) is None


def test_entries_preserve_sleep_metrics(tmp_path, monkeypatch):
    sleep_file = tmp_path / "sleep_merged.jsonl"
    sleep_file.write_text(json.dumps({
        "start_local": "2026-03-15T02:00:00+01:00",
        "end_local": "2026-03-15T10:00:00+01:00",
        "sleep_metrics": {
            "sleep_score": 82,
            "sleep_duration": 480,
            "sleep_efficiency": 91,
            "total_deep_duration": 100,
            "total_rem_duration": 90,
            "deep_pct": 20.8,
            "rem_pct": 18.8,
        },
        "stage_count": 16,
    }) + "\n")
    monkeypatch.setattr("lynchpin.sources.sleep.get_config", lambda: SimpleNamespace(sleep_jsonl=sleep_file))

    result = list(entries())
    assert len(result) == 1
    assert isinstance(result[0].metrics, SleepMetrics)
    assert result[0].metrics.sleep_efficiency == 91.0
    assert result[0].metrics.total_deep_duration == 100.0
    assert result[0].metrics.stage_count == 16


def test_entries_ignore_legacy_metrics_shape(tmp_path, monkeypatch):
    sleep_file = tmp_path / "sleep_merged.jsonl"
    sleep_file.write_text(json.dumps({
        "start": "2026-03-15T02:00:00+01:00",
        "end": "2026-03-15T10:00:00+01:00",
        "metrics": {
            "sleep_score": 82,
            "sleep_duration": 480,
        },
        "sh_datauuid": "legacy",
    }) + "\n")
    monkeypatch.setattr("lynchpin.sources.sleep.get_config", lambda: SimpleNamespace(sleep_jsonl=sleep_file))

    assert list(entries()) == []


def test_sleep_stages_loader(monkeypatch):
    def fake_load(filename):
        assert filename == "health_sleep_stages.jsonl"
        yield {
            "start_time": "2026-03-15T02:00:00+01:00",
            "end_time": "2026-03-15T03:00:00+01:00",
            "stage": "deep",
            "sleep_id": "sleep-1",
            "duration_minutes": 60,
        }

    monkeypatch.setattr("lynchpin.sources.sleep._load_jsonl", fake_load)
    result = sleep_stages(start=date(2026, 3, 15), end=date(2026, 3, 15))
    assert len(result) == 1
    assert result[0].stage == "deep"
    assert result[0].duration_min == 60.0


def test_sleep_architecture_uses_logical_sleep_date(monkeypatch):
    rows = [
        {
            "start_time": "2026-03-15T02:00:00+01:00",
            "end_time": "2026-03-15T03:00:00+01:00",
            "stage": "deep",
            "sleep_id": "sleep-1",
            "duration_minutes": 60,
        },
        {
            "start_time": "2026-03-15T03:00:00+01:00",
            "end_time": "2026-03-15T04:00:00+01:00",
            "stage": "rem",
            "sleep_id": "sleep-1",
            "duration_minutes": 60,
        },
    ]

    monkeypatch.setattr("lynchpin.sources.sleep._load_jsonl", lambda filename: iter(rows))
    result = sleep_architecture(start=date(2026, 3, 14), end=date(2026, 3, 14))
    assert len(result) == 1
    assert result[0].date == date(2026, 3, 14)
    assert result[0].deep_min == 60.0
    assert result[0].rem_min == 60.0
