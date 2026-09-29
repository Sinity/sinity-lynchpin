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
    # Presence labels mirror what operator_daily writes when it fills a day:
    # capture-backed metrics are presence-gated in the composite (in-bounds
    # days without the label are capture gaps, not zeros).
    present = {"activitywatch"}
    if spotify is not None:
        present.add("spotify")
    if web_total > 0:
        present.add("web")
    if reddit > 0:
        present.add("reddit")
    row = OperatorDay(
        date=d,
        aw_active_hours=aw,
        git_commits=git,
        spotify_hours=spotify,
        web_social_visits=web_social,
        web_visits=web_total,
        reddit_comments=reddit,
    )
    row.sources_present = frozenset(present)
    return row


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
    assert len(report.event_metric_coverage) == 3
    assert all("quiet dates unknown" in row for row in report.event_metric_coverage)


def test_sparse_event_volumes_keep_active_days_and_substances_separate() -> None:
    start = date(2025, 1, 1)
    rows = [_day(start + timedelta(days=i), aw=4.0) for i in range(5)]
    rows[0].sources_present = frozenset({"activitywatch", "substance", "wykop"})
    rows[0].substance_doses = 1
    rows[0].substance_mg_by_name = {"caffeine": 100.0}
    rows[0].wykop_comments = 2
    rows[1].sources_present = frozenset({"activitywatch", "substance", "reddit"})
    rows[1].substance_doses = 1
    rows[1].substance_mg_by_name = {"melatonin": 3.0}
    rows[1].reddit_comments = 5

    phases = lp._build_phases(
        rows,
        [lp.PhaseBoundary(rows[4].date, 0.5, ("fixture",), ())],
        {},
    )

    first = phases[0]
    assert first.n_days == 4
    assert set(first.sparse_event_volume) == {
        "substance:doses", "substance:caffeine", "substance:melatonin", "wykop", "reddit",
    }
    doses = first.sparse_event_volume["substance:doses"]
    assert (doses.total, doses.unit, doses.event_days, doses.calendar_days) == (2, "doses", 2, 4)
    caffeine = first.sparse_event_volume["substance:caffeine"]
    melatonin = first.sparse_event_volume["substance:melatonin"]
    assert (caffeine.total, caffeine.unit, caffeine.event_days, caffeine.calendar_days) == (
        100.0, "mg", 1, 4,
    )
    assert caffeine.per_event_day == 100.0
    assert (melatonin.total, melatonin.unit, melatonin.event_days, melatonin.calendar_days) == (
        3.0, "mg", 1, 4,
    )
    assert first.sparse_event_volume["wykop"].per_event_day == 2.0
    assert first.sparse_event_volume["reddit"].per_event_day == 5.0
    assert first.sparse_event_volume["wykop"].calendar_days == 4
    assert first.sparse_event_volume["reddit"].calendar_days == 4
    assert all("quiet dates are unknown, not zero" in value.interpretation
               for value in first.sparse_event_volume.values())
    assert set(phases[1].sparse_event_volume) == {"substance:doses", "wykop", "reddit"}
    for value in phases[1].sparse_event_volume.values():
        assert (value.total, value.event_days, value.calendar_days, value.per_event_day) == (
            None, 0, 1, None,
        )
    assert not {"substance_mg", "wykop", "reddit"}.intersection(
        metric.name for metric in lp._METRICS
    )


def test_sparse_source_ending_does_not_create_a_zero_rate_boundary(monkeypatch) -> None:
    start = date(2025, 1, 1)
    rows = [_day(start + timedelta(days=i), aw=5.0) for i in range(80)]
    for row in rows[:8]:
        row.sources_present = row.sources_present | {"wykop"}
        row.wykop_comments = 4
    _patch_sources(monkeypatch, rows, cov_first=rows[0].date, cov_last=rows[-1].date)
    bounds = _full_coverage_bounds(rows[0].date, rows[-1].date)
    bounds["wykop"] = CoverageBounds("wykop", rows[0].date, rows[7].date, "export")
    monkeypatch.setattr(lp, "coverage_bounds", lambda: bounds)

    report = lp.analyze(rows[0].date, rows[-1].date, known_events=[])

    assert report.boundaries == []
    wykop_coverage = next(row for row in report.event_metric_coverage if row.startswith("Wykop:"))
    assert rows[7].date.isoformat() in wykop_coverage
    assert "quiet dates unknown" in wykop_coverage


def test_sparse_events_distinguish_no_observation_from_observed_zero() -> None:
    start = date(2025, 1, 1)
    rows = [_day(start + timedelta(days=i), aw=4.0) for i in range(4)]
    rows[1].sources_present = rows[1].sources_present | {"wykop"}
    rows[1].wykop_comments = 0
    phases = lp._build_phases(
        rows, [lp.PhaseBoundary(rows[3].date, 0.5, ("fixture",), ())], {},
    )

    observed = phases[0].sparse_event_volume["wykop"]
    assert (observed.total, observed.event_days, observed.calendar_days, observed.per_event_day) == (
        0.0, 1, 3, 0.0,
    )
    unknown = phases[0].sparse_event_volume["reddit"]
    assert (unknown.total, unknown.event_days, unknown.calendar_days, unknown.per_event_day) == (
        None, 0, 3, None,
    )
    assert phases[1].sparse_event_volume["wykop"].total is None

    summary_report = lp.LifePhaseReport(start, rows[-1].date, len(rows), phases=phases)
    assert "wykop=0comments on 1/3" in lp._summarize_phases(summary_report)


def test_capture_boundary_with_observed_zero_does_not_split_phase(monkeypatch) -> None:
    start = date(2025, 1, 1)
    rows = [OperatorDay(date=start + timedelta(days=i)) for i in range(120)]
    for row in rows[:60]:
        row.aw_active_hours = 0.0
        row.sources_present = frozenset({"activitywatch"})
    _patch_sources(monkeypatch, rows, cov_first=rows[0].date, cov_last=rows[-1].date)

    report = lp.analyze(rows[0].date, rows[-1].date, known_events=[])

    assert report.boundaries == []
    assert report.phases == []


def test_observed_zero_is_rendered_in_phase_summary() -> None:
    start = date(2025, 1, 1)
    phase = lp.LifePhase(start, start, 1, aw_active_hours=0.0, stress_mean=0.0,
                         sleep_hours=0.0)
    report = lp.LifePhaseReport(start, start, 1, phases=[phase])

    summary = lp._summarize_phases(report)

    assert "AW=  0h" in summary
    assert "stress=  0" in summary
    assert "sleep= 0.0h" in summary


def test_life_phase_report_versions_sparse_event_schema(tmp_path, monkeypatch) -> None:
    import json

    start = date(2025, 1, 1)
    report = lp.LifePhaseReport(start, start, 1)
    report.event_metric_coverage = ["fixture: quiet dates unknown"]
    monkeypatch.setattr(lp, "analyze", lambda *_args, **_kwargs: report)

    output = tmp_path / "life-phase.json"
    payload = lp.write_report(output, start=start, end=start)

    assert payload["schema_version"] == 2
    assert payload["methodology"]["sparse_event_sources"].startswith("positive-event dates only")
    assert json.loads(output.read_text())["event_metric_coverage"] == report.event_metric_coverage


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
    # is 0.1 rather than a larger value because sleep is zero on both phases and
    # dilutes the aggregate z-score, but spotify/web_dist still pull the composite — the point is that the
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

    # ── 3. Phase characterization keeps sparse event counts qualified ────
    assert report.phases, "boundaries should produce at least one phase"
    for phase in report.phases:
        # Every phase object should now carry the new signal attributes.
        assert hasattr(phase, "spotify_hours_per_day")
        assert hasattr(phase, "web_distraction_ratio")
    social_phase = next(
        p for p in report.phases if p.sparse_event_volume["reddit"].event_days > 0
    )
    assert social_phase.sparse_event_volume["reddit"].event_days == social_phase.n_days
    assert social_phase.sparse_event_volume["reddit"].calendar_days == social_phase.n_days


def test_in_bounds_capture_gap_is_absent_not_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A day inside AW coverage BOUNDS but without the presence label (capture
    gap / phantom manifest coverage, bead lynchpin-jzb) must be absent from the
    composite — not a fabricated zero-activity day.

    Measured consequence this pins: with bounds-only gating, phantom 2013 rows
    widened AW bounds so 776/924 days (84%) of a 2024-01..2026-07 window
    entered the composite as zeros (audit 2026-08-03).
    """
    start = date(2025, 1, 1)
    rows: list[OperatorDay] = []
    for i in range(120):
        d = start + timedelta(days=i)
        if i < 60:
            # In-bounds but NOT observed: no presence label, value None.
            row = OperatorDay(date=d)
            row.sources_present = frozenset()
            rows.append(row)
        else:
            rows.append(_day(d, aw=5.0))
    _patch_sources(monkeypatch, rows, cov_first=rows[0].date, cov_last=rows[-1].date)

    metric_bounds = lp._resolve_metric_bounds(rows)
    signal = lp._build_composite_signal(rows, metric_bounds)

    # First 60 days contribute nothing; observed days are constant → z=0.
    # If the gap days were read as aw=0.0, the observed stretch would z-score
    # positive and a fabricated boundary would appear at day 60.
    assert all(abs(v) < 1e-9 for v in signal)
    report = lp.analyze(rows[0].date, rows[-1].date, known_events=[])
    assert report.boundaries == []


def test_signal_leaving_coverage_is_not_a_phase_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails if a composite that loses a signal at its coverage end reports a
    boundary there. ActivityWatch changes level on day 40 and stops being
    captured after day 119; nothing observed changes on day 120."""
    start = date(2025, 1, 1)
    rows: list[OperatorDay] = []
    for i in range(180):
        d = start + timedelta(days=i)
        if i < 120:
            rows.append(_day(d, aw=2.0 if i < 40 else 8.0, spotify=1.0))
        else:
            row = OperatorDay(date=d, spotify_hours=1.0)
            row.sources_present = frozenset({"spotify"})
            rows.append(row)
    capture_end = rows[119].date
    _patch_sources(monkeypatch, rows, cov_first=rows[0].date, cov_last=rows[-1].date)
    bounds = _full_coverage_bounds(rows[0].date, rows[-1].date)
    bounds["activitywatch"] = CoverageBounds("activitywatch", rows[0].date, capture_end, "capture")
    monkeypatch.setattr(lp, "coverage_bounds", lambda: bounds)

    report = lp.analyze(rows[0].date, rows[-1].date, known_events=[])

    assert all(abs((b.date - rows[120].date).days) > 7 for b in report.boundaries), report.boundaries
    assert any(abs((b.date - rows[40].date).days) <= 7 for b in report.boundaries), report.boundaries
