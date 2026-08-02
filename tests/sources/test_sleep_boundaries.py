"""Tests for the independent-pair sleep boundary estimator."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from lynchpin.sources import sleep_boundaries as sb

TZ2 = timezone(timedelta(hours=2))
SH_START = datetime(2025, 6, 1, 23, 30, tzinfo=TZ2)
SH_END = datetime(2025, 6, 2, 7, 30, tzinfo=TZ2)


def _night_record(*, start_delta=-60.0, end_delta=0.0, relation="independent"):
    return {
        "canonical_id": "sh-night-1",
        "source": "merged",
        "saa_relation": relation,
        "start_local": SH_START.isoformat(),
        "end_local": SH_END.isoformat(),
        "deltas": {
            "start_minutes": start_delta,
            "end_minutes": end_delta,
            "sa_duration_vs_sh_minutes": start_delta * -1 + end_delta,
        },
        "sleep_metrics": {},
    }


def _hr_rows(core_bpm=55.0, disputed_bpm=None):
    """One binned row covering the night core, optionally one covering the
    disputed pre-start hour."""
    rows = []

    def binned(window_start, window_end, bpm):
        bins = []
        cursor = window_start
        while cursor < window_end:
            bins.append(
                {
                    "heart_rate": bpm,
                    "heart_rate_min": bpm - 4,
                    "heart_rate_max": bpm + 4,
                    "start_time": int(cursor.timestamp() * 1000),
                    "end_time": int((cursor + timedelta(minutes=1)).timestamp() * 1000),
                }
            )
            cursor += timedelta(minutes=1)
        return {
            "datauuid": f"hr-{window_start.isoformat()}",
            "start_time": window_start.isoformat(),
            "end_time": window_end.isoformat(),
            "heart_rate": bpm,
            "heart_beat_count": 1,
            "binning_data": bins,
        }

    rows.append(binned(SH_START, SH_START + timedelta(hours=2), core_bpm))
    if disputed_bpm is not None:
        rows.append(
            binned(SH_START - timedelta(minutes=60), SH_START, disputed_bpm)
        )
    return rows


@pytest.fixture()
def env(tmp_path, monkeypatch):
    sleep_file = tmp_path / "sleep_merged.jsonl"
    hr_file = tmp_path / "health_heart_rate.jsonl"
    monkeypatch.setattr(
        "lynchpin.sources.sleep_boundaries.get_config",
        lambda: SimpleNamespace(sleep_jsonl=sleep_file),
    )
    monkeypatch.setattr(sb, "_PROCESSED", tmp_path)

    def write(records, hr_rows):
        sleep_file.write_text("".join(json.dumps(r) + "\n" for r in records))
        hr_file.write_text("".join(json.dumps(r) + "\n" for r in hr_rows))

    return write


def test_mirror_and_nonmerged_records_are_skipped(env):
    env(
        [
            _night_record(relation="mirror"),
            {
                "canonical_id": "x",
                "source": "stage_derived",
                "start_local": SH_START.isoformat(),
                "end_local": SH_END.isoformat(),
                "sleep_metrics": {},
            },
        ],
        [],
    )
    assert sb.boundary_estimates() == []


def test_agreeing_boundaries_keep_samsung(env):
    env([_night_record(start_delta=-4.0, end_delta=3.0)], _hr_rows())
    (est,) = sb.boundary_estimates()
    assert est.start_basis == "agree"
    assert est.end_basis == "agree"
    assert est.best_start == SH_START
    assert est.best_end == SH_END


def test_sleeplike_hr_extends_start_to_saa(env):
    # SAA started 60 min earlier; disputed hour has HR at core level -> asleep
    env(
        [_night_record(start_delta=-60.0, end_delta=0.0)],
        _hr_rows(core_bpm=55.0, disputed_bpm=56.0),
    )
    (est,) = sb.boundary_estimates()
    assert est.start_basis == "hr_asleep"
    assert est.best_start == SH_START - timedelta(minutes=60)
    assert est.start_hr_samples >= sb.MIN_HR_SAMPLES
    assert est.core_hr_avg == 55.0
    assert est.end_basis == "agree"
    assert est.best_duration_min == 540.0


def test_elevated_hr_rejects_saa_start(env):
    env(
        [_night_record(start_delta=-60.0, end_delta=0.0)],
        _hr_rows(core_bpm=55.0, disputed_bpm=80.0),
    )
    (est,) = sb.boundary_estimates()
    assert est.start_basis == "hr_awake"
    assert est.best_start == SH_START  # narrower boundary for a start dispute


def test_no_evidence_falls_back_to_weighted_compromise(env):
    env([_night_record(start_delta=-100.0, end_delta=0.0)], _hr_rows())
    (est,) = sb.boundary_estimates()
    assert est.start_basis == "weighted"
    expected = SH_START + timedelta(minutes=-100.0 * sb.SAA_WEIGHT)
    assert est.best_start == expected
    assert est.adjustment_min == pytest.approx(30.0)


def test_end_dispute_uses_wider_later_end_when_asleep(env):
    # SAA ended 45 min later; give sleep-like HR after the samsung end
    records = [_night_record(start_delta=0.0, end_delta=45.0)]
    hr = _hr_rows(core_bpm=55.0)
    # sleep-like bins in the post-end disputed window
    cursor = SH_END
    bins = []
    while cursor < SH_END + timedelta(minutes=45):
        bins.append(
            {
                "heart_rate": 56,
                "start_time": int(cursor.timestamp() * 1000),
                "end_time": int((cursor + timedelta(minutes=1)).timestamp() * 1000),
            }
        )
        cursor += timedelta(minutes=1)
    hr.append(
        {
            "datauuid": "hr-post",
            "start_time": SH_END.isoformat(),
            "end_time": (SH_END + timedelta(minutes=45)).isoformat(),
            "heart_rate": 56.0,
            "binning_data": bins,
        }
    )
    env(records, hr)
    (est,) = sb.boundary_estimates()
    assert est.end_basis == "hr_asleep"
    assert est.best_end == SH_END + timedelta(minutes=45)


def test_unbinned_rows_contribute_midpoint_sample(env):
    records = [_night_record(start_delta=-100.0, end_delta=0.0)]
    hr = []
    # core: 6 discrete unbinned samples at 55 bpm
    for i in range(6):
        t = SH_START + timedelta(minutes=10 * (i + 1))
        hr.append(
            {
                "datauuid": f"hr-{i}",
                "start_time": t.isoformat(),
                "end_time": t.isoformat(),
                "heart_rate": 55.0,
            }
        )
    # disputed window: 5 discrete samples, elevated
    for i in range(5):
        t = SH_START - timedelta(minutes=90 - 15 * i)
        hr.append(
            {
                "datauuid": f"hr-d{i}",
                "start_time": t.isoformat(),
                "end_time": t.isoformat(),
                "heart_rate": 82.0,
            }
        )
    env(records, hr)
    (est,) = sb.boundary_estimates()
    assert est.start_basis == "hr_awake"
    assert est.best_start == SH_START


def test_date_range_filter(env):
    env([_night_record()], _hr_rows())
    assert sb.boundary_estimates(start=date(2025, 6, 3), end=date(2025, 6, 9)) == []
    assert len(sb.boundary_estimates(start=date(2025, 6, 1), end=date(2025, 6, 2))) == 1


def test_hr_asleep_extension_is_capped(env):
    # SAA claims sleep began 4h earlier and HR stays sleep-like the whole
    # time: trust it only up to MAX_EXTEND_MINUTES.
    records = [_night_record(start_delta=-240.0, end_delta=0.0)]
    hr = _hr_rows(core_bpm=55.0)
    cursor = SH_START - timedelta(minutes=240)
    bins = []
    while cursor < SH_START:
        bins.append(
            {
                "heart_rate": 56,
                "start_time": int(cursor.timestamp() * 1000),
                "end_time": int((cursor + timedelta(minutes=1)).timestamp() * 1000),
            }
        )
        cursor += timedelta(minutes=1)
    hr.append(
        {
            "datauuid": "hr-pre-long",
            "start_time": (SH_START - timedelta(minutes=240)).isoformat(),
            "end_time": SH_START.isoformat(),
            "heart_rate": 56.0,
            "binning_data": bins,
        }
    )
    env(records, hr)
    (est,) = sb.boundary_estimates()
    assert est.start_basis == "hr_asleep_capped"
    assert est.best_start == SH_START - timedelta(minutes=sb.MAX_EXTEND_MINUTES)


def test_implausible_saa_divergence_is_clamped(env):
    env([_night_record(start_delta=-900.0, end_delta=0.0)], _hr_rows())
    (est,) = sb.boundary_estimates()
    assert est.start_basis == "weighted_capped"
    assert est.best_start == SH_START - timedelta(minutes=sb.MAX_EXTEND_MINUTES)
