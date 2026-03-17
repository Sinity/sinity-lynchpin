"""Tests for context/packet_builders.py individual packet-build functions.

Verifies field mapping, tier-based top-N truncation, hours conversion,
and to_dict() serialization for each packet builder.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from lynchpin.context.packet_builders import (
    build_coverage_packet,
    build_day_packet,
    build_episode_packet,
    build_month_packet,
    build_project_packet,
    build_week_packet,
)
from lynchpin.context.packet_types import (
    DayPacket,
    EpisodePacket,
    MonthPacket,
    ProjectPacket,
    WeekPacket,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_trajectory_day(**kwargs):
    defaults = dict(
        date=date(2026, 3, 10),
        active_seconds=14400.0,
        recovery_seconds=3600.0,
        chain_count=5,
        signal_count=80,
        command_count=30,
        transcript_count=2,
        commit_count=3,
        dominant_mode="coding",
        dominant_project="sinex",
        dominant_topic="rust",
        top_modes=(("coding", 10000.0), ("review", 4000.0), ("research", 1000.0), ("chat", 500.0)),
        top_projects=(("sinex", 10000.0), ("lynchpin", 4000.0), ("sinnix", 1000.0)),
        top_topics=(("rust", 9000.0), ("nix", 3000.0), ("python", 1000.0)),
        highlights=["commit: feat: add batch ingest", "session: 120 min"],
        coverage={},
        source_counts={},
        anomalies=[],
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_trajectory_week(**kwargs):
    defaults = dict(
        iso_week="2026-W11",
        start_date=date(2026, 3, 9),
        end_date=date(2026, 3, 15),
        days=7,
        active_seconds=72000.0,
        recovery_seconds=10800.0,
        chain_count=25,
        signal_count=400,
        command_count=150,
        transcript_count=8,
        commit_count=15,
        top_modes=(("coding", 50000.0), ("review", 15000.0), ("research", 5000.0)),
        top_projects=(("sinex", 40000.0), ("lynchpin", 20000.0)),
        top_topics=(("rust", 35000.0), ("nix", 10000.0)),
        day_pattern="uniform",
        busiest_day=date(2026, 3, 11),
        quietest_day=date(2026, 3, 15),
        active_delta_vs_prior=None,
        dominant_mode="coding",
        dominant_project="sinex",
        dominant_topic="rust",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_trajectory_month(**kwargs):
    defaults = dict(
        month="2026-03",
        start_date=date(2026, 3, 1),
        end_date=date(2026, 3, 31),
        total_days=31,
        active_days=22,
        active_seconds=396000.0,
        recovery_seconds=54000.0,
        chain_count=100,
        signal_count=1500,
        command_count=600,
        transcript_count=30,
        commit_count=60,
        top_modes=(("coding", 280000.0), ("review", 80000.0)),
        top_projects=(("sinex", 200000.0), ("lynchpin", 80000.0)),
        top_topics=(("rust", 180000.0), ("nix", 60000.0)),
        highlights=["Major sprint", "KG export"],
        chat_session_count=20,
        chat_work_events={"implementation": 8, "review": 5},
        chat_cost_usd=3.50,
        episode_count=2,
        episode_labels=["sinex-sprint", "nix-overhaul"],
        active_delta_vs_prior=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_trajectory_episode(**kwargs):
    defaults = dict(
        episode_id="abc12345",
        label="sinex-sprint",
        start_date=date(2026, 3, 1),
        end_date=date(2026, 3, 10),
        days=10,
        active_seconds=72000.0,
        dominant_mode="coding",
        dominant_project="sinex",
        dominant_topic="rust",
        mode_distribution={"coding": 50000.0},
        project_distribution={"sinex": 60000.0},
        trigger="project_shift",
        confidence=0.85,
        day_count_with_dominant=8,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_chain(**kwargs):
    defaults = dict(
        chain_id="c001",
        project="sinex",
        mode="coding",
        duration_seconds=3600.0,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# build_day_packet
# ---------------------------------------------------------------------------

class TestBuildDayPacket:
    def test_returns_day_packet_instance(self):
        day = _make_trajectory_day()
        pkt = build_day_packet(day)
        assert isinstance(pkt, DayPacket)

    def test_date_is_isoformat(self):
        day = _make_trajectory_day(date=date(2026, 3, 15))
        pkt = build_day_packet(day)
        assert pkt.date == "2026-03-15"

    def test_hours_conversion(self):
        day = _make_trajectory_day(active_seconds=7200.0, recovery_seconds=1800.0)
        pkt = build_day_packet(day)
        assert pkt.active_hours == pytest.approx(2.0)
        assert pkt.recovery_hours == pytest.approx(0.5)

    def test_dominant_fields_passed_through(self):
        day = _make_trajectory_day()
        pkt = build_day_packet(day)
        assert pkt.dominant_mode == "coding"
        assert pkt.dominant_project == "sinex"
        assert pkt.dominant_topic == "rust"

    def test_compact_tier_truncates_top_modes(self):
        day = _make_trajectory_day()
        pkt = build_day_packet(day, tier="compact")
        # compact tier uses top-2
        assert len(pkt.top_modes) <= 3

    def test_to_dict_is_json_serializable(self):
        day = _make_trajectory_day()
        pkt = build_day_packet(day)
        d = pkt.to_dict()
        json.dumps(d)
        assert "date" in d
        assert "active_hours" in d
        assert "dominant_mode" in d

    def test_meta_reflects_tier(self):
        day = _make_trajectory_day()
        pkt = build_day_packet(day, tier="full")
        assert pkt.meta.budget_tier == "full"

    def test_highlights_list(self):
        day = _make_trajectory_day(highlights=["feat: added replay", "debug: fixed crash"])
        pkt = build_day_packet(day)
        assert pkt.highlights == ["feat: added replay", "debug: fixed crash"]


# ---------------------------------------------------------------------------
# build_week_packet
# ---------------------------------------------------------------------------

class TestBuildWeekPacket:
    def test_returns_week_packet(self):
        week = _make_trajectory_week()
        pkt = build_week_packet(week)
        assert isinstance(pkt, WeekPacket)

    def test_iso_week_key(self):
        week = _make_trajectory_week(iso_week="2026-W10")
        pkt = build_week_packet(week)
        assert pkt.iso_week == "2026-W10"

    def test_date_range_isoformat(self):
        week = _make_trajectory_week(
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 8),
        )
        pkt = build_week_packet(week)
        assert pkt.start_date == "2026-03-02"
        assert pkt.end_date == "2026-03-08"

    def test_hours_conversion(self):
        week = _make_trajectory_week(active_seconds=36000.0)
        pkt = build_week_packet(week)
        assert pkt.active_hours == pytest.approx(10.0)

    def test_dominant_fields(self):
        week = _make_trajectory_week()
        pkt = build_week_packet(week)
        assert pkt.dominant_mode == "coding"
        assert pkt.dominant_project == "sinex"
        assert pkt.dominant_topic == "rust"

    def test_active_delta_none_when_absent(self):
        week = _make_trajectory_week(active_delta_vs_prior=None)
        pkt = build_week_packet(week)
        assert pkt.active_delta_vs_prior is None

    def test_active_delta_converted_to_hours(self):
        week = _make_trajectory_week(active_delta_vs_prior=7200.0)
        pkt = build_week_packet(week)
        assert pkt.active_delta_vs_prior == pytest.approx(2.0)

    def test_to_dict_is_serializable(self):
        week = _make_trajectory_week()
        d = build_week_packet(week).to_dict()
        json.dumps(d)
        assert "iso_week" in d
        assert "dominant_mode" in d


# ---------------------------------------------------------------------------
# build_month_packet
# ---------------------------------------------------------------------------

class TestBuildMonthPacket:
    def test_returns_month_packet(self):
        month = _make_trajectory_month()
        pkt = build_month_packet(month)
        assert isinstance(pkt, MonthPacket)

    def test_month_key(self):
        month = _make_trajectory_month(month="2026-02")
        pkt = build_month_packet(month)
        assert pkt.month == "2026-02"

    def test_hours_conversion(self):
        month = _make_trajectory_month(active_seconds=360000.0)
        pkt = build_month_packet(month)
        assert pkt.active_hours == pytest.approx(100.0)

    def test_chat_fields_populated(self):
        month = _make_trajectory_month(chat_session_count=30, chat_cost_usd=4.5)
        pkt = build_month_packet(month)
        assert pkt.chat_session_count == 30
        assert pkt.chat_cost_usd == pytest.approx(4.5)

    def test_episode_labels(self):
        month = _make_trajectory_month(episode_count=2, episode_labels=["ep-a", "ep-b"])
        pkt = build_month_packet(month)
        assert pkt.episode_count == 2
        assert "ep-a" in pkt.episode_labels

    def test_to_dict_serializable(self):
        month = _make_trajectory_month()
        d = build_month_packet(month).to_dict()
        json.dumps(d)
        assert "month" in d
        assert "active_hours" in d


# ---------------------------------------------------------------------------
# build_episode_packet
# ---------------------------------------------------------------------------

class TestBuildEpisodePacket:
    def test_returns_episode_packet(self):
        ep = _make_trajectory_episode()
        pkt = build_episode_packet(ep)
        assert isinstance(pkt, EpisodePacket)

    def test_episode_id_and_label(self):
        ep = _make_trajectory_episode(episode_id="xyz999", label="test-ep")
        pkt = build_episode_packet(ep)
        assert pkt.episode_id == "xyz999"
        assert pkt.label == "test-ep"

    def test_date_range_isoformat(self):
        ep = _make_trajectory_episode(
            start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 10),
        )
        pkt = build_episode_packet(ep)
        assert pkt.start_date == "2026-02-01"
        assert pkt.end_date == "2026-02-10"

    def test_hours_conversion(self):
        ep = _make_trajectory_episode(active_seconds=36000.0)
        pkt = build_episode_packet(ep)
        assert pkt.active_hours == pytest.approx(10.0)

    def test_confidence(self):
        ep = _make_trajectory_episode(confidence=0.92)
        pkt = build_episode_packet(ep)
        assert pkt.confidence == pytest.approx(0.92)

    def test_to_dict_serializable(self):
        ep = _make_trajectory_episode()
        d = build_episode_packet(ep).to_dict()
        json.dumps(d)
        assert "episode_id" in d
        assert "trigger" in d
        assert "confidence" in d


# ---------------------------------------------------------------------------
# build_project_packet
# ---------------------------------------------------------------------------

class TestBuildProjectPacket:
    def test_returns_project_packet(self):
        day = _make_trajectory_day()
        chain = _make_chain()
        pkt = build_project_packet("sinex", [day], [chain])
        assert isinstance(pkt, ProjectPacket)

    def test_counts_hours_from_matching_days(self):
        day = _make_trajectory_day(
            top_projects=(("sinex", 7200.0), ("lynchpin", 3600.0)),
        )
        pkt = build_project_packet("sinex", [day], [])
        assert pkt.total_hours == pytest.approx(2.0)

    def test_zero_hours_for_nonexistent_project(self):
        day = _make_trajectory_day()
        pkt = build_project_packet("nonexistent", [day], [])
        assert pkt.total_hours == 0.0

    def test_chain_count_from_matching_chains(self):
        chains = [_make_chain(chain_id=f"c{i}", project="sinex") for i in range(3)]
        pkt = build_project_packet("sinex", [], chains)
        assert pkt.chain_count == 3

    def test_to_dict_serializable(self):
        day = _make_trajectory_day()
        d = build_project_packet("sinex", [day], []).to_dict()
        json.dumps(d)
        assert "project" in d
        assert "total_hours" in d


# ---------------------------------------------------------------------------
# build_coverage_packet
# ---------------------------------------------------------------------------

class TestBuildCoveragePacket:
    def _make_day_with_sources(self, sources: dict) -> SimpleNamespace:
        return SimpleNamespace(
            date=date(2026, 3, 10),
            source_counts=sources,
            signal_count=10,
            chain_count=2,
            active_seconds=14400.0,
            recovery_seconds=3600.0,
            coverage={
                "has_activitywatch": "activitywatch.window" in sources,
                "has_terminal": "instrumentation.terminal" in sources,
                "has_chatlog": "polylogue.session" in sources,
                "has_git": "git.commit" in sources,
            },
        )

    def test_basic_field_presence(self):
        days = [self._make_day_with_sources({"activitywatch.window": 1})]
        from lynchpin.context.packet_types import CoveragePacket
        pkt = build_coverage_packet(days)
        assert isinstance(pkt, CoveragePacket)

    def test_day_count(self):
        days = [self._make_day_with_sources({}) for _ in range(5)]
        pkt = build_coverage_packet(days)
        assert pkt.day_count == 5

    def test_activitywatch_count(self):
        days = [
            self._make_day_with_sources({"activitywatch.window": 1}),
            self._make_day_with_sources({}),
            self._make_day_with_sources({"activitywatch.window": 1}),
        ]
        pkt = build_coverage_packet(days)
        assert pkt.days_with_activitywatch == 2

    def test_git_count(self):
        days = [
            self._make_day_with_sources({"git.commit": 3}),
            self._make_day_with_sources({"git.commit": 1}),
            self._make_day_with_sources({}),
        ]
        pkt = build_coverage_packet(days)
        assert pkt.days_with_git == 2

    def test_anomaly_count_passed_through(self):
        days = [self._make_day_with_sources({})]
        pkt = build_coverage_packet(days, anomaly_count=7)
        assert pkt.anomaly_count == 7

    def test_to_dict_serializable(self):
        days = [self._make_day_with_sources({"activitywatch.window": 1, "git.commit": 2})]
        d = build_coverage_packet(days).to_dict()
        json.dumps(d)
        assert "day_count" in d
        assert "anomaly_count" in d
        assert "days_with_activitywatch" in d
