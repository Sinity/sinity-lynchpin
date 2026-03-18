"""Tests for context-layer modules: claims generation and memory persistence."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from lynchpin.context.claims import Claim, generate_claims
from lynchpin.context.themes import Theme, detect_themes
from lynchpin.context.memory import (
    ClaimRecord,
    MemoryStore,
    build_memory_packet,
    load_memory,
    save_memory,
    update_memory,
)
from lynchpin.context.packet_builders import (
    _aggregate_chat_work_events,
    _top_n,
    build_claims_packet,
    build_coverage_packet,
    build_project_arc_packets,
    build_theme_packets,
    build_thread_packets,
)
from lynchpin.context.project_arcs import build_project_arcs
from lynchpin.trajectory.day import TrajectoryDay
from lynchpin.trajectory.month import TrajectoryMonth
from lynchpin.trajectory.signal import TrajectorySignal
from lynchpin.trajectory.week import TrajectoryWeek


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_month(
    month: str = "2026-01",
    *,
    active_seconds: float = 36000.0,   # 10h
    recovery_seconds: float = 28800.0,
    active_days: int = 20,
    chat_session_count: int = 0,
    commit_count: int = 0,
    top_modes: tuple[tuple[str, float], ...] = (("coding", 25200.0),),
    top_projects: tuple[tuple[str, float], ...] = (("polylogue", 25200.0),),
    top_topics: tuple[tuple[str, float], ...] = (),
) -> TrajectoryMonth:
    year, mo = month.split("-")
    start = date(int(year), int(mo), 1)
    end = date(int(year), int(mo), 28)
    return TrajectoryMonth(
        month=month,
        start_date=start,
        end_date=end,
        total_days=28,
        active_days=active_days,
        active_seconds=active_seconds,
        recovery_seconds=recovery_seconds,
        chain_count=100,
        signal_count=500,
        command_count=50,
        transcript_count=0,
        commit_count=commit_count,
        dominant_mode="coding",
        dominant_project="polylogue",
        dominant_topic=None,
        top_modes=top_modes,
        top_projects=top_projects,
        top_topics=top_topics,
        source_counts={"atuin.command": 50},
        coverage_summary={"full": 15, "partial": 5},
        highlights=(),
        chat_session_count=chat_session_count,
        chat_work_events={},
        chat_cost_usd=0.0,
        episode_count=0,
        episode_labels=(),
        week_count=4,
        day_patterns=(),
    )


def _make_week(
    iso_week: str = "2026-W10",
    *,
    active_seconds: float = 72000.0,  # 20h
    commit_count: int = 0,
    top_projects: tuple[tuple[str, float], ...] = (("sinex", 72000.0),),
    top_topics: tuple[tuple[str, float], ...] = (),
) -> TrajectoryWeek:
    return TrajectoryWeek(
        iso_week=iso_week,
        start_date=date(2026, 3, 2),
        end_date=date(2026, 3, 8),
        days=5,
        active_seconds=active_seconds,
        recovery_seconds=28800.0,
        chain_count=20,
        signal_count=100,
        command_count=10,
        transcript_count=0,
        commit_count=commit_count,
        top_modes=(("coding", active_seconds),),
        top_projects=top_projects,
        top_topics=top_topics,
        day_pattern="uniform",
        busiest_day=None,
        quietest_day=None,
        active_delta_vs_prior=None,
    )


def _make_day(
    day_date: date,
    *,
    active_seconds: float = 36000.0,
    commit_count: int = 0,
    command_count: int = 5,
) -> TrajectoryDay:
    return TrajectoryDay(
        date=day_date,
        active_seconds=active_seconds,
        recovery_seconds=28800.0,
        chain_count=10,
        signal_count=50,
        command_count=command_count,
        transcript_count=0,
        commit_count=commit_count,
        dominant_mode="coding",
        dominant_project="polylogue",
        dominant_topic=None,
        top_modes=(("coding", active_seconds),),
        top_projects=(("polylogue", active_seconds),),
        top_topics=(),
        source_counts={"atuin.command": command_count},
        coverage={
            "has_activitywatch": True,
            "has_terminal": True,
            "has_chatlog": False,
            "has_git": True,
            "observed_hours": 18.0,
            "sources": ["atuin.command"],
        },
        highlights=(),
        projects=(),
    )


# ---------------------------------------------------------------------------
# generate_claims
# ---------------------------------------------------------------------------

class TestGenerateClaims:
    def test_empty_months_returns_empty(self) -> None:
        assert generate_claims([]) == []

    def test_zero_active_hours_returns_empty(self) -> None:
        month = _make_month(active_seconds=0.0)
        assert generate_claims([month]) == []

    def test_primary_project_claim_above_threshold(self) -> None:
        # polylogue = 9h / 10h = 90% → above 40% threshold
        month = _make_month(
            active_seconds=36000.0,
            top_projects=(("polylogue", 32400.0),),
        )
        claims = generate_claims([month])
        project_claims = [c for c in claims if c.category == "project"]
        assert any("polylogue" in c.statement for c in project_claims)

    def test_primary_project_claim_below_threshold_absent(self) -> None:
        # polylogue = 3h / 10h = 30% → below 40% threshold
        month = _make_month(
            active_seconds=36000.0,
            top_projects=(("polylogue", 10800.0), ("other", 10800.0), ("third", 7200.0)),
        )
        claims = generate_claims([month])
        project_claims = [c for c in claims if c.category == "project" and "Primary project" in c.statement]
        assert len(project_claims) == 0

    def test_dominant_mode_excludes_recovery(self) -> None:
        # recovery appears as top mode but must be excluded
        month = _make_month(
            active_seconds=36000.0,
            top_modes=(("recovery", 50000.0), ("coding", 25200.0)),
        )
        claims = generate_claims([month])
        mode_claims = [c for c in claims if c.category == "mode" and "Dominant mode" in c.statement]
        # "recovery" must never appear as dominant mode claim
        assert not any("recovery" in c.statement for c in mode_claims)
        # coding at 25200s / 36000s = 70% > 20% → should appear
        assert any("coding" in c.statement for c in mode_claims)

    def test_dominant_mode_below_threshold_absent(self) -> None:
        # 5% coding — well below 20% threshold
        month = _make_month(
            active_seconds=360000.0,  # 100h
            top_modes=(("coding", 1800.0),),  # 0.5h = 0.5%
        )
        claims = generate_claims([month])
        mode_claims = [c for c in claims if "Dominant mode" in c.statement]
        assert len(mode_claims) == 0

    def test_chat_heavy_workflow_claim(self) -> None:
        # 30 sessions over 10 active days = 3.0 sessions/day > 2.0 threshold
        month = _make_month(active_days=10, chat_session_count=30)
        claims = generate_claims([month])
        workflow_claims = [c for c in claims if c.category == "workflow" and "Chat-heavy" in c.statement]
        assert len(workflow_claims) == 1
        assert "3.0" in workflow_claims[0].statement

    def test_chat_heavy_absent_when_below_threshold(self) -> None:
        # 1 session per active day — below 2.0 threshold
        month = _make_month(active_days=20, chat_session_count=15)
        claims = generate_claims([month])
        assert not any("Chat-heavy" in c.statement for c in claims)

    def test_rising_project_claim_needs_three_months(self) -> None:
        # Two months → no rising claim (need >= 3)
        m1 = _make_month("2026-01", top_projects=(("sinex", 36000.0),))
        m2 = _make_month("2026-02", top_projects=(("sinex", 36000.0),))
        claims = generate_claims([m1, m2])
        assert not any("rising" in c.statement for c in claims)

    def test_rising_project_claim_with_three_months(self) -> None:
        # sinex: 40h in recent 2 months, 0h in earlier — qualifies as rising
        earlier = _make_month("2026-01", active_seconds=72000.0,
                               top_projects=(("other", 72000.0),))
        m2 = _make_month("2026-02", active_seconds=72000.0,
                         top_projects=(("sinex", 72000.0),))
        m3 = _make_month("2026-03", active_seconds=72000.0,
                         top_projects=(("sinex", 72000.0),))
        claims = generate_claims([earlier, m2, m3])
        rising = [c for c in claims if "rising" in c.statement and "sinex" in c.statement]
        assert len(rising) == 1

    def test_primary_topic_claim_above_threshold(self) -> None:
        # rust: 25h / 30h total = 83% > 30% and > 20h
        month = _make_month(
            active_seconds=108000.0,  # 30h
            top_topics=(("rust", 90000.0), ("python", 18000.0)),  # 25h, 5h
        )
        claims = generate_claims([month])
        topic_claims = [c for c in claims if "Dominant topic" in c.statement]
        assert len(topic_claims) == 1
        assert "rust" in topic_claims[0].statement

    def test_primary_topic_absent_when_under_hour_threshold(self) -> None:
        # rust: 80% but only 10h (< 20h minimum)
        month = _make_month(
            active_seconds=108000.0,
            top_topics=(("rust", 28800.0), ("python", 7200.0)),  # 8h, 2h
        )
        claims = generate_claims([month])
        topic_claims = [c for c in claims if "Dominant topic" in c.statement]
        assert len(topic_claims) == 0

    def test_high_commit_velocity_claim(self) -> None:
        # 6 commits/active day > 5 threshold
        days = [
            _make_day(date(2026, 3, d), commit_count=6, active_seconds=36000.0)
            for d in range(1, 11)
        ]
        month = _make_month()
        claims = generate_claims([month], days=days)
        velocity = [c for c in claims if "commit velocity" in c.statement]
        assert len(velocity) == 1
        assert "6.0" in velocity[0].statement

    def test_high_commit_velocity_absent_below_threshold(self) -> None:
        # 2 commits/active day < 5 threshold
        days = [
            _make_day(date(2026, 3, d), commit_count=2, active_seconds=36000.0)
            for d in range(1, 11)
        ]
        month = _make_month()
        claims = generate_claims([month], days=days)
        assert not any("commit velocity" in c.statement for c in claims)

    def test_irregular_schedule_claim(self) -> None:
        # Alternate 1h and 11h days over 14 days → std_dev = 5h, mean = 6h, std > 0.4*mean ✓
        days = (
            [_make_day(date(2026, 1, d), active_seconds=3600.0) for d in range(1, 8)]
            + [_make_day(date(2026, 1, d + 7), active_seconds=39600.0) for d in range(1, 8)]
        )
        month = _make_month()
        claims = generate_claims([month], days=days)
        irregular = [c for c in claims if "Irregular schedule" in c.statement]
        assert len(irregular) == 1

    def test_weekday_pattern_claim(self) -> None:
        # 2026-03-02 = Monday; build exact Mon-Fri (10h) and Sat-Sun (1h) over 2 weeks
        # Weekdays: Mar 2-6, Mar 9-13 (10 days × 10h); Weekends: Mar 7-8, Mar 14-15 (4 days × 1h)
        # weekday_avg = 10h, weekend_avg = 1h → 10 > 1*1.5 → claim triggered
        weekdays = [2, 3, 4, 5, 6, 9, 10, 11, 12, 13]
        weekends = [7, 8, 14, 15]
        days = (
            [_make_day(date(2026, 3, d), active_seconds=36000.0) for d in weekdays]
            + [_make_day(date(2026, 3, d), active_seconds=3600.0) for d in weekends]
        )
        month = _make_month()
        claims = generate_claims([month], days=days)
        rhythm = [c for c in claims if "weekdays" in c.statement]
        assert len(rhythm) == 1

    def test_week_level_peak_week_claim(self) -> None:
        # Peak week = 60h, others = 20h → 60 > 20*1.5 = 30 → fires
        weeks = [
            _make_week("2026-W01", active_seconds=72000.0),   # 20h
            _make_week("2026-W02", active_seconds=72000.0),   # 20h
            _make_week("2026-W03", active_seconds=216000.0),  # 60h — peak
        ]
        month = _make_month(active_seconds=216000.0, top_projects=(("sinex", 216000.0),))
        claims = generate_claims([month], weeks=weeks)
        peak_claims = [c for c in claims if "Peak week" in c.statement]
        assert len(peak_claims) == 1
        assert "W03" in peak_claims[0].statement

    def test_week_level_peak_week_absent_when_not_prominent(self) -> None:
        # All weeks similar (20h each) → no peak claim
        weeks = [_make_week(f"2026-W0{i}", active_seconds=72000.0) for i in range(1, 5)]
        month = _make_month(active_seconds=72000.0)
        claims = generate_claims([month], weeks=weeks)
        assert not any("Peak week" in c.statement for c in claims)

    def test_week_level_consistent_output_claim(self) -> None:
        # All weeks 20h exactly → std=0, consistency=1.0 → fires (mean=20 >= 20)
        weeks = [_make_week(f"2026-W{10+i}", active_seconds=72000.0) for i in range(5)]
        month = _make_month(active_seconds=72000.0)
        claims = generate_claims([month], weeks=weeks)
        consistent = [c for c in claims if "Consistent weekly output" in c.statement]
        assert len(consistent) == 1

    def test_week_level_upward_trend_claim(self) -> None:
        # First 2 weeks: 10h each, last 2: 30h each → second_avg(30) > first_avg(10)*1.25
        weeks = [
            _make_week("2026-W01", active_seconds=36000.0),   # 10h
            _make_week("2026-W02", active_seconds=36000.0),   # 10h
            _make_week("2026-W03", active_seconds=108000.0),  # 30h
            _make_week("2026-W04", active_seconds=108000.0),  # 30h
        ]
        month = _make_month(active_seconds=72000.0)
        claims = generate_claims([month], weeks=weeks)
        rising = [c for c in claims if "Rising weekly output" in c.statement]
        assert len(rising) == 1
        assert "10h" in rising[0].statement
        assert "30h" in rising[0].statement

    def test_week_level_declining_trend_claim(self) -> None:
        # First 2 weeks: 30h each, last 2: 10h each → second_avg(10) < first_avg(30)*0.75
        weeks = [
            _make_week("2026-W01", active_seconds=108000.0),  # 30h
            _make_week("2026-W02", active_seconds=108000.0),  # 30h
            _make_week("2026-W03", active_seconds=36000.0),   # 10h
            _make_week("2026-W04", active_seconds=36000.0),   # 10h
        ]
        month = _make_month(active_seconds=72000.0)
        claims = generate_claims([month], weeks=weeks)
        declining = [c for c in claims if "Declining weekly output" in c.statement]
        assert len(declining) == 1

    def test_claims_sorted_by_confidence_descending(self) -> None:
        month = _make_month(
            active_seconds=36000.0,
            chat_session_count=30,
            active_days=10,
            top_projects=(("polylogue", 32400.0),),
        )
        claims = generate_claims([month])
        confidences = [c.confidence for c in claims]
        assert confidences == sorted(confidences, reverse=True)

    def test_confidence_in_valid_range(self) -> None:
        month = _make_month(
            active_seconds=36000.0,
            chat_session_count=30,
            active_days=10,
            top_projects=(("polylogue", 32400.0),),
            top_modes=(("coding", 25200.0),),
        )
        claims = generate_claims([month])
        for claim in claims:
            assert 0.5 <= claim.confidence <= 0.95, f"Out of range: {claim.confidence} for {claim.statement}"


# ---------------------------------------------------------------------------
# update_memory + build_memory_packet
# ---------------------------------------------------------------------------

class TestMemory:
    @pytest.fixture(autouse=True)
    def _patch_memory_path(self, monkeypatch, tmp_path):
        """Redirect _MEMORY_PATH to a temp dir so tests don't touch artefacts/."""
        import lynchpin.context.memory as mem_module
        monkeypatch.setattr(mem_module, "_MEMORY_PATH", tmp_path / "memory.json")

    def test_load_returns_empty_store_when_missing(self) -> None:
        store = load_memory()
        assert store.claims == []
        assert store.themes == []

    def test_save_and_load_roundtrip(self) -> None:
        store = MemoryStore(
            claims=[ClaimRecord(
                statement="test claim",
                confidence=0.8,
                category="workflow",
                first_seen="2026-01-01",
                last_seen="2026-01-15",
            )],
            themes=[],
            last_updated="2026-01-15T00:00:00+00:00",
            version=1,
        )
        save_memory(store)
        loaded = load_memory()
        assert len(loaded.claims) == 1
        assert loaded.claims[0].statement == "test claim"
        assert loaded.claims[0].confidence == 0.8

    def test_update_adds_new_claim(self) -> None:
        claim = Claim(
            statement="Chat-heavy workflow (3.0 sessions/active day)",
            confidence=0.75,
            evidence_refs=("chat_sessions",),
            category="workflow",
        )
        store = update_memory([claim], [])
        assert len(store.claims) == 1
        assert store.claims[0].statement == claim.statement
        assert store.claims[0].support_count == 1
        assert store.claims[0].confidence == 0.75

    def test_update_blends_confidence_via_ema(self) -> None:
        # First run: confidence = 0.8
        claim_v1 = Claim("test claim", 0.8, ("ref",), "workflow")
        store = update_memory([claim_v1], [])
        assert store.claims[0].confidence == 0.8

        # Second run: new_conf=0.6, alpha=0.3 → 0.3*0.6 + 0.7*0.8 = 0.74
        claim_v2 = Claim("test claim", 0.6, ("ref",), "workflow")
        store2 = update_memory([claim_v2], [], alpha=0.3)
        assert abs(store2.claims[0].confidence - 0.74) < 1e-9

    def test_update_increments_support_count(self) -> None:
        claim = Claim("test claim", 0.7, (), "project")
        update_memory([claim], [])
        store = update_memory([claim], [])
        assert store.claims[0].support_count == 2

    def test_update_records_revision_on_large_delta(self) -> None:
        # Initial confidence = 0.9; update with 0.5 → delta > 0.05
        claim_v1 = Claim("volatile claim", 0.9, (), "workflow")
        update_memory([claim_v1], [])

        claim_v2 = Claim("volatile claim", 0.5, (), "workflow")
        store = update_memory([claim_v2], [], alpha=0.3)
        # 0.3*0.5 + 0.7*0.9 = 0.15 + 0.63 = 0.78; delta from 0.9 = 0.12 > 0.05
        assert len(store.claims[0].revisions) == 1

    def test_update_skips_revision_on_small_delta(self) -> None:
        # Initial and updated confidence are very close
        claim_v1 = Claim("stable claim", 0.8, (), "workflow")
        update_memory([claim_v1], [])

        # With alpha=0.3: 0.3*0.79 + 0.7*0.8 = 0.237 + 0.56 = 0.797; delta = 0.003 < 0.05
        claim_v2 = Claim("stable claim", 0.79, (), "workflow")
        store = update_memory([claim_v2], [], alpha=0.3)
        assert len(store.claims[0].revisions) == 0

    def test_update_merges_evidence_refs(self) -> None:
        claim_v1 = Claim("test", 0.7, ("ref_a",), "project")
        update_memory([claim_v1], [])
        claim_v2 = Claim("test", 0.7, ("ref_b",), "project")
        store = update_memory([claim_v2], [])
        refs = set(store.claims[0].evidence_refs)
        assert "ref_a" in refs
        assert "ref_b" in refs

    def test_update_adds_new_theme(self) -> None:
        theme = Theme(
            name="sinex",
            kind="project",
            total_hours=120.0,
            month_count=3,
            trend="rising",
            first_seen="2026-01",
            last_seen="2026-03",
        )
        store = update_memory([], [theme])
        assert len(store.themes) == 1
        assert store.themes[0].name == "sinex"
        assert store.themes[0].months_active == 3

    def test_update_merges_existing_theme(self) -> None:
        # Theme field order: name, kind, total_hours, month_count, trend, first_seen, last_seen
        theme_v1 = Theme("sinex", "project", 80.0, 2, "rising", "2026-01", "2026-02")
        update_memory([], [theme_v1])
        theme_v2 = Theme("sinex", "project", 120.0, 3, "stable", "2026-01", "2026-03")
        store = update_memory([], [theme_v2])
        assert len(store.themes) == 1
        assert store.themes[0].total_hours == 120.0
        assert store.themes[0].months_active == 3

    def test_build_memory_packet_top_n(self) -> None:
        claims = [
            ClaimRecord("claim A", 0.9, "workflow", "2026-01-01", "2026-01-15"),
            ClaimRecord("claim B", 0.7, "project", "2026-01-01", "2026-01-15"),
            ClaimRecord("claim C", 0.6, "rhythm", "2026-01-01", "2026-01-15"),
        ]
        store = MemoryStore(claims=claims)
        packet = build_memory_packet(store, top_n=2)
        assert len(packet) == 2
        assert packet[0]["statement"] == "claim A"
        assert packet[1]["statement"] == "claim B"

    def test_build_memory_packet_includes_expected_fields(self) -> None:
        record = ClaimRecord("test", 0.75, "workflow", "2026-01-01", "2026-01-15", support_count=3)
        store = MemoryStore(claims=[record])
        packet = build_memory_packet(store)
        assert len(packet) == 1
        item = packet[0]
        assert "statement" in item
        assert "confidence" in item
        assert "category" in item
        assert "age_days" in item
        assert "support_count" in item
        assert item["confidence"] == round(item["confidence"], 3)

    def test_load_handles_corrupt_json_gracefully(self, tmp_path) -> None:
        import lynchpin.context.memory as mem_module
        mem_module._MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        mem_module._MEMORY_PATH.write_text("{invalid json}")
        store = load_memory()
        assert store.claims == []

    def test_update_preserves_first_seen(self) -> None:
        claim = Claim("persisted claim", 0.8, (), "project")
        store1 = update_memory([claim], [])
        first_seen = store1.claims[0].first_seen

        store2 = update_memory([claim], [])
        assert store2.claims[0].first_seen == first_seen


# ---------------------------------------------------------------------------
# detect_themes
# ---------------------------------------------------------------------------

class TestDetectThemes:
    def test_empty_months_returns_empty(self) -> None:
        assert detect_themes([]) == []

    def test_single_month_no_themes(self) -> None:
        # Themes require ≥2 appearances; one month produces none
        month = _make_month(top_projects=(("sinex", 36000.0),))
        assert detect_themes([month]) == []

    def test_project_appearing_in_two_months_creates_theme(self) -> None:
        m1 = _make_month("2026-01", top_projects=(("sinex", 36000.0),))
        m2 = _make_month("2026-02", top_projects=(("sinex", 36000.0),))
        themes = detect_themes([m1, m2])
        project_themes = [t for t in themes if t.kind == "project" and t.name == "sinex"]
        assert len(project_themes) == 1
        assert project_themes[0].month_count == 2
        assert project_themes[0].total_hours == pytest.approx(20.0, rel=0.01)

    def test_topic_appearing_in_two_months_creates_theme(self) -> None:
        m1 = _make_month("2026-01", top_topics=(("rust", 36000.0),))
        m2 = _make_month("2026-02", top_topics=(("rust", 36000.0),))
        themes = detect_themes([m1, m2])
        topic_themes = [t for t in themes if t.kind == "topic" and t.name == "rust"]
        assert len(topic_themes) == 1

    def test_rising_trend_when_second_half_exceeds_threshold(self) -> None:
        # second_half (50h) > first_half (10h) * 1.3 → rising
        m1 = _make_month("2026-01", top_projects=(("sinex", 36000.0),))   # 10h
        m2 = _make_month("2026-02", top_projects=(("sinex", 180000.0),))  # 50h
        themes = detect_themes([m1, m2])
        sinex_theme = next(t for t in themes if t.name == "sinex")
        assert sinex_theme.trend == "rising"

    def test_declining_trend_when_second_half_below_threshold(self) -> None:
        # second_half (5h) < first_half (50h) * 0.7 → declining
        m1 = _make_month("2026-01", top_projects=(("sinex", 180000.0),))  # 50h
        m2 = _make_month("2026-02", top_projects=(("sinex", 18000.0),))   # 5h
        themes = detect_themes([m1, m2])
        sinex_theme = next(t for t in themes if t.name == "sinex")
        assert sinex_theme.trend == "declining"

    def test_stable_trend_between_bounds(self) -> None:
        m1 = _make_month("2026-01", top_projects=(("sinex", 36000.0),))   # 10h
        m2 = _make_month("2026-02", top_projects=(("sinex", 36000.0),))   # 10h
        themes = detect_themes([m1, m2])
        sinex_theme = next(t for t in themes if t.name == "sinex")
        assert sinex_theme.trend == "stable"

    def test_stable_trend_three_months_equal_hours(self) -> None:
        # Regression: 3 equal months should be "stable" not "rising".
        # Bug: comparing sums (not averages) across unequal halves made flat activity appear rising.
        m1 = _make_month("2026-01", top_projects=(("sinex", 36000.0),))   # 10h
        m2 = _make_month("2026-02", top_projects=(("sinex", 36000.0),))   # 10h
        m3 = _make_month("2026-03", top_projects=(("sinex", 36000.0),))   # 10h
        themes = detect_themes([m1, m2, m3])
        sinex_theme = next(t for t in themes if t.name == "sinex")
        assert sinex_theme.trend == "stable"

    def test_only_top3_projects_per_month_count(self) -> None:
        # rank-4 project appears in 2 months but should not create a theme
        top4_projects = (
            ("a", 40000.0), ("b", 36000.0), ("c", 28000.0), ("rare", 1000.0)
        )
        m1 = _make_month("2026-01", top_projects=top4_projects)
        m2 = _make_month("2026-02", top_projects=top4_projects)
        themes = detect_themes([m1, m2])
        assert not any(t.name == "rare" for t in themes)
        assert any(t.name == "a" for t in themes)

    def test_themes_sorted_by_total_hours_descending(self) -> None:
        # Set up two projects with different total hours across 2 months
        m1 = _make_month("2026-01", top_projects=(("big", 72000.0), ("small", 36000.0)))
        m2 = _make_month("2026-02", top_projects=(("big", 72000.0), ("small", 36000.0)))
        themes = detect_themes([m1, m2])
        project_themes = [t for t in themes if t.kind == "project"]
        hours = [t.total_hours for t in project_themes]
        assert hours == sorted(hours, reverse=True)

    def test_first_and_last_seen_correct(self) -> None:
        m1 = _make_month("2026-01", top_projects=(("sinex", 36000.0),))
        m2 = _make_month("2026-02", top_projects=(("sinex", 36000.0),))
        m3 = _make_month("2026-03", top_projects=(("sinex", 36000.0),))
        themes = detect_themes([m1, m2, m3])
        sinex_theme = next(t for t in themes if t.name == "sinex")
        assert sinex_theme.first_seen == "2026-01"
        assert sinex_theme.last_seen == "2026-03"
        assert sinex_theme.month_count == 3

    def test_weekly_theme_detected_for_3_consecutive_weeks(self) -> None:
        # sinex appears in 3 consecutive weeks but only 1 month → monthly won't catch it
        weeks = [
            _make_week("2026-W10", top_projects=(("sinex", 36000.0),)),
            _make_week("2026-W11", top_projects=(("sinex", 36000.0),)),
            _make_week("2026-W12", top_projects=(("sinex", 36000.0),)),
        ]
        themes = detect_themes([], weeks=weeks)
        sinex = next((t for t in themes if t.name == "sinex"), None)
        assert sinex is not None
        assert sinex.kind == "project"

    def test_weekly_theme_not_detected_for_2_consecutive_weeks(self) -> None:
        # Only 2 consecutive weeks — below the 3-week threshold
        weeks = [
            _make_week("2026-W10", top_projects=(("sinex", 36000.0),)),
            _make_week("2026-W11", top_projects=(("sinex", 36000.0),)),
            _make_week("2026-W12", top_projects=(("other", 36000.0),)),
        ]
        themes = detect_themes([], weeks=weeks)
        assert not any(t.name == "sinex" for t in themes)

    def test_monthly_theme_not_duplicated_by_weekly_detection(self) -> None:
        # sinex appears in both 2 months (monthly theme) AND 3+ weeks
        m1 = _make_month("2026-01", top_projects=(("sinex", 72000.0),))
        m2 = _make_month("2026-02", top_projects=(("sinex", 72000.0),))
        weeks = [
            _make_week("2026-W04", top_projects=(("sinex", 18000.0),)),
            _make_week("2026-W05", top_projects=(("sinex", 18000.0),)),
            _make_week("2026-W06", top_projects=(("sinex", 18000.0),)),
        ]
        themes = detect_themes([m1, m2], weeks=weeks)
        sinex_themes = [t for t in themes if t.name == "sinex"]
        assert len(sinex_themes) == 1  # no duplicates

    def test_weekly_topic_theme_detected(self) -> None:
        # "rust" as a topic in 3 consecutive weeks
        weeks = [
            _make_week("2026-W10", top_topics=(("rust", 36000.0),)),
            _make_week("2026-W11", top_topics=(("rust", 36000.0),)),
            _make_week("2026-W12", top_topics=(("rust", 36000.0),)),
        ]
        themes = detect_themes([], weeks=weeks)
        rust = next((t for t in themes if t.name == "rust"), None)
        assert rust is not None
        assert rust.kind == "topic"

    def test_weekly_theme_non_consecutive_not_detected(self) -> None:
        # sinex in W10, W12, W14 — not consecutive (gap at W11, W13)
        weeks = [
            _make_week("2026-W10", top_projects=(("sinex", 36000.0),)),
            _make_week("2026-W11", top_projects=(("other", 36000.0),)),
            _make_week("2026-W12", top_projects=(("sinex", 36000.0),)),
            _make_week("2026-W13", top_projects=(("other", 36000.0),)),
            _make_week("2026-W14", top_projects=(("sinex", 36000.0),)),
        ]
        themes = detect_themes([], weeks=weeks)
        assert not any(t.name == "sinex" for t in themes)


# ---------------------------------------------------------------------------
# Helpers for packet_builders tests
# ---------------------------------------------------------------------------

def _polylogue_signal(
    signal_id: str,
    start: datetime,
    end: datetime,
    *,
    conversation_id: str | None = None,
    work_event_kind: str | None = None,
    total_cost_usd: float | None = None,
    thread_id: str | None = None,
    project_hint: str | None = None,
) -> TrajectorySignal:
    evidence: dict[str, object] = {}
    if conversation_id is not None:
        evidence["conversation_id"] = conversation_id
    if work_event_kind is not None:
        evidence["work_event_kind"] = work_event_kind
    if total_cost_usd is not None:
        evidence["total_cost_usd"] = total_cost_usd
    if thread_id is not None:
        evidence["thread_id"] = thread_id
    if project_hint is not None:
        evidence["project_hint"] = project_hint
    return TrajectorySignal(
        signal_id=signal_id,
        source="polylogue.session",
        kind="chat_session",
        start=start,
        end=end,
        evidence=evidence,
    )


_T0 = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 3, 10, 11, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _top_n
# ---------------------------------------------------------------------------

class TestTopN:
    def _items(self, n: int = 10) -> tuple[tuple[str, float], ...]:
        return tuple((f"p{i}", float(i * 3600)) for i in range(1, n + 1))

    def test_compact_limit_is_3(self) -> None:
        assert len(_top_n(self._items(10), "compact")) == 3

    def test_standard_limit_is_5(self) -> None:
        assert len(_top_n(self._items(10), "standard")) == 5

    def test_full_limit_is_10(self) -> None:
        assert len(_top_n(self._items(15), "full")) == 10

    def test_does_not_exceed_input_length(self) -> None:
        assert len(_top_n(self._items(2), "full")) == 2

    def test_converts_seconds_to_hours(self) -> None:
        result = _top_n((("foo", 3600.0),), "standard")
        assert result[0] == ("foo", pytest.approx(1.0))

    def test_unknown_tier_defaults_to_5(self) -> None:
        assert len(_top_n(self._items(10), "unknown_tier")) == 5

    def test_preserves_input_order(self) -> None:
        items = (("a", 7200.0), ("b", 3600.0), ("c", 1800.0))
        result = _top_n(items, "full")
        assert [name for name, _ in result] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# _aggregate_chat_work_events
# ---------------------------------------------------------------------------

class TestAggregateWorkEvents:
    def test_counts_unique_conversations(self) -> None:
        sigs = [
            _polylogue_signal("s1", _T0, _T1, conversation_id="c1"),
            _polylogue_signal("s2", _T0, _T1, conversation_id="c1"),  # duplicate
            _polylogue_signal("s3", _T0, _T1, conversation_id="c2"),
        ]
        assert _aggregate_chat_work_events(sigs)["session_count"] == 2

    def test_sums_costs(self) -> None:
        sigs = [
            _polylogue_signal("s1", _T0, _T1, conversation_id="c1", total_cost_usd=1.5),
            _polylogue_signal("s2", _T0, _T1, conversation_id="c2", total_cost_usd=0.25),
        ]
        assert _aggregate_chat_work_events(sigs)["total_cost_usd"] == pytest.approx(1.75)

    def test_counts_work_event_kinds(self) -> None:
        sigs = [
            _polylogue_signal("s1", _T0, _T1, conversation_id="c1", work_event_kind="implementation"),
            _polylogue_signal("s2", _T0, _T1, conversation_id="c2", work_event_kind="implementation"),
            _polylogue_signal("s3", _T0, _T1, conversation_id="c3", work_event_kind="debugging"),
        ]
        breakdown = _aggregate_chat_work_events(sigs)["work_event_breakdown"]
        assert breakdown["implementation"] == 2
        assert breakdown["debugging"] == 1

    def test_ignores_non_polylogue_signals(self) -> None:
        non_poly = TrajectorySignal(
            signal_id="x1", source="atuin.command", kind="command",
            start=_T0, end=_T1,
            evidence={"conversation_id": "c999", "total_cost_usd": 99.0},
        )
        result = _aggregate_chat_work_events([non_poly])
        assert result["session_count"] == 0
        assert result["total_cost_usd"] == 0.0

    def test_empty_signals_returns_zeros(self) -> None:
        result = _aggregate_chat_work_events([])
        assert result == {"session_count": 0, "work_event_breakdown": {}, "total_cost_usd": 0.0}


# ---------------------------------------------------------------------------
# build_thread_packets
# ---------------------------------------------------------------------------

class TestBuildThreadPackets:
    def test_groups_signals_by_thread_id(self) -> None:
        sigs = [
            _polylogue_signal("s1", _T0, _T1, conversation_id="c1", thread_id="t1"),
            _polylogue_signal("s2", _T0, _T1, conversation_id="c2", thread_id="t1"),
            _polylogue_signal("s3", _T0, _T1, conversation_id="c3", thread_id="t2"),
        ]
        packets = build_thread_packets(sigs)
        assert len(packets) == 2
        t1 = next(p for p in packets if p.thread_id == "t1")
        assert t1.session_count == 2

    def test_n_limits_output(self) -> None:
        sigs = [
            _polylogue_signal(f"s{i}", _T0, _T1, conversation_id=f"c{i}", thread_id=f"t{i}")
            for i in range(10)
        ]
        assert len(build_thread_packets(sigs, n=3)) == 3

    def test_ignores_non_polylogue_signals(self) -> None:
        sigs = [
            TrajectorySignal(
                signal_id="x1", source="atuin.command", kind="command",
                start=_T0, end=_T1,
                evidence={"thread_id": "t1", "conversation_id": "c1"},
            ),
        ]
        assert build_thread_packets(sigs) == []

    def test_falls_back_to_conversation_id_when_no_thread_id(self) -> None:
        sigs = [_polylogue_signal("s1", _T0, _T1, conversation_id="c1")]
        packets = build_thread_packets(sigs)
        assert len(packets) == 1
        assert packets[0].thread_id == "c1"

    def test_accumulates_cost_per_thread(self) -> None:
        sigs = [
            _polylogue_signal("s1", _T0, _T1, conversation_id="c1", thread_id="t1", total_cost_usd=1.0),
            _polylogue_signal("s2", _T0, _T1, conversation_id="c2", thread_id="t1", total_cost_usd=0.5),
        ]
        packets = build_thread_packets(sigs)
        assert packets[0].total_cost_usd == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# build_coverage_packet
# ---------------------------------------------------------------------------

def _day_with_coverage(
    day_date: date,
    *,
    has_activitywatch: bool = False,
    has_terminal: bool = False,
    has_chatlog: bool = False,
    has_git: bool = False,
    signal_count: int = 50,
    chain_count: int = 10,
    source_counts: dict[str, int] | None = None,
) -> TrajectoryDay:
    return TrajectoryDay(
        date=day_date,
        active_seconds=36000.0,
        recovery_seconds=28800.0,
        chain_count=chain_count,
        signal_count=signal_count,
        command_count=5,
        transcript_count=0,
        commit_count=0,
        dominant_mode="coding",
        dominant_project="sinex",
        dominant_topic=None,
        top_modes=(("coding", 36000.0),),
        top_projects=(("sinex", 36000.0),),
        top_topics=(),
        source_counts=source_counts or {"atuin.command": 20, "activitywatch.window": 30},
        coverage={
            "has_activitywatch": has_activitywatch,
            "has_terminal": has_terminal,
            "has_chatlog": has_chatlog,
            "has_git": has_git,
            "observed_hours": 18.0,
            "sources": [],
        },
        highlights=(),
        projects=(),
    )


class TestBuildCoveragePacket:
    def test_counts_days_with_activitywatch(self) -> None:
        days = [
            _day_with_coverage(date(2026, 3, 1), has_activitywatch=True),
            _day_with_coverage(date(2026, 3, 2), has_activitywatch=True),
            _day_with_coverage(date(2026, 3, 3), has_activitywatch=False),
        ]
        assert build_coverage_packet(days).days_with_activitywatch == 2

    def test_counts_days_with_chatlog(self) -> None:
        days = [
            _day_with_coverage(date(2026, 3, 1), has_chatlog=True),
            _day_with_coverage(date(2026, 3, 2), has_chatlog=False),
        ]
        assert build_coverage_packet(days).days_with_chatlog == 1

    def test_sums_signals_across_days(self) -> None:
        days = [
            _day_with_coverage(date(2026, 3, 1), signal_count=100, chain_count=15),
            _day_with_coverage(date(2026, 3, 2), signal_count=200, chain_count=25),
        ]
        packet = build_coverage_packet(days)
        assert packet.signal_count == 300
        assert packet.chain_count == 40

    def test_anomaly_count_is_passed_through(self) -> None:
        days = [_day_with_coverage(date(2026, 3, 1))]
        assert build_coverage_packet(days, anomaly_count=7).anomaly_count == 7

    def test_source_breakdown_aggregates_across_days(self) -> None:
        days = [
            _day_with_coverage(date(2026, 3, 1)),
            _day_with_coverage(date(2026, 3, 2)),
        ]
        # each day: {"atuin.command": 20, "activitywatch.window": 30}
        packet = build_coverage_packet(days)
        assert packet.source_breakdown["atuin.command"] == 40
        assert packet.source_breakdown["activitywatch.window"] == 60

    def test_empty_days_returns_zero_counts(self) -> None:
        packet = build_coverage_packet([])
        assert packet.day_count == 0
        assert packet.signal_count == 0
        assert packet.chain_count == 0


# ---------------------------------------------------------------------------
# build_project_arcs
# ---------------------------------------------------------------------------

class TestBuildProjectArcs:
    def test_empty_months_returns_empty(self) -> None:
        assert build_project_arcs([]) == []

    def test_single_month_returns_arc(self) -> None:
        months = [_make_month("2026-01", top_projects=(("sinex", 36000.0),))]
        arcs = build_project_arcs(months)
        assert len(arcs) == 1
        assert arcs[0].project == "sinex"

    def test_total_hours_accumulated_across_months(self) -> None:
        months = [
            _make_month("2026-01", top_projects=(("sinex", 36000.0),)),   # 10h
            _make_month("2026-02", top_projects=(("sinex", 72000.0),)),   # 20h
        ]
        arcs = build_project_arcs(months)
        sinex = next(a for a in arcs if a.project == "sinex")
        assert sinex.total_hours == pytest.approx(30.0)

    def test_velocity_trend_accelerating(self) -> None:
        # second half much higher than first half → accelerating
        months = [
            _make_month("2026-01", top_projects=(("sinex", 18000.0),)),   # 5h
            _make_month("2026-02", top_projects=(("sinex", 36000.0),)),   # 10h
            _make_month("2026-03", top_projects=(("sinex", 54000.0),)),   # 15h → second avg 12.5h > 5h*1.3
        ]
        arcs = build_project_arcs(months)
        sinex = next(a for a in arcs if a.project == "sinex")
        assert sinex.velocity_trend == "accelerating"

    def test_velocity_trend_stalling(self) -> None:
        # second half much lower than first half → stalling
        months = [
            _make_month("2026-01", top_projects=(("sinex", 54000.0),)),   # 15h
            _make_month("2026-02", top_projects=(("sinex", 36000.0),)),   # 10h
            _make_month("2026-03", top_projects=(("sinex", 7200.0),)),    # 2h → second avg 6h < 15h*0.7
        ]
        arcs = build_project_arcs(months)
        sinex = next(a for a in arcs if a.project == "sinex")
        assert sinex.velocity_trend == "stalling"

    def test_velocity_trend_steady_for_equal_halves(self) -> None:
        months = [
            _make_month("2026-01", top_projects=(("sinex", 36000.0),)),
            _make_month("2026-02", top_projects=(("sinex", 36000.0),)),
            _make_month("2026-03", top_projects=(("sinex", 36000.0),)),
        ]
        arcs = build_project_arcs(months)
        sinex = next(a for a in arcs if a.project == "sinex")
        assert sinex.velocity_trend == "steady"

    def test_top_5_projects_returned(self) -> None:
        # 6 distinct projects — should return at most 5
        projects = [(f"p{i}", float(i * 3600)) for i in range(1, 7)]
        months = [_make_month("2026-01", top_projects=tuple(projects))]
        arcs = build_project_arcs(months)
        assert len(arcs) <= 5

    def test_active_months_count(self) -> None:
        months = [
            _make_month("2026-01", top_projects=(("sinex", 36000.0),)),
            _make_month("2026-02", top_projects=(("sinex", 36000.0),)),
            _make_month("2026-03", top_projects=(("sinex", 36000.0),)),
        ]
        arcs = build_project_arcs(months)
        sinex = next(a for a in arcs if a.project == "sinex")
        assert sinex.active_months == 3

    def test_momentum_falls_back_to_trend_without_enough_weeks(self) -> None:
        months = [_make_month("2026-01", top_projects=(("sinex", 36000.0),))]
        arcs = build_project_arcs(months, weeks=[])
        assert arcs[0].momentum == arcs[0].velocity_trend


# ---------------------------------------------------------------------------
# build_theme_packets
# ---------------------------------------------------------------------------

class TestBuildThemePackets:
    def test_empty_months_returns_empty(self) -> None:
        assert build_theme_packets([], []) == []

    def test_returns_list_of_dicts(self) -> None:
        months = [
            _make_month("2026-01", top_projects=(("sinex", 36000.0),)),
            _make_month("2026-02", top_projects=(("sinex", 36000.0),)),
        ]
        packets = build_theme_packets(months, [])
        assert isinstance(packets, list)
        for p in packets:
            assert isinstance(p, dict)

    def test_detected_theme_has_required_fields(self) -> None:
        import json
        months = [
            _make_month("2026-01", top_projects=(("sinex", 36000.0),)),
            _make_month("2026-02", top_projects=(("sinex", 36000.0),)),
        ]
        packets = build_theme_packets(months, [])
        assert len(packets) >= 1
        sinex = next(p for p in packets if p["name"] == "sinex")
        for key in ("name", "kind", "total_hours", "month_count", "trend", "first_seen", "last_seen"):
            assert key in sinex
        json.dumps(sinex)


# ---------------------------------------------------------------------------
# build_claims_packet
# ---------------------------------------------------------------------------

class TestBuildClaimsPacket:
    def test_returns_dict_with_claims_key(self) -> None:
        months = [_make_month("2026-01")]
        result = build_claims_packet(months, [], [])
        assert isinstance(result, dict)
        assert "claims" in result

    def test_claims_is_list_of_dicts(self) -> None:
        months = [_make_month("2026-01")]
        result = build_claims_packet(months, [], [])
        assert isinstance(result["claims"], list)
        for claim in result["claims"]:
            assert isinstance(claim, dict)

    def test_empty_returns_empty_claims(self) -> None:
        result = build_claims_packet([], [], [])
        assert result["claims"] == []

    def test_claim_has_required_fields(self) -> None:
        import json
        months = [
            _make_month("2026-01", top_projects=(("sinex", 108000.0),)),
        ]
        result = build_claims_packet(months, [], [])
        if result["claims"]:
            claim = result["claims"][0]
            for key in ("statement", "confidence", "evidence_refs", "category"):
                assert key in claim
            json.dumps(result)


# ---------------------------------------------------------------------------
# build_project_arc_packets
# ---------------------------------------------------------------------------

class TestBuildProjectArcPackets:
    def test_empty_months_returns_empty(self) -> None:
        assert build_project_arc_packets([], [], []) == []

    def test_returns_list_of_dicts(self) -> None:
        months = [
            _make_month("2026-01", top_projects=(("sinex", 36000.0),)),
            _make_month("2026-02", top_projects=(("sinex", 36000.0),)),
        ]
        packets = build_project_arc_packets(months, [], [])
        assert isinstance(packets, list)
        for p in packets:
            assert isinstance(p, dict)

    def test_arc_has_required_fields(self) -> None:
        import json
        months = [
            _make_month("2026-01", top_projects=(("sinex", 36000.0),)),
            _make_month("2026-02", top_projects=(("sinex", 36000.0),)),
        ]
        packets = build_project_arc_packets(months, [], [])
        assert len(packets) >= 1
        arc = next(p for p in packets if p["project"] == "sinex")
        for key in ("project", "total_hours", "active_months", "velocity_trend",
                    "cost_usd", "active_episodes", "momentum"):
            assert key in arc
        json.dumps(arc)
