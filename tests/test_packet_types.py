"""Direct tests for packet_types.py to_dict() serialization methods.

These verify the serialization contract for every packet type:
JSON serializability, required field presence, rounding, and None handling.
"""

from __future__ import annotations

import json

import pytest

from lynchpin.context.packet_types import (
    ClaimPacket,
    ClaimsPacket,
    ContextPacketMeta,
    CoveragePacket,
    DayPacket,
    EpisodePacket,
    MonthPacket,
    ProjectArcPacket,
    ProjectPacket,
    QuarterPacket,
    ThemePacket,
    ThreadPacket,
    WeekPacket,
    YearPacket,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _meta(tier: str = "standard") -> ContextPacketMeta:
    return ContextPacketMeta(schema="v1", generated_at="2026-03-17T10:00:00Z", budget_tier=tier)


def _is_json_serializable(obj: object) -> bool:
    try:
        json.dumps(obj)
        return True
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# DayPacket
# ---------------------------------------------------------------------------

class TestDayPacketToDict:
    def _packet(self) -> DayPacket:
        return DayPacket(
            meta=_meta(),
            date="2026-03-17",
            active_hours=6.5,
            recovery_hours=1.0,
            dominant_mode="coding",
            dominant_project="sinity-lynchpin",
            dominant_topic="rust",
            chain_count=12,
            signal_count=80,
            command_count=30,
            transcript_count=2,
            commit_count=3,
            top_modes=[("coding", 3600.0), ("research", 1800.0)],
            top_projects=[("sinity-lynchpin", 5400.0)],
            top_topics=[("rust", 2700.0)],
            highlights=["built cargo workspace"],
        )

    def test_json_serializable(self) -> None:
        assert _is_json_serializable(self._packet().to_dict())

    def test_required_fields_present(self) -> None:
        d = self._packet().to_dict()
        for key in ("meta", "date", "active_hours", "chain_count", "signal_count",
                    "dominant_mode", "top_modes", "top_projects", "top_topics", "highlights"):
            assert key in d

    def test_meta_is_nested_dict(self) -> None:
        d = self._packet().to_dict()
        assert isinstance(d["meta"], dict)
        assert d["meta"]["schema"] == "v1"
        assert d["meta"]["budget_tier"] == "standard"

    def test_top_modes_are_lists_not_tuples(self) -> None:
        d = self._packet().to_dict()
        assert isinstance(d["top_modes"][0], list)

    def test_none_dominant_mode_passes_through(self) -> None:
        p = DayPacket(
            meta=_meta(), date="2026-03-17", active_hours=0.0, recovery_hours=0.0,
            dominant_mode=None, dominant_project=None, dominant_topic=None,
            chain_count=0, signal_count=0, command_count=0, transcript_count=0, commit_count=0,
            top_modes=[], top_projects=[], top_topics=[], highlights=[],
        )
        d = p.to_dict()
        assert d["dominant_mode"] is None
        assert d["dominant_project"] is None


# ---------------------------------------------------------------------------
# WeekPacket
# ---------------------------------------------------------------------------

class TestWeekPacketToDict:
    def _packet(self, *, delta: float | None = 2.5) -> WeekPacket:
        return WeekPacket(
            meta=_meta(),
            iso_week="2026-W11",
            start_date="2026-03-09",
            end_date="2026-03-15",
            active_hours=30.0,
            recovery_hours=5.0,
            day_pattern="MTWT...",
            chain_count=50,
            top_modes=[("coding", 18000.0)],
            top_projects=[("sinex", 10000.0)],
            top_topics=[("rust", 9000.0)],
            active_delta_vs_prior=delta,
        )

    def test_json_serializable(self) -> None:
        assert _is_json_serializable(self._packet().to_dict())

    def test_required_fields_present(self) -> None:
        d = self._packet().to_dict()
        for key in ("meta", "iso_week", "start_date", "end_date", "active_hours",
                    "day_pattern", "chain_count", "top_modes", "active_delta_vs_prior"):
            assert key in d

    def test_active_delta_rounded(self) -> None:
        d = self._packet(delta=1.23456).to_dict()
        assert d["active_delta_vs_prior"] == round(1.23456, 2)

    def test_active_delta_none_passes_through(self) -> None:
        d = self._packet(delta=None).to_dict()
        assert d["active_delta_vs_prior"] is None

    def test_top_lists_are_two_element_lists(self) -> None:
        d = self._packet().to_dict()
        for row in d["top_modes"]:
            assert isinstance(row, list)
            assert len(row) == 2


# ---------------------------------------------------------------------------
# MonthPacket
# ---------------------------------------------------------------------------

class TestMonthPacketToDict:
    def _packet(self) -> MonthPacket:
        return MonthPacket(
            meta=_meta(),
            month="2026-03",
            active_hours=120.0,
            recovery_hours=20.0,
            active_days=22,
            chain_count=200,
            signal_count=1500,
            dominant_modes=[("coding", 80000.0)],
            dominant_projects=[("sinity-lynchpin", 50000.0)],
            dominant_topics=[("rust", 30000.0)],
            highlights=["completed sprint 4"],
            chat_session_count=15,
            chat_work_events={"implementation": 8, "research": 4},
            chat_cost_usd=0.12345,
            episode_count=2,
            episode_labels=["deep-rust-sprint"],
        )

    def test_json_serializable(self) -> None:
        assert _is_json_serializable(self._packet().to_dict())

    def test_required_fields_present(self) -> None:
        d = self._packet().to_dict()
        for key in ("meta", "month", "active_hours", "active_days", "chain_count",
                    "dominant_modes", "chat_session_count", "chat_cost_usd",
                    "episode_count", "episode_labels"):
            assert key in d

    def test_chat_cost_rounded_to_4dp(self) -> None:
        d = self._packet().to_dict()
        assert d["chat_cost_usd"] == round(0.12345, 4)

    def test_none_defaults_become_empty_collections(self) -> None:
        # MonthPacket with None chat_work_events/episode_labels should default to {} / []
        p = MonthPacket(
            meta=_meta(), month="2026-03", active_hours=0.0, recovery_hours=0.0,
            active_days=0, chain_count=0, signal_count=0,
            dominant_modes=[], dominant_projects=[], dominant_topics=[], highlights=[],
        )
        d = p.to_dict()
        assert d["chat_work_events"] == {}
        assert d["episode_labels"] == []


# ---------------------------------------------------------------------------
# EpisodePacket
# ---------------------------------------------------------------------------

class TestEpisodePacketToDict:
    def _packet(self) -> EpisodePacket:
        return EpisodePacket(
            meta=_meta(),
            episode_id="ep-abc123",
            label="rust-sprint",
            start_date="2026-03-01",
            end_date="2026-03-07",
            days=7,
            active_hours=42.0,
            dominant_mode="coding",
            dominant_project="sinex",
            dominant_topic="rust",
            trigger="project_shift",
            confidence=0.876543,
        )

    def test_json_serializable(self) -> None:
        assert _is_json_serializable(self._packet().to_dict())

    def test_required_fields_present(self) -> None:
        d = self._packet().to_dict()
        for key in ("meta", "episode_id", "label", "start_date", "end_date",
                    "days", "active_hours", "trigger", "confidence"):
            assert key in d

    def test_confidence_rounded_to_3dp(self) -> None:
        d = self._packet().to_dict()
        assert d["confidence"] == round(0.876543, 3)

    def test_none_dominant_fields_pass_through(self) -> None:
        p = EpisodePacket(
            meta=_meta(), episode_id="x", label="unknown", start_date="2026-03-01",
            end_date="2026-03-01", days=1, active_hours=0.0,
            dominant_mode=None, dominant_project=None, dominant_topic=None,
            trigger="anomaly_cluster", confidence=0.5,
        )
        d = p.to_dict()
        assert d["dominant_mode"] is None
        assert d["dominant_project"] is None
        assert d["dominant_topic"] is None


# ---------------------------------------------------------------------------
# ProjectPacket
# ---------------------------------------------------------------------------

class TestProjectPacketToDict:
    def _packet(self) -> ProjectPacket:
        return ProjectPacket(
            meta=_meta(),
            project="sinity-lynchpin",
            total_hours=45.0,
            day_count=10,
            chain_count=80,
            top_modes=[("coding", 120000.0), ("research", 30000.0)],
        )

    def test_json_serializable(self) -> None:
        assert _is_json_serializable(self._packet().to_dict())

    def test_required_fields_present(self) -> None:
        d = self._packet().to_dict()
        for key in ("meta", "project", "total_hours", "day_count", "chain_count", "top_modes"):
            assert key in d

    def test_top_modes_rounded(self) -> None:
        d = self._packet().to_dict()
        assert d["top_modes"][0] == ["coding", round(120000.0, 2)]

    def test_empty_top_modes(self) -> None:
        p = ProjectPacket(meta=_meta(), project="x", total_hours=0.0, day_count=0, chain_count=0, top_modes=[])
        d = p.to_dict()
        assert d["top_modes"] == []


# ---------------------------------------------------------------------------
# ThreadPacket
# ---------------------------------------------------------------------------

class TestThreadPacketToDict:
    def _packet(self) -> ThreadPacket:
        return ThreadPacket(
            meta=_meta(),
            thread_id="thread-abc",
            depth=5,
            session_count=3,
            start_date="2026-03-10",
            end_date="2026-03-12",
            dominant_project="sinity-lynchpin",
            work_event_breakdown={"implementation": 2, "research": 1},
            total_cost_usd=0.043219,
        )

    def test_json_serializable(self) -> None:
        assert _is_json_serializable(self._packet().to_dict())

    def test_required_fields_present(self) -> None:
        d = self._packet().to_dict()
        for key in ("meta", "thread_id", "depth", "session_count", "start_date",
                    "end_date", "dominant_project", "work_event_breakdown", "total_cost_usd"):
            assert key in d

    def test_cost_rounded_to_4dp(self) -> None:
        d = self._packet().to_dict()
        assert d["total_cost_usd"] == round(0.043219, 4)

    def test_none_dominant_project(self) -> None:
        p = ThreadPacket(
            meta=_meta(), thread_id="x", depth=0, session_count=1,
            start_date="2026-03-10", end_date="2026-03-10",
            dominant_project=None, work_event_breakdown={}, total_cost_usd=0.0,
        )
        assert p.to_dict()["dominant_project"] is None


# ---------------------------------------------------------------------------
# CoveragePacket
# ---------------------------------------------------------------------------

class TestCoveragePacketToDict:
    def _packet(self) -> CoveragePacket:
        return CoveragePacket(
            meta=_meta(),
            day_count=7,
            signal_count=350,
            chain_count=45,
            source_breakdown={"atuin.command": 200, "git.commit": 50},
            days_with_activitywatch=7,
            days_with_terminal=6,
            days_with_chatlog=5,
            days_with_git=4,
            anomaly_count=2,
        )

    def test_json_serializable(self) -> None:
        assert _is_json_serializable(self._packet().to_dict())

    def test_required_fields_present(self) -> None:
        d = self._packet().to_dict()
        for key in ("meta", "day_count", "signal_count", "chain_count", "source_breakdown",
                    "days_with_activitywatch", "days_with_terminal", "days_with_chatlog",
                    "days_with_git", "anomaly_count"):
            assert key in d

    def test_anomaly_count_defaults_zero(self) -> None:
        p = CoveragePacket(
            meta=_meta(), day_count=1, signal_count=0, chain_count=0,
            source_breakdown={}, days_with_activitywatch=0,
            days_with_terminal=0, days_with_chatlog=0, days_with_git=0,
        )
        assert p.to_dict()["anomaly_count"] == 0


# ---------------------------------------------------------------------------
# QuarterPacket
# ---------------------------------------------------------------------------

class TestQuarterPacketToDict:
    def _packet(self, *, delta: float | None = 5.0) -> QuarterPacket:
        return QuarterPacket(
            meta=_meta(),
            quarter="2026-Q1",
            active_hours=320.0,
            recovery_hours=50.0,
            active_days=60,
            chain_count=600,
            signal_count=4500,
            dominant_mode="coding",
            dominant_project="sinex",
            dominant_topic="rust",
            top_modes=[("coding", 900000.0)],
            top_projects=[("sinex", 500000.0)],
            top_topics=[("rust", 300000.0)],
            chat_session_count=40,
            chat_cost_usd=0.38765,
            episode_count=6,
            month_count=3,
            month_active_trend=[100.0, 110.0, 110.0],
            active_delta_vs_prior=delta,
        )

    def test_json_serializable(self) -> None:
        assert _is_json_serializable(self._packet().to_dict())

    def test_required_fields_present(self) -> None:
        d = self._packet().to_dict()
        for key in ("meta", "quarter", "active_hours", "active_days", "chain_count",
                    "top_modes", "top_projects", "top_topics", "month_active_trend",
                    "chat_cost_usd", "active_delta_vs_prior"):
            assert key in d

    def test_month_active_trend_rounded(self) -> None:
        d = self._packet().to_dict()
        assert all(isinstance(v, float) for v in d["month_active_trend"])

    def test_active_delta_none(self) -> None:
        d = self._packet(delta=None).to_dict()
        assert d["active_delta_vs_prior"] is None

    def test_chat_cost_rounded_4dp(self) -> None:
        d = self._packet().to_dict()
        assert d["chat_cost_usd"] == round(0.38765, 4)


# ---------------------------------------------------------------------------
# YearPacket
# ---------------------------------------------------------------------------

class TestYearPacketToDict:
    def _packet(self, *, delta: float | None = 10.0) -> YearPacket:
        return YearPacket(
            meta=_meta(),
            year="2026",
            active_hours=1400.0,
            recovery_hours=200.0,
            active_days=240,
            chain_count=2500,
            signal_count=18000,
            dominant_mode="coding",
            dominant_project="sinex",
            dominant_topic="rust",
            top_modes=[("coding", 3600000.0)],
            top_projects=[("sinex", 2000000.0)],
            top_topics=[("rust", 1200000.0)],
            chat_session_count=160,
            chat_cost_usd=1.56789,
            episode_count=24,
            quarter_count=4,
            quarter_active_trend=[340.0, 355.0, 360.0, 345.0],
            active_delta_vs_prior=delta,
        )

    def test_json_serializable(self) -> None:
        assert _is_json_serializable(self._packet().to_dict())

    def test_required_fields_present(self) -> None:
        d = self._packet().to_dict()
        for key in ("meta", "year", "active_hours", "active_days", "chain_count",
                    "top_modes", "top_projects", "top_topics", "quarter_active_trend",
                    "chat_cost_usd", "episode_count", "quarter_count", "active_delta_vs_prior"):
            assert key in d

    def test_quarter_active_trend_is_list_of_floats(self) -> None:
        d = self._packet().to_dict()
        assert isinstance(d["quarter_active_trend"], list)
        assert all(isinstance(v, float) for v in d["quarter_active_trend"])

    def test_active_delta_none(self) -> None:
        d = self._packet(delta=None).to_dict()
        assert d["active_delta_vs_prior"] is None

    def test_chat_cost_rounded_4dp(self) -> None:
        d = self._packet().to_dict()
        assert d["chat_cost_usd"] == round(1.56789, 4)


# ---------------------------------------------------------------------------
# ThemePacket
# ---------------------------------------------------------------------------

class TestThemePacketToDict:
    def _packet(self) -> ThemePacket:
        return ThemePacket(
            meta=_meta(),
            name="sinex",
            kind="project",
            total_hours=85.5,
            month_count=3,
            trend="rising",
            first_seen="2026-01",
            last_seen="2026-03",
        )

    def test_json_serializable(self) -> None:
        assert _is_json_serializable(self._packet().to_dict())

    def test_required_fields_present(self) -> None:
        d = self._packet().to_dict()
        for key in ("meta", "name", "kind", "total_hours", "month_count",
                    "trend", "first_seen", "last_seen"):
            assert key in d

    def test_kind_is_project_or_topic(self) -> None:
        d = self._packet().to_dict()
        assert d["kind"] in ("project", "topic")

    def test_trend_value(self) -> None:
        d = self._packet().to_dict()
        assert d["trend"] in ("rising", "stable", "declining")


# ---------------------------------------------------------------------------
# ClaimPacket
# ---------------------------------------------------------------------------

class TestClaimPacketToDict:
    def _packet(self) -> ClaimPacket:
        return ClaimPacket(
            statement="Primary work focus is Rust systems programming",
            confidence=0.8765432,
            evidence_refs=("git.commit:abc", "atuin.command:xyz"),
            category="work_pattern",
        )

    def test_json_serializable(self) -> None:
        assert _is_json_serializable(self._packet().to_dict())

    def test_required_fields_present(self) -> None:
        d = self._packet().to_dict()
        for key in ("statement", "confidence", "evidence_refs", "category"):
            assert key in d

    def test_confidence_rounded_to_3dp(self) -> None:
        d = self._packet().to_dict()
        assert d["confidence"] == round(0.8765432, 3)

    def test_evidence_refs_is_list_not_tuple(self) -> None:
        d = self._packet().to_dict()
        assert isinstance(d["evidence_refs"], list)

    def test_no_meta_field(self) -> None:
        # ClaimPacket is a leaf node — embedded in ClaimsPacket, has no meta
        d = self._packet().to_dict()
        assert "meta" not in d


# ---------------------------------------------------------------------------
# ClaimsPacket
# ---------------------------------------------------------------------------

class TestClaimsPacketToDict:
    def _packet(self) -> ClaimsPacket:
        claim = ClaimPacket(
            statement="Primary focus is systems engineering",
            confidence=0.85,
            evidence_refs=("git.commit:a",),
            category="work_pattern",
        )
        return ClaimsPacket(meta=_meta(), claims=(claim,))

    def test_json_serializable(self) -> None:
        assert _is_json_serializable(self._packet().to_dict())

    def test_required_fields_present(self) -> None:
        d = self._packet().to_dict()
        assert "meta" in d
        assert "claims" in d

    def test_claims_is_list_of_dicts(self) -> None:
        d = self._packet().to_dict()
        assert isinstance(d["claims"], list)
        assert isinstance(d["claims"][0], dict)

    def test_nested_claim_confidence_rounded(self) -> None:
        d = self._packet().to_dict()
        claim_d = d["claims"][0]
        assert claim_d["confidence"] == round(0.85, 3)

    def test_empty_claims(self) -> None:
        p = ClaimsPacket(meta=_meta(), claims=())
        assert p.to_dict()["claims"] == []


# ---------------------------------------------------------------------------
# ProjectArcPacket
# ---------------------------------------------------------------------------

class TestProjectArcPacketToDict:
    def _packet(self) -> ProjectArcPacket:
        return ProjectArcPacket(
            meta=_meta(),
            project="sinex",
            total_hours=95.0,
            active_months=3,
            velocity_trend="accelerating",
            cost_usd=0.987654,
            active_episodes=4,
            momentum="high",
        )

    def test_json_serializable(self) -> None:
        assert _is_json_serializable(self._packet().to_dict())

    def test_required_fields_present(self) -> None:
        d = self._packet().to_dict()
        for key in ("meta", "project", "total_hours", "active_months", "velocity_trend",
                    "cost_usd", "active_episodes", "momentum"):
            assert key in d

    def test_cost_rounded_to_2dp(self) -> None:
        d = self._packet().to_dict()
        assert d["cost_usd"] == round(0.987654, 2)

    def test_velocity_trend_value(self) -> None:
        d = self._packet().to_dict()
        assert d["velocity_trend"] in ("accelerating", "stalling", "steady")
