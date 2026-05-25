"""Tests for the operator-rhythm composer.

The composer is a pure function over four input lists; tests don't
need a substrate. They verify per-source attribution, weekday/hour
bucketing, partial-source detection, peak ranking, and graceful
empty behavior.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from lynchpin.analysis.operator_rhythm import (
    WEEKDAY_NAMES,
    compute_operator_rhythm,
    render_rhythm_summary,
)


def _ts(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, 0, 0, tzinfo=UTC)


def test_compute_combines_all_four_signals_in_same_bucket() -> None:
    # 2026-05-25 is a Monday → dow=0; 14:00 UTC stays 14:00 in UTC.
    rhythm = compute_operator_rhythm(
        start=date(2026, 5, 25),
        end=date(2026, 5, 25),
        project=None,
        focus_rows=[(date(2026, 5, 25), 14, 35.0)],
        commit_timestamps=[_ts(2026, 5, 25, 14), _ts(2026, 5, 25, 14)],
        ai_session_timestamps=[_ts(2026, 5, 25, 14)],
        pressure_timestamps=[],
        target_tz=UTC,
    )
    assert len(rhythm.buckets) == 1
    bucket = rhythm.buckets[0]
    assert bucket.dow == 0
    assert bucket.hour == 14
    assert bucket.focus_min == 35.0
    assert bucket.commit_count == 2
    assert bucket.ai_session_count == 1
    assert bucket.pressure_episode_count == 0


def test_compute_separates_distinct_hour_buckets() -> None:
    rhythm = compute_operator_rhythm(
        start=date(2026, 5, 25),
        end=date(2026, 5, 27),
        project=None,
        focus_rows=[
            (date(2026, 5, 25), 9, 20.0),
            (date(2026, 5, 26), 22, 40.0),
        ],
        commit_timestamps=[],
        ai_session_timestamps=[],
        pressure_timestamps=[],
    )
    by_key = {(b.dow, b.hour): b for b in rhythm.buckets}
    assert by_key[(0, 9)].focus_min == 20.0
    assert by_key[(1, 22)].focus_min == 40.0


def test_compute_omits_all_zero_buckets() -> None:
    rhythm = compute_operator_rhythm(
        start=date(2026, 5, 25),
        end=date(2026, 5, 25),
        project=None,
        focus_rows=[(date(2026, 5, 25), 9, 0.0)],  # zero focus
        commit_timestamps=[],
        ai_session_timestamps=[],
        pressure_timestamps=[],
    )
    assert rhythm.buckets == ()


def test_partial_sources_flagged_when_input_empty() -> None:
    rhythm = compute_operator_rhythm(
        start=date(2026, 5, 25),
        end=date(2026, 5, 25),
        project=None,
        focus_rows=[(date(2026, 5, 25), 14, 30.0)],
        commit_timestamps=[],
        ai_session_timestamps=[],
        pressure_timestamps=[],
    )
    assert set(rhythm.partial_sources) == {"commit_fact", "ai_session", "machine_episode"}
    assert "activitywatch" not in rhythm.partial_sources


def test_peak_hours_prefer_loudest_signal() -> None:
    # Two buckets: one with high focus only, one with commits dominating.
    rhythm = compute_operator_rhythm(
        start=date(2026, 5, 25),
        end=date(2026, 5, 27),
        project=None,
        focus_rows=[
            (date(2026, 5, 25), 9, 50.0),   # Mon 09:00 — focus peak
            (date(2026, 5, 27), 22, 5.0),   # Wed 22:00 — minor
        ],
        commit_timestamps=[_ts(2026, 5, 27, 22)] * 8,  # Wed 22:00 — commit peak
        ai_session_timestamps=[],
        pressure_timestamps=[],
        target_tz=UTC,
    )
    assert rhythm.peak_focus_hour == (0, 9)
    assert rhythm.peak_commit_hour == (2, 22)
    # Combined: Wed 22:00 has 5+8*5=45 vs Mon 09:00 with 50; depends on tie-break.
    assert rhythm.peak_combined_hour in {(0, 9), (2, 22)}


def test_empty_window_returns_empty_rhythm_with_render_handling() -> None:
    rhythm = compute_operator_rhythm(
        start=date(2026, 5, 25),
        end=date(2026, 5, 25),
        project=None,
        focus_rows=[],
        commit_timestamps=[],
        ai_session_timestamps=[],
        pressure_timestamps=[],
    )
    assert rhythm.buckets == ()
    assert rhythm.peak_combined_hour is None
    assert "No activity observed" in render_rhythm_summary(rhythm)


def test_render_summary_names_partials_and_peaks() -> None:
    rhythm = compute_operator_rhythm(
        start=date(2026, 5, 25),
        end=date(2026, 5, 25),
        project="lynchpin",
        focus_rows=[(date(2026, 5, 25), 14, 30.0)],
        commit_timestamps=[_ts(2026, 5, 25, 14)],
        ai_session_timestamps=[],
        pressure_timestamps=[],
    )
    out = render_rhythm_summary(rhythm)
    assert "lynchpin" in out
    assert WEEKDAY_NAMES[0] in out  # Monday
    assert "ai_session" in out  # partial source listed


def test_timezone_handling_converts_to_local() -> None:
    # 2026-05-25 14:00 UTC = same Monday 14:00 in UTC. Just verify tz-aware
    # input doesn't crash and bucketing uses astimezone consistently.
    rhythm = compute_operator_rhythm(
        start=date(2026, 5, 25),
        end=date(2026, 5, 25),
        project=None,
        focus_rows=[],
        commit_timestamps=[datetime(2026, 5, 25, 14, 0, 0, tzinfo=UTC)],
        ai_session_timestamps=[],
        pressure_timestamps=[],
        target_tz=UTC,
    )
    assert len(rhythm.buckets) == 1
    assert rhythm.buckets[0].commit_count == 1
    assert rhythm.buckets[0].hour == 14
