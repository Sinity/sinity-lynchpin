"""Tests for sources/sleep.py."""

import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from lynchpin.sources.sleep import (
    SleepEntry,
    SleepMetrics,
    canonical_entries,
    daily_activity,
    entries,
    sleep_architecture,
    sleep_stages,
    sleep_productivity,
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
    # 02:00 local is before the 6 AM logical-day boundary, so logical_date maps
    # it to 2026-03-14 (the previous calendar day / "night of"). Query that date.
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
    result = sleep_stages(start=date(2026, 3, 14), end=date(2026, 3, 14))
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


def test_sleep_productivity_chunks_activitywatch_and_uses_logical_deep_work_day(monkeypatch):
    from lynchpin.sources import sleep as mod

    entries = [
        SimpleNamespace(
            date=date(2026, 3, 1),
            total_minutes=420.0,
            effective_score=80.0,
            quality_label="good",
            is_nap=False,
            source="merged",
            score_estimated=False,
            metrics=None,
            signals=None,
            segments=(SimpleNamespace(
                start=datetime(2026, 3, 1, 23, 0),
                end=datetime(2026, 3, 1, 23, 0) + timedelta(minutes=420),
            ),),
        ),
        SimpleNamespace(
            date=date(2026, 3, 2),
            total_minutes=390.0,
            effective_score=70.0,
            quality_label="fair",
            is_nap=False,
            source="merged",
            score_estimated=False,
            metrics=None,
            signals=None,
            segments=(SimpleNamespace(
                start=datetime(2026, 3, 2, 23, 0),
                end=datetime(2026, 3, 2, 23, 0) + timedelta(minutes=390),
            ),),
        ),
    ]
    active_calls = []

    def fake_active_seconds_by_date(start, end):
        active_calls.append((start, end))
        return {start: 3600.0}

    def fake_deep_work(*, start, end):
        if start.date() == date(2026, 3, 3):
            return (SimpleNamespace(start=datetime(2026, 3, 3, 5), duration_min=10.0),)
        return ()

    monkeypatch.setattr(mod, "entries_in_range", lambda *, start, end, canonical=True: entries)
    monkeypatch.setattr(mod, "_activitywatch_derived_bounds", lambda: (None, None))
    monkeypatch.setattr("lynchpin.sources.activitywatch.active_seconds_by_date", fake_active_seconds_by_date)
    monkeypatch.setattr("lynchpin.sources.activitywatch.deep_work", fake_deep_work)

    rows = sleep_productivity(start=date(2026, 3, 1), end=date(2026, 3, 2), chunk_days=1)

    assert active_calls == [
        (date(2026, 3, 2), date(2026, 3, 3)),
        (date(2026, 3, 3), date(2026, 3, 4)),
    ]
    assert [row.workday_deep_work_min for row in rows] == [10.0, 0]
    assert [row.productivity_vs_baseline for row in rows] == [1.0, 1.0]


def _write_rows(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_entries_hydrate_fusion_fields(tmp_path, monkeypatch):
    sleep_file = tmp_path / "sleep_merged.jsonl"
    _write_rows(sleep_file, [{
        "start_local": "2026-03-15T02:00:00+01:00",
        "end_local": "2026-03-15T10:00:00+01:00",
        "source": "stage_derived",
        "nap_evidence": None,
        "saa_relation": None,
        "sleep_metrics": {
            "sleep_score": None,
            "sleep_duration": 420,
            "proxy_score": 71.5,
        },
        "signals": {
            "hr_avg": 55.2, "hr_min": 47.0, "hr_max": 80.0, "hr_samples": 120,
            "hrv_rmssd": 31.0, "respiratory_rate": 14.2,
            "snoring_seconds": 120.0, "skin_temp_c": 33.1,
        },
    }])
    monkeypatch.setattr("lynchpin.sources.sleep.get_config", lambda: SimpleNamespace(sleep_jsonl=sleep_file))

    (entry,) = list(entries())
    assert entry.metrics.proxy_score == 71.5
    assert entry.avg_score is None
    assert entry.effective_score == 71.5
    assert entry.score_estimated is True
    assert entry.quality_label == "fair"
    assert entry.is_nap is False
    assert entry.signals.hr_avg == 55.2
    assert entry.signals.respiratory_rate == 14.2


def test_canonical_entries_prefer_night_over_nap(tmp_path, monkeypatch):
    sleep_file = tmp_path / "sleep_merged.jsonl"
    _write_rows(sleep_file, [
        {
            # merged midday nap: highest source priority, but a nap
            "start_local": "2026-03-15T14:00:00+01:00",
            "end_local": "2026-03-15T14:40:00+01:00",
            "source": "merged",
            "nap_evidence": "vitality_nap",
            "sleep_metrics": {"sleep_duration": 40},
        },
        {
            # the actual night: lower priority tier, must win the date
            "start_local": "2026-03-15T23:00:00+01:00",
            "end_local": "2026-03-16T07:00:00+01:00",
            "source": "stage_derived",
            "nap_evidence": None,
            "sleep_metrics": {"sleep_duration": 430},
        },
    ])
    monkeypatch.setattr("lynchpin.sources.sleep.get_config", lambda: SimpleNamespace(sleep_jsonl=sleep_file))

    canonical = list(canonical_entries())
    assert len(canonical) == 1
    assert canonical[0].source == "stage_derived"
    assert canonical[0].is_nap is False


def test_daily_activity_populates_signal_fields(tmp_path, monkeypatch):
    sleep_file = tmp_path / "sleep_merged.jsonl"
    _write_rows(sleep_file, [{
        "start_local": "2026-03-15T23:30:00+01:00",
        "end_local": "2026-03-16T07:30:00+01:00",
        "source": "merged",
        "sleep_metrics": {"sleep_score": 82, "sleep_duration": 460},
        "signals": {
            "hr_avg": 54.0, "hr_min": 46.0, "hr_max": 90.0,
            "respiratory_rate": 13.8, "snoring_seconds": 30.0,
            "skin_temp_c": 33.4,
        },
    }])
    monkeypatch.setattr("lynchpin.sources.sleep.get_config", lambda: SimpleNamespace(sleep_jsonl=sleep_file))

    (day,) = daily_activity(start=date(2026, 3, 15), end=date(2026, 3, 16))
    assert day.hr_avg_bpm == 54.0
    assert day.hr_min_bpm == 46.0
    assert day.respiratory_rate == 13.8
    assert day.snoring_seconds == 30.0
    assert day.skin_temp_c == 33.4
    assert day.score == 82.0


def test_daily_activity_uses_composite_not_canonical_minutes(tmp_path, monkeypatch):
    """A short high-priority-source record must not shadow the composite night
    (lynchpin-txz: canonical selection understated nights by ~62 min/night)."""
    sleep_file = tmp_path / "sleep_merged.jsonl"
    _write_rows(sleep_file, [
        {
            # highest source priority but short -> wins canonical selection
            "start_local": "2026-03-15T23:00:00+01:00",
            "end_local": "2026-03-16T00:40:00+01:00",
            "source": "merged",
            "sleep_metrics": {"sleep_score": 82, "sleep_duration": 100},
        },
        {
            # lower priority but covers the real night, overlapping the above
            "start_local": "2026-03-15T23:00:00+01:00",
            "end_local": "2026-03-16T05:00:00+01:00",
            "source": "stage_derived",
            "sleep_metrics": {"sleep_duration": 360},
        },
    ])
    monkeypatch.setattr("lynchpin.sources.sleep.get_config", lambda: SimpleNamespace(sleep_jsonl=sleep_file))

    (day,) = daily_activity(start=date(2026, 3, 15), end=date(2026, 3, 16))
    assert day.total_hours == 6.0  # 360 min composite, not the canonical 100 min
    assert day.score == 82.0  # scalar fields still come from the canonical record


def test_sleep_productivity_uses_composite_not_canonical_minutes(tmp_path, monkeypatch):
    sleep_file = tmp_path / "sleep_merged.jsonl"
    _write_rows(sleep_file, [
        {
            "start_local": "2026-03-15T23:00:00+01:00",
            "end_local": "2026-03-16T00:40:00+01:00",
            "source": "merged",
            "sleep_metrics": {"sleep_duration": 100},
        },
        {
            "start_local": "2026-03-15T23:00:00+01:00",
            "end_local": "2026-03-16T05:00:00+01:00",
            "source": "stage_derived",
            "sleep_metrics": {"sleep_duration": 360},
        },
    ])
    monkeypatch.setattr("lynchpin.sources.sleep.get_config", lambda: SimpleNamespace(sleep_jsonl=sleep_file))
    monkeypatch.setattr(
        "lynchpin.sources.activitywatch.active_seconds_by_date", lambda start, end: {}
    )
    monkeypatch.setattr("lynchpin.sources.activitywatch.deep_work", lambda *, start, end: ())
    monkeypatch.setattr(
        "lynchpin.sources.sleep._activitywatch_derived_bounds", lambda: (None, None)
    )

    (row,) = sleep_productivity(start=date(2026, 3, 15), end=date(2026, 3, 15))
    assert row.sleep_hours == 6.0  # 360 min composite, not the canonical 100 min


def test_sleep_stage_movement_flags_contradicted_deep(monkeypatch):
    from lynchpin.sources.sleep import sleep_stage_movement, STAGE_MOVEMENT_CONTRADICTION_LEVEL
    from datetime import datetime, timezone, timedelta

    tz = timezone(timedelta(hours=1))
    t0 = datetime(2026, 3, 15, 2, 0, tzinfo=tz)

    stage_rows = [
        {"start_time": t0.isoformat(), "end_time": (t0 + timedelta(minutes=10)).isoformat(),
         "stage": "deep", "sleep_id": "s1", "duration_minutes": 10},
        {"start_time": (t0 + timedelta(minutes=10)).isoformat(),
         "end_time": (t0 + timedelta(minutes=20)).isoformat(),
         "stage": "deep", "sleep_id": "s1", "duration_minutes": 10},
        {"start_time": (t0 + timedelta(minutes=20)).isoformat(),
         "end_time": (t0 + timedelta(minutes=30)).isoformat(),
         "stage": "awake", "sleep_id": "s1", "duration_minutes": 10},
    ]

    def bins(window_start, minutes, level):
        return [
            {"start_time": int((window_start + timedelta(minutes=i)).timestamp() * 1000),
             "end_time": int((window_start + timedelta(minutes=i + 1)).timestamp() * 1000),
             "activity_level": level}
            for i in range(minutes)
        ]

    movement_rows = [
        # quiet during first deep interval, thrashing during the second
        {"start_time": t0.isoformat(), "end_time": (t0 + timedelta(minutes=20)).isoformat(),
         "binning_data": bins(t0, 10, 0.1) + bins(t0 + timedelta(minutes=10), 10, 6.0)},
    ]

    def fake_load(filename):
        if filename == "health_sleep_stages.jsonl":
            return iter(stage_rows)
        assert filename == "health_movement.jsonl"
        return iter(movement_rows)

    monkeypatch.setattr("lynchpin.sources.sleep._load_jsonl", fake_load)
    checks = sleep_stage_movement(start=date(2026, 3, 14), end=date(2026, 3, 15))
    assert len(checks) == 3
    quiet, thrash, awake = checks
    assert quiet.movement_mean == 0.1 and not quiet.contradicted
    assert thrash.movement_mean == 6.0 and thrash.contradicted
    # awake interval has no bins -> None, never contradicted
    assert awake.movement_mean is None and not awake.contradicted
    assert STAGE_MOVEMENT_CONTRADICTION_LEVEL == 2.0
