"""Tests for the phone_events source."""

import json
from datetime import date

from lynchpin.sources import phone_events as pe


def _write_day(root, day, records):
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"events-{day}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
    return path


def test_readiness_missing_dir(tmp_path):
    r = pe.readiness(tmp_path / "nope")
    assert r.status == "missing"


def test_readiness_counts_across_day_files(tmp_path):
    _write_day(tmp_path, "20260813", [
        {"kind": "boot", "ts": "2026-08-13T00:01:00Z"},
        {"kind": "boot", "ts": "2026-08-13T00:02:00Z"},
    ])
    _write_day(tmp_path, "20260814", [
        {"kind": "boot", "ts": "2026-08-14T00:01:00Z"},
    ])
    r = pe.readiness(tmp_path)
    assert r.status == "ok"
    assert r.row_count == 3


def test_phone_events_parses_extended_and_compact_ts(tmp_path):
    _write_day(tmp_path, "20260814", [
        {"kind": "boot", "ts": "2026-08-14T00:28:43Z"},
        {"kind": "epoch_opened", "epoch": "e1", "ts": "20260814T002038Z"},
        {"kind": "unparseable", "ts": "not-a-timestamp"},
    ])
    events = list(pe.phone_events(root=tmp_path))
    assert len(events) == 2
    kinds = {e.kind for e in events}
    assert kinds == {"boot", "epoch_opened"}
    epoch_event = next(e for e in events if e.kind == "epoch_opened")
    assert epoch_event.date == date(2026, 8, 14)


def test_phone_events_date_filter_bounds_by_parsed_date(tmp_path):
    _write_day(tmp_path, "20260813", [{"kind": "boot", "ts": "2026-08-13T00:01:00Z"}])
    _write_day(tmp_path, "20260814", [{"kind": "boot", "ts": "2026-08-14T00:01:00Z"}])
    events = list(pe.phone_events(root=tmp_path, start=date(2026, 8, 14), end=date(2026, 8, 14)))
    assert len(events) == 1
    assert events[0].date == date(2026, 8, 14)


def test_instrument_runs_extracts_covariates_and_metrics(tmp_path):
    _write_day(tmp_path, "20260814", [
        {
            "kind": "instrument_run",
            "instrument": "pvt",
            "engine": "reaction",
            "epoch": "e1",
            "started_at": "2026-08-14T09:00:00Z",
            "seconds": 150,
            "hour_of_day": 9,
            "energy_state": "good",
            "lux_mean": 120.5,
            "motion_rms": 0.02,
            "headphones": False,
            "scored_on_device": True,
            "trials": 25,
            "median_rt_ms": 280.0,
            "mean_rt_ms": 300.0,
            "lapses": 1,
            "ts": "2026-08-14T09:02:30Z",
        },
    ])
    runs = list(pe.instrument_runs(root=tmp_path))
    assert len(runs) == 1
    run = runs[0]
    assert run.instrument == "pvt"
    assert run.engine == "reaction"
    assert run.hour_of_day == 9
    assert run.lux_mean == 120.5
    assert run.metrics["median_rt_ms"] == 280.0
    assert run.metrics["lapses"] == 1
    assert "kind" not in run.metrics
    assert "started_at" not in run.metrics


def test_daily_instrument_metrics_reaction_uses_median_rt(tmp_path):
    _write_day(tmp_path, "20260814", [
        {
            "kind": "instrument_run", "instrument": "pvt", "engine": "reaction",
            "started_at": "2026-08-14T09:00:00Z", "seconds": 150,
            "median_rt_ms": 260.0, "ts": "2026-08-14T09:02:30Z",
        },
        {
            "kind": "instrument_run", "instrument": "pvt", "engine": "reaction",
            "started_at": "2026-08-14T18:00:00Z", "seconds": 150,
            "median_rt_ms": 300.0, "ts": "2026-08-14T18:02:30Z",
        },
    ])
    days = pe.daily_instrument_metrics(root=tmp_path)
    assert len(days) == 1
    row = days[0]
    assert row.instrument == "pvt"
    assert row.run_count == 2
    assert row.primary_metric_name == "median_rt_ms"
    assert row.primary_metric_value == 280.0


def test_daily_instrument_metrics_uses_persisted_primary_pair(tmp_path):
    _write_day(tmp_path, "20260814", [
        {
            "kind": "instrument_run", "instrument": "stroop", "engine": "forced_choice",
            "started_at": "2026-08-14T09:00:00Z", "seconds": 120,
            "primary_metric": "interference_ms", "primary_value": 42.5,
            "accuracy": 0.9, "median_correct_rt_ms": 700.0,
            "ts": "2026-08-14T09:02:00Z",
        },
    ])
    days = pe.daily_instrument_metrics(root=tmp_path)
    assert days[0].primary_metric_name == "interference_ms"
    assert days[0].primary_metric_value == 42.5


def test_daily_instrument_metrics_covers_torch_preflight_primary(tmp_path):
    _write_day(tmp_path, "20260814", [
        {
            "kind": "instrument_run", "instrument": "torch_cff", "engine": "staircase",
            "started_at": "2026-08-14T09:00:00Z", "seconds": 1,
            "primary_metric": "achievable_hz", "primary_value": 83.25,
            "achievable_hz": 83.25, "ts": "2026-08-14T09:00:01Z",
        },
    ])
    days = pe.daily_instrument_metrics(root=tmp_path)
    assert days[0].primary_metric_name == "achievable_hz"
    assert days[0].primary_metric_value == 83.25


def test_daily_instrument_metrics_mixed_primary_names_do_not_blend(tmp_path):
    # A day straddling the migration: one record predates the persisted pair
    # and falls back to a 0-1 accuracy, two carry the app's own 0-100
    # headline. Averaging all three would report a number in neither unit.
    _write_day(tmp_path, "20260901", [
        {
            "kind": "instrument_run", "instrument": "breath_counting", "engine": "counting",
            "started_at": "2026-09-01T08:00:00Z", "seconds": 120,
            "cycles_correct": 8, "unaware_miscounts": 2,
            "ts": "2026-09-01T08:02:00Z",
        },
        {
            "kind": "instrument_run", "instrument": "breath_counting", "engine": "counting",
            "started_at": "2026-09-01T12:00:00Z", "seconds": 120,
            "primary_metric": "cycles_correct", "primary_value": 80.0,
            "cycles_correct": 8, "unaware_miscounts": 2,
            "ts": "2026-09-01T12:02:00Z",
        },
        {
            "kind": "instrument_run", "instrument": "breath_counting", "engine": "counting",
            "started_at": "2026-09-01T18:00:00Z", "seconds": 120,
            "primary_metric": "cycles_correct", "primary_value": 90.0,
            "cycles_correct": 9, "unaware_miscounts": 1,
            "ts": "2026-09-01T18:02:00Z",
        },
    ])
    days = pe.daily_instrument_metrics(root=tmp_path)
    row = days[0]
    assert row.run_count == 3
    assert row.primary_metric_name == "cycles_correct"
    assert row.primary_metric_value == 85.0


def test_daily_instrument_metrics_null_primary_value_is_not_re_derived(tmp_path):
    # The app names a headline it could not compute; the reader must not go
    # looking for a substitute in the raw fields.
    _write_day(tmp_path, "20260901", [
        {
            "kind": "instrument_run", "instrument": "pvt", "engine": "reaction",
            "started_at": "2026-09-01T08:00:00Z", "seconds": 150,
            "primary_metric": "median_rt_ms", "primary_value": None,
            "interval_sd_ms": 42.0, "ts": "2026-09-01T08:02:30Z",
        },
    ])
    days = pe.daily_instrument_metrics(root=tmp_path)
    assert days[0].run_count == 1
    assert days[0].primary_metric_name is None
    assert days[0].primary_metric_value is None


def test_daily_instrument_metrics_staircase_uses_threshold(tmp_path):
    _write_day(tmp_path, "20260814", [
        {
            "kind": "instrument_run", "instrument": "pitch_jnd", "engine": "staircase",
            "started_at": "2026-08-14T09:00:00Z", "seconds": 180,
            "threshold": 12.5, "ts": "2026-08-14T09:03:00Z",
        },
    ])
    days = pe.daily_instrument_metrics(root=tmp_path)
    assert days[0].primary_metric_name == "threshold"
    assert days[0].primary_metric_value == 12.5


def test_daily_instrument_metrics_forced_choice_uses_accuracy(tmp_path):
    _write_day(tmp_path, "20260814", [
        {
            "kind": "instrument_run", "instrument": "stroop", "engine": "forced_choice",
            "started_at": "2026-08-14T09:00:00Z", "seconds": 120,
            "accuracy": 0.9, "median_correct_rt_ms": 700.0, "ts": "2026-08-14T09:02:00Z",
        },
    ])
    days = pe.daily_instrument_metrics(root=tmp_path)
    assert days[0].primary_metric_name == "accuracy"
    assert days[0].primary_metric_value == 0.9


def test_daily_instrument_metrics_counting_derives_accuracy_from_components(tmp_path):
    _write_day(tmp_path, "20260814", [
        {
            "kind": "instrument_run", "instrument": "breath_counting", "engine": "count",
            "started_at": "2026-08-14T01:20:03Z", "seconds": 300,
            "cycles_correct": 8, "unaware_miscounts": 2, "self_caught_resets": 1,
            "ts": "2026-08-14T01:20:34Z",
        },
    ])
    days = pe.daily_instrument_metrics(root=tmp_path)
    assert days[0].instrument == "breath_counting"
    assert days[0].primary_metric_name == "accuracy"
    assert days[0].primary_metric_value == 0.8


def test_daily_instrument_metrics_counting_zero_total_is_none_not_zero(tmp_path):
    # Live 2026-08-14 event: cycles_correct=0, unaware_miscounts=0 — total is 0,
    # so accuracy is undefined (not fabricated as 0).
    _write_day(tmp_path, "20260814", [
        {
            "kind": "instrument_run", "instrument": "breath_counting", "engine": "count",
            "started_at": "2026-08-14T01:20:03Z", "seconds": 31,
            "cycles_correct": 0, "unaware_miscounts": 0, "self_caught_resets": 1,
            "ts": "2026-08-14T01:20:34Z",
        },
    ])
    days = pe.daily_instrument_metrics(root=tmp_path)
    assert days[0].run_count == 1
    assert days[0].primary_metric_name is None
    assert days[0].primary_metric_value is None


def test_daily_instrument_metrics_absent_day_is_not_a_zero_row(tmp_path):
    _write_day(tmp_path, "20260814", [
        {
            "kind": "instrument_run", "instrument": "pvt", "engine": "reaction",
            "started_at": "2026-08-14T09:00:00Z", "seconds": 150,
            "median_rt_ms": 260.0, "ts": "2026-08-14T09:02:30Z",
        },
    ])
    days = pe.daily_instrument_metrics(root=tmp_path, start=date(2026, 8, 13), end=date(2026, 8, 15))
    dates = {row.date for row in days}
    assert date(2026, 8, 13) not in dates
    assert date(2026, 8, 15) not in dates
    assert date(2026, 8, 14) in dates
