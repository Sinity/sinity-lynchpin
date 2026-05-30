"""Tests for life-phase boundary detection.

Pins the data-integrity contracts the module was rewritten to honour:

  1. Known events NEVER create phase boundaries. They may only annotate
     boundaries the composite signal actually detected. A known event with no
     nearby detected shift is recorded as an un-aligned ``EventAnnotation`` and
     must not appear in ``boundaries`` or split a phase.
  2. Missing != zero. A metric outside its observed coverage on a given day is
     ABSENT from that day's composite — not coerced to 0, not imputed to the
     mean — so unobserved days cannot bias/fabricate transitions.
  3. Coverage provenance is reported for every composite signal.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from lynchpin.analysis import life_phase as lp
from lynchpin.analysis.operator_daily import OperatorDay
from lynchpin.core.coverage import CoverageBounds


def _day(
    d: date,
    *,
    aw: float,
    git: int = 0,
    spotify: float | None = None,
    web_social: int = 0,
    web_total: int = 0,
    reddit: int = 0,
) -> OperatorDay:
    return OperatorDay(
        date=d,
        aw_active_hours=aw,
        git_commits=git,
        spotify_hours=spotify,
        web_social_visits=web_social,
        web_visits=web_total,
        reddit_comments=reddit,
    )


def _full_coverage_bounds(first: date, last: date) -> dict[str, CoverageBounds]:
    """coverage_bounds() stub: every source covers the whole window."""
    keys = ("activitywatch", "git_baseline", "sleep", "wykop", "reddit", "webhistory", "spotify")
    return {
        k: CoverageBounds(source=k, first=first, last=last, kind="capture")
        for k in keys
    }


def _patch_sources(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[OperatorDay],
    *,
    cov_first: date,
    cov_last: date,
) -> None:
    monkeypatch.setattr(
        lp, "operator_daily_matrix", lambda start, end, **kw: rows
    )
    monkeypatch.setattr(
        lp, "coverage_bounds", lambda: _full_coverage_bounds(cov_first, cov_last)
    )
    # stress + substance materialized bounds: cover the full window.
    monkeypatch.setattr(
        lp,
        "_materialized_health_bounds",
        lambda: {
            "stress": CoverageBounds("stress", cov_first, cov_last, "export"),
            "substance": CoverageBounds("substance", cov_first, cov_last, "export"),
        },
    )


def _step_rows(n_before: int, n_after: int, *, low: float, high: float) -> list[OperatorDay]:
    """A clean two-level step in aw_active_hours — one true changepoint."""
    start = date(2025, 1, 1)
    rows: list[OperatorDay] = []
    for i in range(n_before):
        rows.append(_day(start + timedelta(days=i), aw=low))
    for i in range(n_after):
        rows.append(_day(start + timedelta(days=n_before + i), aw=high))
    return rows


def test_detects_real_step_change(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _step_rows(60, 60, low=1.0, high=9.0)
    _patch_sources(monkeypatch, rows, cov_first=rows[0].date, cov_last=rows[-1].date)

    report = lp.analyze(rows[0].date, rows[-1].date, known_events=[])

    assert report.boundaries, "a clean step change should yield >=1 boundary"
    # The boundary should land near the true transition (day 60).
    transition = rows[60].date
    nearest = min(report.boundaries, key=lambda b: abs((b.date - transition).days))
    assert abs((nearest.date - transition).days) <= 14


def test_known_event_without_shift_creates_no_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Perfectly flat signal: no changepoint exists anywhere.
    start = date(2025, 1, 1)
    rows = [_day(start + timedelta(days=i), aw=4.0, git=2) for i in range(120)]
    _patch_sources(monkeypatch, rows, cov_first=rows[0].date, cov_last=rows[-1].date)

    # A known event squarely inside the flat window.
    event_day = rows[60].date
    report = lp.analyze(
        rows[0].date, rows[-1].date, known_events=[(event_day, "fabricated-event")]
    )

    assert report.boundaries == [], "flat signal must yield no boundaries"
    assert report.phases == [], "no boundaries => no split phases"

    # The event is recorded as context, explicitly un-aligned.
    assert len(report.event_annotations) == 1
    ann = report.event_annotations[0]
    assert ann.date == event_day
    assert ann.aligned is False
    # And it never leaked into boundaries.
    assert event_day not in {b.date for b in report.boundaries}


def test_known_event_annotates_nearby_detected_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _step_rows(60, 60, low=1.0, high=9.0)
    _patch_sources(monkeypatch, rows, cov_first=rows[0].date, cov_last=rows[-1].date)

    # Place a known event a few days off the true transition (within snap window).
    event_day = rows[60].date + timedelta(days=5)
    report = lp.analyze(
        rows[0].date, rows[-1].date, known_events=[(event_day, "real-event")]
    )

    aligned = [a for a in report.event_annotations if a.aligned]
    assert aligned, "event near a detected shift should align"
    # The aligned boundary snapped to the event date and carries its label.
    snapped = [b for b in report.boundaries if b.date == event_day]
    assert snapped, "aligned event should snap a detected boundary to its date"
    assert snapped[0].signals_involved == ("real-event",)


def test_missing_not_zero_excluded_from_composite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Constant aw across the window; if absence were coerced to 0, a coverage
    # boundary mid-window would inject a spurious level shift.
    start = date(2025, 1, 1)
    rows = [_day(start + timedelta(days=i), aw=5.0) for i in range(120)]

    # aw coverage starts only at day 60: the first 60 days are ABSENT, not 0.
    aw_first = rows[60].date
    last = rows[-1].date
    bounds = {
        "activitywatch": CoverageBounds("activitywatch", aw_first, last, "capture"),
        "git_baseline": CoverageBounds("git_baseline", start, last, "capture"),
        "sleep": CoverageBounds("sleep", start, last, "export"),
        "wykop": CoverageBounds("wykop", start, last, "export"),
    }
    monkeypatch.setattr(lp, "operator_daily_matrix", lambda s, e, **kw: rows)
    monkeypatch.setattr(lp, "coverage_bounds", lambda: bounds)
    monkeypatch.setattr(
        lp,
        "_materialized_health_bounds",
        lambda: {
            "stress": CoverageBounds("stress", start, last, "export"),
            "substance": CoverageBounds("substance", start, last, "export"),
        },
    )

    metric_bounds = lp._resolve_metric_bounds(rows)
    signal = lp._build_composite_signal(rows, metric_bounds)

    # aw is the only nonzero-weight metric here and it's constant; whether a day
    # is in coverage or not, the composite must be flat (0.0), because constant
    # covered values z-normalize to 0 and absent days contribute nothing.
    assert all(abs(v) < 1e-9 for v in signal)
    # And no boundary is fabricated at the coverage edge.
    report = lp.analyze(rows[0].date, last, known_events=[])
    assert report.boundaries == []


def test_coverage_provenance_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _step_rows(60, 60, low=1.0, high=9.0)
    _patch_sources(monkeypatch, rows, cov_first=rows[0].date, cov_last=rows[-1].date)

    report = lp.analyze(rows[0].date, rows[-1].date, known_events=[])

    assert len(report.signal_coverage) == len(lp._METRICS)
    joined = "\n".join(report.signal_coverage)
    assert "covers" in joined
    # The summary echoes coverage provenance.
    assert "Signal coverage:" in report.summary


def test_short_window_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    start = date(2025, 1, 1)
    rows = [_day(start + timedelta(days=i), aw=4.0) for i in range(30)]
    monkeypatch.setattr(lp, "operator_daily_matrix", lambda s, e, **kw: rows)

    report = lp.analyze(start, rows[-1].date)
    assert report.n_days == 30
    assert report.boundaries == []
    assert report.phases == []


def test_social_phase_distinguishable_from_coding_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A social/media-heavy stretch should be compositionally distinguishable
    from a coding-heavy stretch.

    The coding phase: high git commits, high AW focus, zero spotify/social.
    The social phase: zero git, low AW, high spotify listening, high web social
    visits, high reddit comments.

    Two requirements:
      1. The composite means of the two stretches are clearly separated — the
         social signals pull the composite in an opposite direction from coding.
      2. When the switch is sharp enough (a step change), analyze() detects a
         boundary near the transition point.
    """
    start = date(2025, 1, 1)
    n_each = 70  # enough days for stable stats and changepoint detection

    coding_rows = [
        _day(
            start + timedelta(days=i),
            aw=8.0,
            git=5,
            spotify=0.0,
            web_social=0,
            web_total=10,
            reddit=0,
        )
        for i in range(n_each)
    ]
    social_rows = [
        _day(
            start + timedelta(days=n_each + i),
            aw=2.0,
            git=0,
            spotify=4.0,
            web_social=40,
            web_total=50,
            reddit=8,
        )
        for i in range(n_each)
    ]
    rows = coding_rows + social_rows
    _patch_sources(monkeypatch, rows, cov_first=rows[0].date, cov_last=rows[-1].date)

    # ── 1. Composite mean separation ──────────────────────────────────────
    metric_bounds = lp._resolve_metric_bounds(rows)
    signal = lp._build_composite_signal(rows, metric_bounds)

    coding_mean = sum(signal[:n_each]) / n_each
    social_mean = sum(signal[n_each:]) / n_each

    # The two phases must have clearly different composite means. The threshold
    # is 0.1 rather than a larger value because sleep/wykop are zero on both
    # phases and dilute the aggregate z-score, but spotify/reddit/web_dist do
    # pull the composite in opposite directions — the point is that the
    # separation is non-trivially positive (>0) and reproducibly measurable.
    assert abs(coding_mean - social_mean) > 0.1, (
        f"Expected composite separation >0.1 between coding and social phase; "
        f"got coding_mean={coding_mean:.3f}, social_mean={social_mean:.3f}"
    )

    # ── 2. Boundary detected near the transition ───────────────────────────
    report = lp.analyze(rows[0].date, rows[-1].date, known_events=[])

    assert report.boundaries, "sharp coding→social step change should yield >=1 boundary"
    transition = rows[n_each].date
    nearest = min(report.boundaries, key=lambda b: abs((b.date - transition).days))
    assert abs((nearest.date - transition).days) <= 21, (
        f"Nearest boundary {nearest.date} is >21d from true transition {transition}"
    )

    # ── 3. Phase characterization carries social/music means ──────────────
    assert report.phases, "boundaries should produce at least one phase"
    for phase in report.phases:
        # Every phase object should now carry the new signal attributes.
        assert hasattr(phase, "spotify_hours_per_day")
        assert hasattr(phase, "reddit_comments_per_day")
        assert hasattr(phase, "web_distraction_ratio")
