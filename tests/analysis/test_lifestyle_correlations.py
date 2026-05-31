"""Statistical-integrity tests for lifestyle × productivity / physiology analysis.

These pin the correctness contracts that keep ``analyze`` from feeding false
claims into LLM narratives:

1. Multiple-comparisons (Benjamini-Hochberg FDR) correction across the full
   pair × lag test family; q-value + n surfaced per correlation, pure noise
   rejected.
2. A planted same-day / lag-1 association survives FDR and is flagged.
3. Missing ≠ zero — days outside a source's coverage, or lacking its presence
   label, are excluded (never coerced to a fabricated 0), so they cannot enter
   the correlation pairs.
4. Multi-year ``full_history`` widens the window to source coverage.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

import lynchpin.analysis.lifestyle_correlations as lc
from lynchpin.analysis.lifestyle_correlations import FDR_TARGET, MIN_PAIRS, analyze
from lynchpin.analysis.operator_daily import OperatorDay
from lynchpin.core.coverage import CoverageBounds


# ── synthetic-row helpers ──────────────────────────────────────────────────


def _day(
    d: date,
    *,
    present: set[str],
    spotify_hours: float | None = None,
    deep_work_min: float | None = None,
    git_commits: int = 0,
    git_lines_added: int = 0,
    git_lines_deleted: int = 0,
    web_visits: int = 0,
    web_social_visits: int = 0,
    hrv_rmssd: float | None = None,
    stress_mean: float | None = None,
    aw_fragmentation: float | None = None,
) -> OperatorDay:
    """One OperatorDay with explicit ``sources_present`` (missing ≠ zero)."""
    row = OperatorDay(date=d)
    row.spotify_hours = spotify_hours
    row.aw_deep_work_min = deep_work_min
    row.aw_fragmentation = aw_fragmentation
    row.git_commits = git_commits
    row.git_lines_added = git_lines_added
    row.git_lines_deleted = git_lines_deleted
    row.web_visits = web_visits
    row.web_social_visits = web_social_visits
    row.hrv_rmssd = hrv_rmssd
    row.stress_mean = stress_mean
    row.sources_present = frozenset(present)
    return row


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[OperatorDay],
    bounds: dict[str, CoverageBounds],
) -> None:
    """Patch the matrix builder + coverage_bounds used by ``analyze``."""
    monkeypatch.setattr(lc, "operator_daily_matrix", lambda *a, **k: rows)
    import lynchpin.sources.source_observations as so

    monkeypatch.setattr(so, "coverage_bounds", lambda *a, **k: bounds)


def _full_cov(start: date, end: date) -> dict[str, CoverageBounds]:
    """Coverage spanning [start, end] for every source this module uses."""
    return {
        "spotify": CoverageBounds("spotify", start, end, "export"),
        "webhistory": CoverageBounds("webhistory", start, end, "capture"),
        "activitywatch": CoverageBounds("activitywatch", start, end, "capture"),
        "health": CoverageBounds("health", start, end, "export"),
        # git_baseline is intentionally None/None (live source, not clamped).
        "git_baseline": CoverageBounds("git_baseline", None, None, "capture"),
    }


# ── tests ───────────────────────────────────────────────────────────────────


def test_fdr_correction_suppresses_pure_noise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Random lifestyle/productivity data must yield NO FDR-significant pairs."""
    rng = random.Random(1234)
    start = date(2024, 1, 1)
    rows = []
    for i in range(150):
        d = start + timedelta(days=i)
        rows.append(
            _day(
                d,
                present={"spotify", "activitywatch", "git", "web", "health"},
                spotify_hours=rng.uniform(0.0, 6.0),
                deep_work_min=rng.uniform(0.0, 240.0),
                git_commits=rng.randint(0, 12),
                git_lines_added=rng.randint(0, 500),
                git_lines_deleted=rng.randint(0, 500),
                web_visits=rng.randint(10, 400),
                web_social_visits=rng.randint(0, 150),
                hrv_rmssd=rng.uniform(20.0, 70.0),
                stress_mean=rng.uniform(20.0, 80.0),
                aw_fragmentation=rng.uniform(0.0, 1.0),
            )
        )
    end = rows[-1].date
    _patch(monkeypatch, rows, _full_cov(start, end))

    report = analyze(start, end, max_lag=3)

    assert report.lag_correlations, "expected correlations to be computed"
    for c in report.lag_correlations:
        assert 0.0 <= c.p_value <= 1.0
        assert 0.0 <= c.q_value <= 1.0
        assert c.n >= MIN_PAIRS
        assert c.significant == (c.q_value < FDR_TARGET)
    assert not any(c.significant for c in report.lag_correlations)
    assert report.n_tests == len(report.lag_correlations)
    assert "Benjamini-Hochberg" in report.summary
    # All four families should be represented in the test family.
    fams = {c.family for c in report.lag_correlations}
    assert fams == {"music_focus", "web_productivity", "hrv_stress_code", "hrv_attention"}


def test_planted_music_focus_signal_survives_fdr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A strong same-day spotify→deep-work link must survive FDR and be flagged.

    Everything else is noise; only this one pair carries real signal, so the
    FDR correction across the whole pair×lag family must still let it through.
    """
    start = date(2024, 1, 1)
    rng = random.Random(7)
    rows = []
    for i in range(150):
        d = start + timedelta(days=i)
        hours = float(i % 6)  # 0..5 cycling
        deep = 30.0 + 35.0 * hours + rng.uniform(-10.0, 10.0)  # strong same-day link
        rows.append(
            _day(
                d,
                present={"spotify", "activitywatch", "git", "web", "health"},
                spotify_hours=hours,
                deep_work_min=deep,
                git_commits=rng.randint(0, 12),
                git_lines_added=rng.randint(0, 500),
                git_lines_deleted=rng.randint(0, 500),
                web_visits=rng.randint(10, 400),
                web_social_visits=rng.randint(0, 150),
                hrv_rmssd=rng.uniform(20.0, 70.0),
                stress_mean=rng.uniform(20.0, 80.0),
            )
        )
    end = rows[-1].date
    _patch(monkeypatch, rows, _full_cov(start, end))

    report = analyze(start, end, max_lag=3)
    sig = [c for c in report.lag_correlations if c.significant]
    assert sig, "a strong same-day music→focus link should survive FDR"
    assert any(
        c.family == "music_focus"
        and c.predictor == "spotify_hours"
        and c.outcome == "aw_deep_work_min"
        and c.lag_days == 0
        for c in sig
    )
    assert all(c.q_value < FDR_TARGET for c in sig)
    assert "not causation" in report.summary.lower()
    assert "association" in report.summary.lower()


def test_planted_hrv_next_day_code_signal_survives_fdr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lag-1 HRV→next-day-commits link must survive FDR (the #5 headline)."""
    start = date(2024, 1, 1)
    rng = random.Random(11)
    rows = []
    n = 150
    hrv_series = [float(20 + (i % 8) * 6) for i in range(n)]  # 20..62 cycling
    for i in range(n):
        d = start + timedelta(days=i)
        # tomorrow's commits driven by today's HRV.
        prev_hrv = hrv_series[i - 1] if i > 0 else hrv_series[0]
        commits = int(round(0.25 * prev_hrv + rng.uniform(-1.0, 1.0)))
        rows.append(
            _day(
                d,
                present={"spotify", "activitywatch", "git", "web", "health"},
                spotify_hours=rng.uniform(0.0, 6.0),
                deep_work_min=rng.uniform(0.0, 240.0),
                git_commits=max(commits, 0),
                git_lines_added=rng.randint(0, 500),
                git_lines_deleted=rng.randint(0, 500),
                web_visits=rng.randint(10, 400),
                web_social_visits=rng.randint(0, 150),
                hrv_rmssd=hrv_series[i],
                stress_mean=rng.uniform(20.0, 80.0),
            )
        )
    end = rows[-1].date
    _patch(monkeypatch, rows, _full_cov(start, end))

    report = analyze(start, end, max_lag=3)
    sig = [c for c in report.lag_correlations if c.significant]
    assert any(
        c.family == "hrv_stress_code"
        and c.predictor == "hrv_rmssd"
        and c.outcome == "git_commits"
        and c.lag_days == 1
        for c in sig
    ), "lag-1 HRV→next-day commits should survive FDR"


def test_planted_hrv_fragmentation_signal_survives_fdr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A same-day HRV→focus-fragmentation link must survive FDR (the #6 family).

    Plants a strong negative same-day association (higher HRV → lower
    fragmentation) and asserts the hrv_attention family flags it, proving the
    fragmentation × HRV pairing is actually computed and coverage-gated.
    """
    start = date(2024, 1, 1)
    rng = random.Random(13)
    rows = []
    n = 150
    hrv_series = [float(20 + (i % 8) * 6) for i in range(n)]  # 20..62 cycling
    for i in range(n):
        d = start + timedelta(days=i)
        hrv = hrv_series[i]
        # higher HRV -> lower fragmentation (same-day), with noise.
        frag = max(0.0, 0.9 - 0.012 * hrv + rng.uniform(-0.05, 0.05))
        rows.append(
            _day(
                d,
                present={"spotify", "activitywatch", "git", "web", "health"},
                spotify_hours=rng.uniform(0.0, 6.0),
                deep_work_min=rng.uniform(0.0, 240.0),
                git_commits=rng.randint(0, 12),
                git_lines_added=rng.randint(0, 500),
                git_lines_deleted=rng.randint(0, 500),
                web_visits=rng.randint(10, 400),
                web_social_visits=rng.randint(0, 150),
                hrv_rmssd=hrv,
                stress_mean=rng.uniform(20.0, 80.0),
                aw_fragmentation=frag,
            )
        )
    end = rows[-1].date
    _patch(monkeypatch, rows, _full_cov(start, end))

    report = analyze(start, end, max_lag=3)
    sig = [c for c in report.lag_correlations if c.significant]
    match = [
        c for c in sig
        if c.family == "hrv_attention"
        and c.predictor == "hrv_rmssd"
        and c.outcome == "aw_fragmentation"
        and c.lag_days == 0
    ]
    assert match, "same-day HRV→fragmentation link should survive FDR"
    assert match[0].r < 0, "higher HRV should track lower fragmentation"


def test_out_of_coverage_and_absent_days_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Days past a source's coverage, or lacking its presence label, are dropped.

    Spotify coverage ends mid-window and later days have no 'spotify' presence;
    those trailing days must NOT enter the music_focus pairs (n must reflect only
    the in-coverage, present prefix), and the music_focus covered window must
    clamp to spotify coverage.
    """
    start = date(2024, 1, 1)
    rng = random.Random(99)
    rows = []
    spotify_end = start + timedelta(days=29)  # first 30 days have spotify
    for i in range(100):
        d = start + timedelta(days=i)
        has_spotify = i < 30
        present = {"activitywatch", "git", "web", "health"}
        if has_spotify:
            present.add("spotify")
        rows.append(
            _day(
                d,
                present=present,
                # Spotify hours present only for the first 30 days; later days
                # carry a fabricated 0.0 the gate must reject.
                spotify_hours=rng.uniform(0.0, 6.0) if has_spotify else 0.0,
                deep_work_min=rng.uniform(0.0, 240.0),
                git_commits=rng.randint(0, 12),
                git_lines_added=rng.randint(0, 500),
                git_lines_deleted=rng.randint(0, 500),
                web_visits=rng.randint(10, 400),
                web_social_visits=rng.randint(0, 150),
                hrv_rmssd=rng.uniform(20.0, 70.0),
                stress_mean=rng.uniform(20.0, 80.0),
            )
        )
    end = rows[-1].date
    bounds = _full_cov(start, end)
    bounds["spotify"] = CoverageBounds("spotify", start, spotify_end, "export")
    _patch(monkeypatch, rows, bounds)

    report = analyze(start, end, max_lag=3)

    # Music-focus correlations only use the 30 in-coverage, present spotify days
    # (minus lag), so n must never exceed 30.
    music = [c for c in report.lag_correlations if c.family == "music_focus"]
    assert music, "music_focus pairs expected from the in-coverage prefix"
    for c in music:
        assert c.n <= 30, (c.label, c.n)

    # The music_focus covered window clamps to spotify coverage.
    mf = next(f for f in report.families if f.name == "music_focus")
    assert mf.covered_end == spotify_end
    assert any("spotify: covers" in line for line in mf.coverage_provenance)


def test_absent_presence_label_blocks_correlation(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a source's presence label is never set, its pairs are not computed.

    Even with full coverage bounds, a numeric value with no presence label is
    'not observed' and must be excluded — so a family whose predictor is never
    present yields no correlations for that predictor.
    """
    start = date(2024, 1, 1)
    rng = random.Random(5)
    rows = []
    for i in range(60):
        d = start + timedelta(days=i)
        rows.append(
            _day(
                d,
                # NOTE: no 'web' presence — distraction pairs must be empty.
                present={"spotify", "activitywatch", "git", "health"},
                spotify_hours=rng.uniform(0.0, 6.0),
                deep_work_min=rng.uniform(0.0, 240.0),
                git_commits=rng.randint(0, 12),
                git_lines_added=rng.randint(0, 500),
                git_lines_deleted=rng.randint(0, 500),
                web_visits=rng.randint(10, 400),
                web_social_visits=rng.randint(0, 150),
                hrv_rmssd=rng.uniform(20.0, 70.0),
                stress_mean=rng.uniform(20.0, 80.0),
            )
        )
    end = rows[-1].date
    _patch(monkeypatch, rows, _full_cov(start, end))

    report = analyze(start, end, max_lag=3)
    assert not any(c.family == "web_productivity" for c in report.lag_correlations)
    # Other families still computed (sanity).
    assert any(c.family == "music_focus" for c in report.lag_correlations)


def test_full_history_widens_window_to_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    """full_history derives the window from source-coverage intersection (#11)."""
    # Caller passes a huge outer window; coverage is the real limiter.
    outer_start = date(2010, 1, 1)
    outer_end = date(2030, 1, 1)
    cov_start = date(2014, 3, 1)
    cov_end = date(2025, 12, 18)

    captured: dict[str, date] = {}

    def fake_matrix(start: date, end: date, *a: object, **k: object) -> list[OperatorDay]:
        captured["start"] = start
        captured["end"] = end
        # Return a small in-coverage block so analyze completes.
        rows: list[OperatorDay] = []
        for i in range(40):
            d = start + timedelta(days=i)
            rows.append(
                _day(
                    d,
                    present={"spotify", "activitywatch", "git", "web", "health"},
                    spotify_hours=float(i % 5),
                    deep_work_min=10.0 * (i % 5),
                    git_commits=i % 4,
                    web_visits=100,
                    web_social_visits=10,
                    hrv_rmssd=40.0,
                    stress_mean=40.0,
                )
            )
        return rows

    monkeypatch.setattr(lc, "operator_daily_matrix", fake_matrix)
    import lynchpin.sources.source_observations as so

    bounds = {
        "spotify": CoverageBounds("spotify", date(2013, 2, 12), cov_end, "export"),
        "webhistory": CoverageBounds("webhistory", date(2013, 3, 27), date(2026, 5, 23), "capture"),
        "activitywatch": CoverageBounds("activitywatch", cov_start, date(2026, 5, 27), "capture"),
        "health": CoverageBounds("health", date(2017, 1, 29), date(2026, 3, 29), "export"),
        "git_baseline": CoverageBounds("git_baseline", None, None, "capture"),
    }
    monkeypatch.setattr(so, "coverage_bounds", lambda *a, **k: bounds)

    report = analyze(outer_start, outer_end, full_history=True, max_lag=3)

    # Window widened to coverage union ∩ outer clamps:
    #   first = max(outer_start, min(firsts)) = 2013-02-12
    #   last  = min(outer_end,  max(lasts))  = 2026-05-27
    assert captured["start"] == date(2013, 2, 12)
    assert captured["end"] == date(2026, 5, 27)
    assert report.full_history is True
    assert "multi-year" in report.summary.lower()
