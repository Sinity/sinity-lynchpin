"""Tests for context utility modules: delta, contrast, project_arcs, selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import pytest

from lynchpin.context.selection import ContextAssembly, _estimate_tokens, _score_packet, select_context
from lynchpin.context.contrast import (
    build_contrast,
    build_contrast_for_latest_month,
    build_contrast_for_latest_week,
)
from lynchpin.context.delta import build_delta
from lynchpin.context.project_arcs import build_project_arcs
from lynchpin.trajectory.day import TrajectoryDay


# ---------------------------------------------------------------------------
# Minimal duck-typed period stub — satisfies getattr-based contrast/delta
# ---------------------------------------------------------------------------


@dataclass
class _Period:
    active_seconds: float = 36000.0
    recovery_seconds: float = 28800.0
    dominant_mode: Optional[str] = "coding"
    dominant_project: Optional[str] = "sinex"
    dominant_topic: Optional[str] = "rust"
    chain_count: int = 100
    commit_count: int = 10
    iso_week: str = "2026-W10"
    month: str = "2026-03"
    quarter: str = "2026-Q1"
    date: date = date(2026, 3, 16)


# ---------------------------------------------------------------------------
# TrajectoryMonth helpers (re-use real summarize_months for realistic objects)
# ---------------------------------------------------------------------------


def _make_day(
    day_date: date,
    *,
    active_seconds: float = 36000.0,
    recovery_seconds: float = 28800.0,
    commit_count: int = 0,
    top_projects: tuple = (("sinex", 36000.0),),
    top_modes: tuple = (("coding", 36000.0),),
    top_topics: tuple = (),
) -> TrajectoryDay:
    return TrajectoryDay(
        date=day_date,
        active_seconds=active_seconds,
        recovery_seconds=recovery_seconds,
        chain_count=10,
        signal_count=50,
        command_count=5,
        transcript_count=0,
        commit_count=commit_count,
        dominant_mode="coding",
        dominant_project="sinex",
        dominant_topic=None,
        top_modes=top_modes,
        top_projects=top_projects,
        top_topics=top_topics,
        source_counts={"atuin.command": 5},
        coverage={"has_activitywatch": True, "has_terminal": True, "has_chatlog": False, "has_git": False},
        highlights=(),
        projects=(),
    )


# =============================================================================
# build_delta tests
# =============================================================================


class TestBuildDelta:
    def _make_state(
        self,
        *,
        episode_ids: list[str] | None = None,
        top_projects: list[list] | None = None,
        active_hours: float = 40.0,
        dominant_mode: str | None = "coding",
        claim_statements: list[str] | None = None,
    ) -> dict:
        return {
            "episodes": [{"episode_id": eid, "label": eid} for eid in (episode_ids or [])],
            "period": {
                "top_projects": top_projects or [["sinex", 100.0]],
                "active_hours": active_hours,
            },
            "current": {"dominant_mode": dominant_mode},
            "claims": {
                "claims": [{"statement": s} for s in (claim_statements or [])],
            },
        }

    def test_new_episode_detected(self) -> None:
        prior = self._make_state(episode_ids=["ep1"])
        current = self._make_state(episode_ids=["ep1", "ep2"])
        delta = build_delta(prior, current)
        assert "ep2" in delta.new_episodes
        assert "ep1" not in delta.new_episodes

    def test_ended_episode_detected(self) -> None:
        prior = self._make_state(episode_ids=["ep1", "ep2"])
        current = self._make_state(episode_ids=["ep2"])
        delta = build_delta(prior, current)
        assert "ep1" in delta.ended_episodes

    def test_project_entered(self) -> None:
        prior = self._make_state(top_projects=[["sinex", 100.0]])
        current = self._make_state(top_projects=[["polylogue", 100.0]])
        delta = build_delta(prior, current)
        entered = [s for s in delta.project_shifts if "entered" in s]
        left = [s for s in delta.project_shifts if "left" in s]
        assert any("polylogue" in s for s in entered)
        assert any("sinex" in s for s in left)

    def test_mode_shift_detected(self) -> None:
        prior = self._make_state(dominant_mode="coding")
        current = self._make_state(dominant_mode="research")
        delta = build_delta(prior, current)
        assert len(delta.mode_shifts) == 1
        assert "coding" in delta.mode_shifts[0]
        assert "research" in delta.mode_shifts[0]

    def test_no_mode_shift_when_same(self) -> None:
        prior = self._make_state(dominant_mode="coding")
        current = self._make_state(dominant_mode="coding")
        delta = build_delta(prior, current)
        assert delta.mode_shifts == []

    def test_new_claims_detected(self) -> None:
        prior = self._make_state(claim_statements=["works in rust"])
        current = self._make_state(claim_statements=["works in rust", "active at night"])
        delta = build_delta(prior, current)
        assert "active at night" in delta.new_claims
        assert "works in rust" not in delta.new_claims

    def test_active_hours_delta_positive(self) -> None:
        prior = self._make_state(active_hours=40.0)
        current = self._make_state(active_hours=50.0)
        delta = build_delta(prior, current)
        assert delta.active_hours_delta == pytest.approx(10.0)

    def test_active_hours_delta_negative(self) -> None:
        prior = self._make_state(active_hours=50.0)
        current = self._make_state(active_hours=40.0)
        delta = build_delta(prior, current)
        assert delta.active_hours_delta == pytest.approx(-10.0)

    def test_empty_states_return_empty_delta(self) -> None:
        delta = build_delta({}, {})
        assert delta.new_episodes == []
        assert delta.ended_episodes == []
        assert delta.project_shifts == []
        assert delta.mode_shifts == []
        assert delta.new_claims == []
        assert delta.active_hours_delta == 0.0

    def test_to_dict_is_serializable(self) -> None:
        import json
        prior = self._make_state(episode_ids=["ep1"])
        current = self._make_state(episode_ids=["ep2"])
        delta = build_delta(prior, current)
        d = delta.to_dict()
        json.dumps(d)  # must not raise
        assert "new_episodes" in d
        assert "active_hours_delta" in d

    def test_active_hours_delta_from_active_seconds(self) -> None:
        """build_delta should derive hours from active_seconds when active_hours is absent.

        period.to_dict() returns 'active_seconds', not 'active_hours'. This test
        verifies that active_hours_delta is correctly computed from active_seconds.
        """
        prior = {"period": {"top_projects": [["sinex", 100.0]], "active_seconds": 144000.0}, "current": {}, "claims": {}, "episodes": []}
        current = {"period": {"top_projects": [["sinex", 100.0]], "active_seconds": 180000.0}, "current": {}, "claims": {}, "episodes": []}
        delta = build_delta(prior, current)
        # 180000 - 144000 = 36000 seconds = 10 hours
        assert delta.active_hours_delta == pytest.approx(10.0)

    def test_anomaly_count_delta_from_coverage(self) -> None:
        """build_delta reads anomaly_count from coverage dict in both states."""
        prior = self._make_state()
        prior = dict(prior, coverage={"anomaly_count": 3})
        current = self._make_state()
        current = dict(current, coverage={"anomaly_count": 7})
        delta = build_delta(prior, current)
        assert delta.anomaly_count_delta == 4

    def test_anomaly_count_delta_zero_when_coverage_absent(self) -> None:
        """anomaly_count_delta is 0 when coverage is not present in states."""
        prior = self._make_state()
        current = self._make_state()
        delta = build_delta(prior, current)
        assert delta.anomaly_count_delta == 0

    def test_anomaly_count_delta_negative(self) -> None:
        """anomaly_count_delta can be negative when anomalies decrease."""
        prior = self._make_state()
        prior = dict(prior, coverage={"anomaly_count": 10})
        current = self._make_state()
        current = dict(current, coverage={"anomaly_count": 4})
        delta = build_delta(prior, current)
        assert delta.anomaly_count_delta == -6


# =============================================================================
# build_contrast tests
# =============================================================================


class TestBuildContrast:
    def test_direction_up_when_more_active(self) -> None:
        prior = _Period(active_seconds=36000.0)   # 10h
        current = _Period(active_seconds=54000.0)  # 15h — +50%
        contrast = build_contrast(current, prior, "week")
        assert contrast.direction == "up"

    def test_direction_down_when_less_active(self) -> None:
        prior = _Period(active_seconds=54000.0)   # 15h
        current = _Period(active_seconds=36000.0)  # 10h — -33%
        contrast = build_contrast(current, prior, "week")
        assert contrast.direction == "down"

    def test_direction_flat_when_similar(self) -> None:
        prior = _Period(active_seconds=36000.0)
        current = _Period(active_seconds=36100.0)  # +0.1h — well within 5% threshold
        contrast = build_contrast(current, prior, "week")
        assert contrast.direction == "flat"

    def test_dominant_mode_shift(self) -> None:
        prior = _Period(dominant_mode="coding")
        current = _Period(dominant_mode="research")
        contrast = build_contrast(current, prior, "month")
        assert contrast.dominant_mode_shift is not None
        assert "coding" in contrast.dominant_mode_shift
        assert "research" in contrast.dominant_mode_shift

    def test_no_mode_shift_when_same_mode(self) -> None:
        prior = _Period(dominant_mode="coding")
        current = _Period(dominant_mode="coding")
        contrast = build_contrast(current, prior, "week")
        assert contrast.dominant_mode_shift is None

    def test_project_shift_detected(self) -> None:
        prior = _Period(dominant_project="sinex")
        current = _Period(dominant_project="polylogue")
        contrast = build_contrast(current, prior, "month")
        assert contrast.dominant_project_shift is not None
        assert "sinex" in contrast.dominant_project_shift
        assert "polylogue" in contrast.dominant_project_shift

    def test_commit_count_delta(self) -> None:
        prior = _Period(commit_count=10)
        current = _Period(commit_count=25)
        contrast = build_contrast(current, prior, "week")
        assert contrast.commit_count_delta == 15

    def test_chain_count_delta(self) -> None:
        prior = _Period(chain_count=100)
        current = _Period(chain_count=80)
        contrast = build_contrast(current, prior, "week")
        assert contrast.chain_count_delta == -20

    def test_week_keys_match_iso_week(self) -> None:
        prior = _Period(iso_week="2026-W09")
        current = _Period(iso_week="2026-W10")
        contrast = build_contrast(current, prior, "week")
        assert contrast.current_key == "2026-W10"
        assert contrast.prior_key == "2026-W09"
        assert contrast.scale == "week"

    def test_month_keys_match_month_field(self) -> None:
        prior = _Period(month="2026-02")
        current = _Period(month="2026-03")
        contrast = build_contrast(current, prior, "month")
        assert contrast.current_key == "2026-03"
        assert contrast.prior_key == "2026-02"

    def test_year_keys_match_year_field(self) -> None:
        @dataclass
        class _YearPeriod:
            year: str
            active_seconds: float = 360000.0
            recovery_seconds: float = 100000.0
            chain_count: int = 500
            commit_count: int = 50
            dominant_mode: Optional[str] = "coding"
            dominant_project: Optional[str] = "sinex"
            dominant_topic: Optional[str] = "rust"

        prior = _YearPeriod(year="2025")
        current = _YearPeriod(year="2026")
        contrast = build_contrast(current, prior, "year")
        assert contrast.current_key == "2026"
        assert contrast.prior_key == "2025"
        assert contrast.scale == "year"


class TestBuildContrastConvenience:
    def _make_week(self, iso_week: str, active_seconds: float) -> object:
        from lynchpin.trajectory.week import TrajectoryWeek
        return TrajectoryWeek(
            iso_week=iso_week,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 11),
            days=7,
            active_seconds=active_seconds,
            recovery_seconds=28800.0,
            chain_count=100,
            signal_count=500,
            command_count=50,
            transcript_count=0,
            commit_count=5,
            top_modes=(("coding", active_seconds),),
            top_projects=(("sinex", active_seconds),),
            top_topics=(),
            day_pattern="Mon-Fri",
            busiest_day=date(2026, 1, 6),
            quietest_day=date(2026, 1, 11),
            active_delta_vs_prior=None,
        )

    def test_build_contrast_for_latest_week_returns_none_for_single_week(self) -> None:
        w = self._make_week("2026-W02", 36000.0)
        result = build_contrast_for_latest_week([w])
        assert result is None

    def test_build_contrast_for_latest_week_compares_last_two(self) -> None:
        w1 = self._make_week("2026-W02", 36000.0)   # 10h
        w2 = self._make_week("2026-W03", 54000.0)   # 15h — +50%
        result = build_contrast_for_latest_week([w1, w2])
        assert result is not None
        assert result.direction == "up"
        assert result.current_key == "2026-W03"
        assert result.prior_key == "2026-W02"

    def test_build_contrast_for_latest_month_returns_none_for_single_month(self) -> None:
        from lynchpin.trajectory.month import summarize_months
        days = [_make_day(date(2026, 3, d)) for d in range(1, 5)]
        months = summarize_months(days)
        result = build_contrast_for_latest_month(months)
        assert result is None

    def test_build_contrast_for_latest_month_with_two_months(self) -> None:
        from lynchpin.trajectory.month import summarize_months
        days = (
            [_make_day(date(2026, 2, d), active_seconds=36000.0) for d in range(1, 5)]
            + [_make_day(date(2026, 3, d), active_seconds=54000.0) for d in range(1, 5)]
        )
        months = summarize_months(days)
        result = build_contrast_for_latest_month(months)
        assert result is not None
        assert result.scale == "month"


# =============================================================================
# build_project_arcs tests
# =============================================================================


class TestBuildProjectArcs:
    def _make_months(self, project: str = "sinex", n: int = 3, hours_per_day: float = 10.0) -> list:
        from lynchpin.trajectory.month import summarize_months

        days = []
        for month_idx in range(n):
            m = month_idx + 1
            max_d = 28 if m == 2 else (30 if m in (4, 6, 9, 11) else 31)
            for d in range(1, min(max_d, 5) + 1):
                days.append(
                    _make_day(
                        date(2026, m, d),
                        active_seconds=hours_per_day * 3600,
                        top_projects=((project, hours_per_day * 3600),),
                    )
                )
        return summarize_months(days)

    def test_returns_arc_for_active_project(self) -> None:
        months = self._make_months("sinex", n=3)
        arcs = build_project_arcs(months)
        assert len(arcs) >= 1
        assert arcs[0].project == "sinex"

    def test_arc_has_positive_total_hours(self) -> None:
        months = self._make_months("polylogue", n=2, hours_per_day=8.0)
        arcs = build_project_arcs(months)
        assert arcs[0].total_hours > 0.0

    def test_arc_active_months_matches_data(self) -> None:
        months = self._make_months("sinex", n=3)
        arcs = build_project_arcs(months)
        # 3 months of data, all with sinex
        sinex_arc = next((a for a in arcs if a.project == "sinex"), None)
        assert sinex_arc is not None
        assert sinex_arc.active_months == 3

    def test_accelerating_project_trend(self) -> None:
        from lynchpin.trajectory.month import summarize_months
        # Build 6 months where later months have much more time
        days = []
        for m in range(1, 7):
            # First 3 months: 2h/day, last 3: 8h/day
            hrs = 2.0 if m <= 3 else 8.0
            max_d = 28 if m == 2 else (30 if m in (4, 6) else 31)
            for d in range(1, min(max_d, 5) + 1):
                days.append(
                    _make_day(
                        date(2026, m, d),
                        active_seconds=hrs * 3600,
                        top_projects=(("sinex", hrs * 3600),),
                    )
                )
        months = summarize_months(days)
        arcs = build_project_arcs(months)
        sinex_arc = next((a for a in arcs if a.project == "sinex"), None)
        assert sinex_arc is not None
        assert sinex_arc.velocity_trend == "accelerating"

    def test_empty_months_returns_empty(self) -> None:
        arcs = build_project_arcs([])
        assert arcs == []

    def test_arc_to_dict_is_serializable(self) -> None:
        import json
        months = self._make_months("sinex", n=2)
        arcs = build_project_arcs(months)
        for arc in arcs:
            d = arc.to_dict()
            json.dumps(d)
            assert "project" in d
            assert "total_hours" in d
            assert "velocity_trend" in d

    def test_weekly_momentum_accelerating_when_recent_weeks_higher(self) -> None:
        """momentum derived from last 4 weeks when project has 3+ weekly data points."""
        from lynchpin.trajectory.week import TrajectoryWeek
        from datetime import date

        def _make_week(iso_week: str, project: str, hours: float) -> TrajectoryWeek:
            yr, wn = iso_week.split("-W")
            # minimal week object
            return TrajectoryWeek(
                iso_week=iso_week,
                start_date=date(int(yr), 1, 1),
                end_date=date(int(yr), 1, 7),
                days=5,
                active_seconds=hours * 3600,
                recovery_seconds=0.0,
                chain_count=10,
                signal_count=50,
                command_count=5,
                transcript_count=0,
                commit_count=0,
                top_modes=(("coding", hours * 3600),),
                top_projects=((project, hours * 3600),),
                top_topics=(),
                day_pattern="uniform",
                busiest_day=None,
                quietest_day=None,
                active_delta_vs_prior=None,
            )

        months = self._make_months("sinex", n=2, hours_per_day=4.0)
        # 4 weeks: first 2 at 2h, last 2 at 10h → accelerating momentum
        weeks = [
            _make_week("2026-W01", "sinex", 2.0),
            _make_week("2026-W02", "sinex", 2.0),
            _make_week("2026-W03", "sinex", 10.0),
            _make_week("2026-W04", "sinex", 10.0),
        ]
        arcs = build_project_arcs(months, weeks=weeks)
        sinex_arc = next((a for a in arcs if a.project == "sinex"), None)
        assert sinex_arc is not None
        assert sinex_arc.momentum == "accelerating"

    def test_weekly_momentum_falls_back_to_trend_with_sparse_weeks(self) -> None:
        """Only 1 week of data → fall back to velocity_trend."""
        from lynchpin.trajectory.week import TrajectoryWeek
        from datetime import date

        months = self._make_months("sinex", n=2, hours_per_day=4.0)
        week = TrajectoryWeek(
            iso_week="2026-W01",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 7),
            days=5,
            active_seconds=36000.0,
            recovery_seconds=0.0,
            chain_count=10,
            signal_count=50,
            command_count=5,
            transcript_count=0,
            commit_count=0,
            top_modes=(("coding", 36000.0),),
            top_projects=(("sinex", 36000.0),),
            top_topics=(),
            day_pattern="uniform",
            busiest_day=None,
            quietest_day=None,
            active_delta_vs_prior=None,
        )
        arcs = build_project_arcs(months, weeks=[week])
        sinex_arc = next((a for a in arcs if a.project == "sinex"), None)
        assert sinex_arc is not None
        # Only 1 week, so momentum falls back to velocity_trend
        assert sinex_arc.momentum == sinex_arc.velocity_trend


# =============================================================================
# select_context tests (unit-level using helpers + monkeypatched build_current_state)
# =============================================================================


def _fake_state(extra_packets: dict | None = None) -> dict:
    """Build a minimal fake build_current_state response for testing."""
    base: dict = {
        "schema": "1",
        "generated_at": "2026-03-16T10:00:00+00:00",
        "budget_tier": "standard",
        "window": {"start": "2026-03-02", "end": "2026-03-16", "days": 14},
        "coverage": {"quality": "rich", "plane_count": 4},
        "period": {"active_hours": 40.0, "chain_count": 100},
        "current": None,
        "days": [{"date": "2026-03-16", "active_seconds": 36000}],
        "weeks": [{"iso_week": "2026-W11", "active_seconds": 180000}],
        "months": [],
        "quarters": [],
        "years": [],
        "episodes": [],
        "themes": [{"name": "sinex", "kind": "project", "total_hours": 80.0}],
        "project_arcs": [],
        "claims": {"claims": [{"statement": "Primary project is sinex", "confidence": 0.9}]},
        "memory": [{"statement": "persisted claim", "confidence": 0.8}],
        "threads": [],
        "chat_work_events": {},
        "recent_chains": [],
    }
    if extra_packets:
        base.update(extra_packets)
    return base


class TestScorePacket:
    def test_empty_query_returns_nonzero_recency(self) -> None:
        # query_terms empty → topic_match = 0; recency_score drives result
        score = _score_packet("days", {"date": "2026-03-16"}, set())
        assert score > 0.0  # recency_score for "days" = 1.0 * 0.3

    def test_matching_query_term_boosts_score(self) -> None:
        packet = {"project": "sinex", "active_hours": 10.0}
        score_no_match = _score_packet("weeks", packet, {"python"})
        score_match = _score_packet("weeks", packet, {"sinex"})
        assert score_match > score_no_match

    def test_days_packet_has_higher_recency_than_years(self) -> None:
        packet = {"value": "x"}
        day_score = _score_packet("days", packet, set())
        year_score = _score_packet("years", packet, set())
        assert day_score > year_score

    def test_known_project_in_query_and_packet_adds_type_priority(self) -> None:
        # "sinex" is a known project; it should add type_priority bonus when matched
        packet = {"project": "sinex", "chain_count": 10}
        score_with_sinex = _score_packet("themes", packet, {"sinex"})
        score_without = _score_packet("themes", {"project": "other"}, {"other"})
        # sinex is in ALL_PROJECTS, "other" isn't — type_priority fires for sinex
        assert score_with_sinex >= score_without


class TestEstimateTokens:
    def test_empty_packet_returns_at_least_one(self) -> None:
        assert _estimate_tokens({}) >= 1

    def test_larger_packet_returns_more_tokens(self) -> None:
        small = {"k": "v"}
        large = {"key": "x" * 1000}
        assert _estimate_tokens(large) > _estimate_tokens(small)

    def test_approx_4_chars_per_token(self) -> None:
        # A 400-char payload → ~100 tokens
        payload = {"data": "a" * 392}  # ~400 chars in JSON
        tokens = _estimate_tokens(payload)
        assert 80 <= tokens <= 120


class TestSelectContext:
    def test_returns_context_assembly(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "lynchpin.context.selection.build_current_state",
            lambda **_: _fake_state(),
            raising=False,
        )
        # Patch via packet_builders reference used inside select_context
        import lynchpin.context.packet_builders as pb
        monkeypatch.setattr(pb, "build_current_state", lambda **_: _fake_state())
        result = select_context("sinex project status", budget_tokens=2000)
        assert isinstance(result, ContextAssembly)
        assert result.budget_tokens == 2000
        assert result.total_estimated_tokens <= 2000

    def test_respects_budget(self, monkeypatch) -> None:
        import lynchpin.context.packet_builders as pb
        # State with large packets that exceed a tiny budget
        fat_packet = {"data": "x" * 4000}
        state = _fake_state()
        state["days"] = [fat_packet]
        monkeypatch.setattr(pb, "build_current_state", lambda **_: state)
        result = select_context("test", budget_tokens=100)
        assert result.total_estimated_tokens <= 100

    def test_includes_memory_packets(self, monkeypatch) -> None:
        import lynchpin.context.packet_builders as pb
        state = _fake_state()
        state["memory"] = [{"statement": "important claim", "confidence": 0.9}]
        monkeypatch.setattr(pb, "build_current_state", lambda **_: state)
        result = select_context("memory", budget_tokens=10000)
        assert "memory" in result.packet_types_included

    def test_packet_types_included_nonempty(self, monkeypatch) -> None:
        import lynchpin.context.packet_builders as pb
        monkeypatch.setattr(pb, "build_current_state", lambda **_: _fake_state())
        result = select_context("sinex", budget_tokens=10000)
        assert len(result.packet_types_included) >= 1

    def test_to_dict_is_serializable(self, monkeypatch) -> None:
        import json
        import lynchpin.context.packet_builders as pb
        monkeypatch.setattr(pb, "build_current_state", lambda **_: _fake_state())
        result = select_context("rust", budget_tokens=5000)
        d = result.to_dict()
        json.dumps(d)
        assert "query" in d
        assert "packets" in d
        assert "total_estimated_tokens" in d
