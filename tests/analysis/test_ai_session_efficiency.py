"""Tests for the AI session efficiency meta-analysis module.

Contracts pinned:
1. Efficiency (engaged/wall) math — correct ratio, numerator/denominator carried.
2. No-data days ≠ zero-efficiency — excluded, not coerced.
3. Abandonment numerator/denominator — ABANDONED_STATES membership, terminal_state=None
   is "unknown", not "not abandoned".
4. Trend direction on a monotone series — rising/falling detected by Mann-Kendall.
5. Workflow-shape early/late split is symmetric and consistent.
6. Provider row aggregation — per-provider sums are correct.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterator

import pytest

import lynchpin.analysis.ai_session_efficiency as ae
from lynchpin.analysis.ai_session_efficiency import (
    ABANDONED_STATES,
    analyze,
)
from lynchpin.sources.polylogue_models import SessionProfile


# ── Fixtures / builders ───────────────────────────────────────────────────────


def _profile(
    *,
    d: date,
    provider: str = "claude-code",
    engaged_ms: int = 0,
    wall_ms: int = 0,
    terminal_state: str | None = None,
    workflow_shape: str | None = None,
    tool_use_count: int = 0,
) -> SessionProfile:
    """Build a minimal SessionProfile for testing."""
    return SessionProfile(
        conversation_id=f"conv-{d.isoformat()}-{provider}-{id(terminal_state)}",
        provider=provider,
        title="test session",
        message_count=4,
        word_count=100,
        first_message_at=None,
        last_message_at=None,
        engaged_duration_ms=engaged_ms,
        wall_duration_ms=wall_ms,
        work_event_kind=None,
        work_event_projects=(),
        total_cost_usd=0.0,
        canonical_session_date=d,
        tool_use_count=tool_use_count,
        thinking_count=0,
        auto_tags=(),
        workflow_shape=workflow_shape,
        terminal_state=terminal_state,
    )


def _patch(monkeypatch, profiles: list[SessionProfile]) -> None:
    """Inject a fixed profile list as the source for analyze()."""
    def _source() -> Iterator[SessionProfile]:
        yield from profiles

    monkeypatch.setattr(ae, "_profile_source", _source)


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestEfficiencyMath:
    """Efficiency ratio is correct and carries numerator+denominator."""

    def test_single_session_ratio(self, monkeypatch):
        d = date(2025, 6, 1)
        # 600_000 ms engaged / 1_000_000 ms wall = 0.6
        profiles = [_profile(d=d, engaged_ms=600_000, wall_ms=1_000_000)]
        _patch(monkeypatch, profiles)
        report = analyze(start=d, end=d)

        assert report.total_sessions == 1
        assert report.efficiency_eligible_sessions == 1
        assert report.efficiency_overall_mean == pytest.approx(0.6, abs=1e-4)

        day = report.daily[0]
        assert day.efficiency_eligible_n == 1
        assert day.efficiency_numerator_ms == 600_000
        assert day.efficiency_denominator_ms == 1_000_000
        assert day.efficiency_mean == pytest.approx(0.6, abs=1e-4)

    def test_multiple_sessions_mean(self, monkeypatch):
        d = date(2025, 6, 1)
        # Session A: 0.5 ratio; Session B: 0.8 ratio → mean ≈ 0.65
        profiles = [
            _profile(d=d, engaged_ms=500_000, wall_ms=1_000_000),
            _profile(d=d, engaged_ms=800_000, wall_ms=1_000_000),
        ]
        _patch(monkeypatch, profiles)
        report = analyze(start=d, end=d)
        assert report.efficiency_overall_mean == pytest.approx(0.65, abs=1e-4)

    def test_zero_wall_excluded_from_efficiency(self, monkeypatch):
        d = date(2025, 6, 1)
        # wall_ms=0: ineligible; the second has wall=500_000.
        profiles = [
            _profile(d=d, engaged_ms=0, wall_ms=0),
            _profile(d=d, engaged_ms=200_000, wall_ms=500_000),
        ]
        _patch(monkeypatch, profiles)
        report = analyze(start=d, end=d)

        assert report.total_sessions == 2
        assert report.efficiency_eligible_sessions == 1
        assert report.efficiency_overall_mean == pytest.approx(0.4, abs=1e-4)

        day = report.daily[0]
        assert day.session_count == 2
        assert day.efficiency_eligible_n == 1


class TestNoDataDays:
    """Days with no sessions must not be treated as zero-efficiency or 0% abandonment."""

    def test_no_session_day_is_none_not_zero(self, monkeypatch):
        start = date(2025, 6, 1)
        end = date(2025, 6, 3)
        # Only June 1 has a session.
        profiles = [_profile(d=start, engaged_ms=300_000, wall_ms=600_000)]
        _patch(monkeypatch, profiles)
        report = analyze(start=start, end=end)

        assert report.n_days_with_sessions == 1
        assert len(report.daily) == 3

        empty_days = [d for d in report.daily if not d.has_data]
        assert len(empty_days) == 2
        for ed in empty_days:
            assert ed.session_count == 0
            assert ed.efficiency_mean is None, "no-data day must not be 0.0"
            assert ed.abandonment_rate is None, "no-data day must not be 0.0"
            assert ed.tool_use_per_session is None

    def test_overall_mean_excludes_no_data_days(self, monkeypatch):
        start = date(2025, 6, 1)
        end = date(2025, 6, 5)
        # Only 2 of 5 days have sessions; overall mean is NOT diluted by the empties.
        profiles = [
            _profile(d=start, engaged_ms=600_000, wall_ms=1_000_000),
            _profile(d=start + timedelta(days=4), engaged_ms=200_000, wall_ms=1_000_000),
        ]
        _patch(monkeypatch, profiles)
        report = analyze(start=start, end=end)

        # Mean of [0.6, 0.2] = 0.4 — NOT (0.6 + 0.2) / 5.
        assert report.efficiency_overall_mean == pytest.approx(0.4, abs=1e-4)


class TestAbandonmentNumeratorDenominator:
    """Abandonment math carries numerator + denominator; None terminal_state is unknown."""

    def test_abandoned_states_counted(self, monkeypatch):
        d = date(2025, 6, 1)
        # Two known-abandoned, one completed, one unknown (None).
        profiles = [
            _profile(d=d, terminal_state="abandoned"),
            _profile(d=d, terminal_state="stuck"),
            _profile(d=d, terminal_state="completed"),
            _profile(d=d, terminal_state=None),  # unknown — excluded
        ]
        _patch(monkeypatch, profiles)
        report = analyze(start=d, end=d)

        assert report.abandoned_n == 2
        assert report.terminal_known_n == 3  # None excluded
        # rate = 2/3
        assert report.abandonment_rate == pytest.approx(2 / 3, abs=1e-4)

    def test_none_terminal_state_not_in_denominator(self, monkeypatch):
        d = date(2025, 6, 1)
        profiles = [
            _profile(d=d, terminal_state=None),
            _profile(d=d, terminal_state=None),
        ]
        _patch(monkeypatch, profiles)
        report = analyze(start=d, end=d)

        assert report.terminal_known_n == 0
        assert report.abandonment_rate is None  # not 0.0

    def test_error_exit_counted_as_abandoned(self, monkeypatch):
        d = date(2025, 6, 1)
        assert "error_exit" in ABANDONED_STATES
        profiles = [_profile(d=d, terminal_state="error_exit")]
        _patch(monkeypatch, profiles)
        report = analyze(start=d, end=d)
        assert report.abandoned_n == 1
        assert report.abandonment_rate == pytest.approx(1.0, abs=1e-4)

    def test_day_level_abandonment_carries_both(self, monkeypatch):
        d = date(2025, 6, 1)
        profiles = [
            _profile(d=d, terminal_state="abandoned"),
            _profile(d=d, terminal_state="completed"),
        ]
        _patch(monkeypatch, profiles)
        report = analyze(start=d, end=d)
        day = report.daily[0]
        assert day.abandoned_n == 1
        assert day.terminal_known_n == 2
        assert day.abandonment_rate == pytest.approx(0.5, abs=1e-4)


class TestTrendDirection:
    """Mann-Kendall detects rising/falling on monotone series."""

    def _monotone_profiles(
        self, start: date, n_days: int, *, direction: str
    ) -> list[SessionProfile]:
        """Build a profile per day with linearly changing tool_use_count."""
        profiles = []
        for i in range(n_days):
            d = start + timedelta(days=i)
            tool_count = (i + 1) if direction == "rising" else (n_days - i)
            profiles.append(
                _profile(d=d, engaged_ms=(i + 1) * 60_000, wall_ms=1_000_000, tool_use_count=tool_count)
            )
        return profiles

    def test_rising_sessions_detected(self, monkeypatch):
        start = date(2025, 1, 1)
        n = 20
        # Generate multiple sessions per day to create a rising trend.
        profiles = []
        for i in range(n):
            d = start + timedelta(days=i)
            for _ in range(i + 1):  # day 0: 1 session, day 19: 20 sessions
                profiles.append(_profile(d=d, wall_ms=1_000_000, engaged_ms=500_000))
        _patch(monkeypatch, profiles)
        report = analyze(start=start, end=start + timedelta(days=n - 1))
        assert report.trend_sessions_per_day is not None
        assert report.trend_sessions_per_day.direction == "rising"

    def test_falling_tool_use_detected(self, monkeypatch):
        start = date(2025, 1, 1)
        n = 20
        # Strongly falling tool-use: day i has (n - i) tools.
        profiles = self._monotone_profiles(start, n, direction="falling")
        _patch(monkeypatch, profiles)
        report = analyze(start=start, end=start + timedelta(days=n - 1))
        assert report.trend_tool_use_per_session is not None
        assert report.trend_tool_use_per_session.direction == "falling"

    def test_rising_engaged_minutes_detected(self, monkeypatch):
        start = date(2025, 1, 1)
        n = 20
        # engaged_ms grows by 60_000 each day → engaged_minutes rises.
        profiles = self._monotone_profiles(start, n, direction="rising")
        _patch(monkeypatch, profiles)
        report = analyze(start=start, end=start + timedelta(days=n - 1))
        assert report.trend_engaged_minutes_per_day is not None
        assert report.trend_engaged_minutes_per_day.direction == "rising"

    def test_insufficient_data_returns_none_trend(self, monkeypatch):
        start = date(2025, 6, 1)
        profiles = [_profile(d=start)]  # only 1 day — below MIN_TREND_DAYS
        _patch(monkeypatch, profiles)
        report = analyze(start=start, end=start)
        # With only 1 data-bearing day, all trends are None.
        assert report.trend_sessions_per_day is None
        assert report.trend_engaged_minutes_per_day is None


class TestWorkflowShapeSplit:
    """Workflow-shape early/late windows split the window consistently."""

    def test_shape_distribution_full_matches_halves(self, monkeypatch):
        start = date(2025, 6, 1)
        end = date(2025, 6, 30)
        mid_offset = (end - start).days // 2

        profiles = []
        for i in range(30):
            d = start + timedelta(days=i)
            shape = "research" if i < mid_offset else "implement_verify_loop"
            profiles.append(_profile(d=d, workflow_shape=shape))
        _patch(monkeypatch, profiles)

        report = analyze(start=start, end=end)
        assert report.shape_full is not None
        assert report.shape_early is not None
        assert report.shape_late is not None

        # Full window captures both shapes.
        assert "research" in report.shape_full.distribution
        assert "implement_verify_loop" in report.shape_full.distribution

        # Early half dominated by research.
        assert report.shape_early.top_shape == "research"
        # Late half dominated by implement_verify_loop.
        assert report.shape_late.top_shape == "implement_verify_loop"

        # Session counts are consistent (no double-counting).
        assert (
            report.shape_early.session_count + report.shape_late.session_count
            >= report.shape_full.session_count - 1  # mid-day may land in either
        )


class TestProviderRows:
    """Per-provider aggregation sums are correct."""

    def test_two_providers_split_correctly(self, monkeypatch):
        d = date(2025, 6, 1)
        profiles = [
            _profile(d=d, provider="claude-code", engaged_ms=600_000, wall_ms=1_000_000, terminal_state="completed"),
            _profile(d=d, provider="claude-code", engaged_ms=400_000, wall_ms=1_000_000, terminal_state="abandoned"),
            _profile(d=d, provider="chatgpt", engaged_ms=0, wall_ms=0),  # ineligible
        ]
        _patch(monkeypatch, profiles)
        report = analyze(start=d, end=d)

        by_prov = {row.provider: row for row in report.by_provider}
        assert "claude-code" in by_prov
        assert "chatgpt" in by_prov

        cc = by_prov["claude-code"]
        assert cc.session_count == 2
        assert cc.efficiency_eligible_n == 2
        assert cc.efficiency_mean == pytest.approx(0.5, abs=1e-4)  # (0.6 + 0.4) / 2
        assert cc.abandonment_numerator == 1
        assert cc.abandonment_denominator == 2
        assert cc.abandonment_rate == pytest.approx(0.5, abs=1e-4)

        gpt = by_prov["chatgpt"]
        assert gpt.session_count == 1
        assert gpt.efficiency_eligible_n == 0
        assert gpt.efficiency_mean is None  # no valid wall_ms


class TestSummaryContent:
    """Summary carries required caveats inline."""

    def test_summary_has_caveats(self, monkeypatch):
        d = date(2025, 6, 1)
        profiles = [_profile(d=d, engaged_ms=500_000, wall_ms=1_000_000, terminal_state="completed")]
        _patch(monkeypatch, profiles)
        report = analyze(start=d, end=d)

        assert "CAVEAT" in report.summary
        assert "wall_duration_ms" in report.summary or "wall_ms" in report.summary
        assert "Mann-Kendall" in report.summary or "trend" in report.summary.lower()

    def test_summary_includes_abandonment_states(self, monkeypatch):
        d = date(2025, 6, 1)
        profiles = [_profile(d=d, terminal_state="abandoned")]
        _patch(monkeypatch, profiles)
        report = analyze(start=d, end=d)
        # Summary must name the abandoned states so the LLM doesn't omit them.
        for state in ABANDONED_STATES:
            assert state in report.summary
