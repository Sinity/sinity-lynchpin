from __future__ import annotations

import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from lynchpin.sources.exports import chatlog
from lynchpin.signals import ActivitySignal, resolve_window
from lynchpin.signals.chains import ActivityChain as TrajectoryChain, _chain_id, build_chains, build_chains_from_attributed
from lynchpin.signals.rules import AttributedSignal
from lynchpin.context.period_summaries import summarize_months
from lynchpin.context.signal_rollups import _date_range, _highlights, _split_span_by_day, summarize_days
from lynchpin.context.summary_models import DayProjectSummary as TrajectoryDayProject
from lynchpin.context.summary_models import DaySummary as TrajectoryDay


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_repo_python(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["nix", "develop", "--command", "python", "-c", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )


def _dt(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_chatlog_iter_transcripts_normalizes_naive_timestamps(monkeypatch) -> None:
    row = chatlog._ChatTranscriptRow(
        provider="codex",
        slug="demo",
        title="demo",
        path="/tmp/demo.md",
        started_at=datetime(2026, 3, 16, 12, 0, 0),
        tokens=10,
        words=5,
        attachment_count=0,
        attachment_bytes=None,
    )
    monkeypatch.setattr(chatlog, "_load_transcripts", lambda providers: [row])

    items = list(
        chatlog.iter_transcripts(
            start=_dt(2026, 3, 16, 0, 0),
            end=_dt(2026, 3, 17, 0, 0),
        )
    )

    assert len(items) == 1
    assert items[0].started_at.tzinfo is not None


def test_build_chains_merges_terminal_project_signals() -> None:
    signals = [
        ActivitySignal(
            signal_id="a",
            source="atuin.command",
            kind="command",
            start=_dt(2026, 3, 16, 10, 0),
            end=_dt(2026, 3, 16, 10, 5),
            project_hint="polylogue",
            cwd="/realm/project/polylogue",
            detail="codex resume --last",
        ),
        ActivitySignal(
            signal_id="b",
            source="instrumentation.terminal_session",
            kind="terminal_session",
            start=_dt(2026, 3, 16, 10, 6),
            end=_dt(2026, 3, 16, 10, 40),
            mode_hint="coding",
            project_hint="polylogue",
            cwd="/realm/project/polylogue",
            detail="codex",
        ),
        ActivitySignal(
            signal_id="c",
            source="activitywatch.afk",
            kind="afk",
            start=_dt(2026, 3, 16, 10, 50),
            end=_dt(2026, 3, 16, 11, 5),
            mode_hint="recovery",
            detail="afk",
        ),
    ]

    chains = build_chains(signals)

    assert len(chains) == 2
    assert chains[0].mode == "coding"
    assert chains[0].project == "polylogue"
    assert chains[0].signal_count == 2
    assert chains[1].mode == "recovery"


def test_build_chains_truncates_incompatible_overlaps() -> None:
    signals = [
        ActivitySignal(
            signal_id="a",
            source="instrumentation.terminal_session",
            kind="terminal_session",
            start=_dt(2026, 3, 16, 10, 0),
            end=_dt(2026, 3, 16, 11, 0),
            mode_hint="coding",
            project_hint="polylogue",
            cwd="/realm/project/polylogue",
            detail="codex",
        ),
        ActivitySignal(
            signal_id="b",
            source="activitywatch.web",
            kind="web",
            start=_dt(2026, 3, 16, 10, 30),
            end=_dt(2026, 3, 16, 11, 30),
            url="https://docs.rs/serde",
            title="serde docs",
        ),
    ]

    chains = build_chains(signals)

    assert len(chains) == 2
    assert chains[0].end == _dt(2026, 3, 16, 10, 30)
    assert chains[1].start == _dt(2026, 3, 16, 10, 30)


def test_summarize_days_splits_cross_midnight_chain() -> None:
    signals = [
        ActivitySignal(
            signal_id="cmd",
            source="atuin.command",
            kind="command",
            start=_dt(2026, 3, 15, 23, 30),
            end=_dt(2026, 3, 15, 23, 45),
            project_hint="polylogue",
            cwd="/realm/project/polylogue",
            detail="codex resume --last",
        ),
        ActivitySignal(
            signal_id="git",
            source="git.commit",
            kind="git_commit",
            start=_dt(2026, 3, 16, 0, 10),
            end=_dt(2026, 3, 16, 0, 10),
            project_hint="polylogue",
            detail="feat: improve packets",
        ),
    ]
    chain = TrajectoryChain(
        chain_id="chain",
        start=_dt(2026, 3, 15, 23, 30),
        end=_dt(2026, 3, 16, 0, 30),
        mode="coding",
        project="polylogue",
        mode_confidence=0.9,
        project_confidence=1.0,
        signal_count=2,
        source_count=2,
        sources=("atuin.command", "git.commit"),
        apps=(),
        domains=(),
        titles=(),
        reasons=("project_terminal",),
        signals=(),
    )

    days = summarize_days(
        signals=signals,
        chains=[chain],
        start=_dt(2026, 3, 15, 0, 0),
        end=_dt(2026, 3, 17, 0, 0),
    )

    assert [day.date for day in days] == [date(2026, 3, 15), date(2026, 3, 16), date(2026, 3, 17)]
    assert days[0].active_seconds == 1800.0
    assert days[1].active_seconds == 1800.0
    assert days[2].active_seconds == 0.0
    assert days[0].command_count == 1
    assert days[1].commit_count == 1
    assert days[1].dominant_project == "polylogue"


def test_context_source_is_registered_in_warehouse() -> None:
    result = _run_repo_python(
        """
from lynchpin.views.warehouse.specs import SOURCE_SPECS
spec = next(spec for spec in SOURCE_SPECS if spec.name == "context")
table_names = [table.name for table in spec.tables]
# Core context rollup tables
for expected in [
    "context_signal",
    "context_day",
    "context_day_project",
    "context_period",
    "context_day_event",
    "context_period_project",
    "context_period_topic",
]:
    assert expected in table_names, f"{expected!r} missing from context source; got {table_names}"
print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_summarize_months_groups_day_summaries() -> None:
    january = TrajectoryDay(
        date=date(2026, 1, 31),
        active_seconds=7200.0,
        recovery_seconds=1800.0,
        chain_count=3,
        signal_count=10,
        command_count=4,
        transcript_count=1,
        commit_count=2,
        dominant_mode="coding",
        dominant_project="polylogue",
        dominant_topic=None,
        top_modes=(("coding", 7200.0),),
        top_projects=(("polylogue", 5400.0),),
        top_topics=(),
        source_counts={"atuin.command": 4},
        coverage={"has_activitywatch": True, "has_terminal": True, "has_chatlog": True, "has_git": True},
        highlights=("mode:coding 2.0h",),
        projects=(),
    )
    february = TrajectoryDay(
        date=date(2026, 2, 1),
        active_seconds=3600.0,
        recovery_seconds=900.0,
        chain_count=2,
        signal_count=5,
        command_count=1,
        transcript_count=0,
        commit_count=1,
        dominant_mode="research",
        dominant_project="lynchpin",
        dominant_topic=None,
        top_modes=(("research", 3600.0),),
        top_projects=(("lynchpin", 2400.0),),
        top_topics=(),
        source_counts={"activitywatch.window": 5},
        coverage={"has_activitywatch": True, "has_terminal": False, "has_chatlog": False, "has_git": True},
        highlights=("mode:research 1.0h",),
        projects=(),
    )

    months = summarize_months([january, february])

    assert list(months) == ["2026-01", "2026-02"]
    assert months["2026-01"].active_seconds == 7200.0
    assert months["2026-01"].commit_count == 2
    assert months["2026-02"].dominant_modes == (("research", 3600.0),)
    assert months["2026-02"].dominant_projects == (("lynchpin", 2400.0),)


# ---------------------------------------------------------------------------
# resolve_window
# ---------------------------------------------------------------------------

class TestResolveWindow:
    def _now(self) -> datetime:
        return datetime(2026, 3, 17, 12, 0, 0, tzinfo=timezone.utc)

    def test_default_lookback_is_14_days(self):
        now = self._now()
        start, end = resolve_window(now=now, days=14)
        assert (end - start).total_seconds() == pytest.approx(14 * 86400)

    def test_end_uses_provided_now(self):
        now = self._now()
        _, end = resolve_window(now=now, days=7)
        # end should be equal to (or very close to) now
        assert abs((end - now).total_seconds()) < 1.0

    def test_custom_days_changes_window(self):
        now = self._now()
        start_7, _ = resolve_window(now=now, days=7)
        start_30, _ = resolve_window(now=now, days=30)
        diff = (start_7 - start_30).total_seconds()
        assert diff == pytest.approx(23 * 86400)

    def test_explicit_start_overrides_days(self):
        now = self._now()
        explicit_start = now - timedelta(days=5)
        start, _ = resolve_window(start=explicit_start, now=now)
        assert abs((start - explicit_start).total_seconds()) < 1.0

    def test_start_equal_to_end_raises(self):
        now = self._now()
        with pytest.raises(ValueError, match="Invalid activity window"):
            resolve_window(start=now, end=now)

    def test_start_after_end_raises(self):
        now = self._now()
        with pytest.raises(ValueError):
            resolve_window(start=now + timedelta(hours=1), end=now)

    def test_returns_timezone_aware_datetimes(self):
        start, end = resolve_window(days=7)
        assert start.tzinfo is not None
        assert end.tzinfo is not None


# ---------------------------------------------------------------------------
# ActivitySignal
# ---------------------------------------------------------------------------

class TestActivitySignal:
    def _dt(self, h: int, m: int = 0) -> datetime:
        return datetime(2026, 3, 17, h, m, tzinfo=timezone.utc)

    def test_duration_seconds_basic(self):
        sig = ActivitySignal(
            signal_id="s1",
            source="atuin.command",
            kind="command",
            start=self._dt(10, 0),
            end=self._dt(10, 30),
        )
        assert sig.duration_seconds == pytest.approx(1800.0)

    def test_duration_zero_for_point_signal(self):
        t = self._dt(10)
        sig = ActivitySignal(
            signal_id="s2",
            source="git.commit",
            kind="git_commit",
            start=t,
            end=t,
        )
        assert sig.duration_seconds == 0.0

    def test_to_dict_is_json_serializable(self):
        import json
        sig = ActivitySignal(
            signal_id="s3",
            source="activitywatch.window",
            kind="window",
            start=self._dt(9),
            end=self._dt(9, 30),
            title="Visual Studio Code",
            app="code",
        )
        d = sig.to_dict()
        json.dumps(d)
        assert "signal_id" in d
        assert "duration_seconds" in d
        assert d["app"] == "code"

    def test_evidence_default_is_empty_dict(self):
        sig = ActivitySignal(
            signal_id="s4",
            source="atuin.command",
            kind="command",
            start=self._dt(10),
            end=self._dt(10, 5),
        )
        assert sig.evidence == {}


# ---------------------------------------------------------------------------
# _split_span_by_day / _date_range
# ---------------------------------------------------------------------------

class TestSplitSpanByDay:
    def _dt(self, day: int, hour: int) -> datetime:
        return datetime(2026, 3, day, hour, 0, 0, tzinfo=timezone.utc)

    def test_same_day_span(self):
        segments = _split_span_by_day(self._dt(10, 9), self._dt(10, 17))
        assert len(segments) == 1
        d, secs = segments[0]
        assert d == date(2026, 3, 10)
        assert secs == pytest.approx(8 * 3600)

    def test_cross_midnight_split(self):
        # 2026-03-15 23:00 → 2026-03-16 01:00 (2h total, split 1h+1h)
        segments = _split_span_by_day(self._dt(15, 23), self._dt(16, 1))
        assert len(segments) == 2
        day15, secs15 = segments[0]
        day16, secs16 = segments[1]
        assert day15 == date(2026, 3, 15)
        assert secs15 == pytest.approx(3600.0)  # 1h before midnight
        assert day16 == date(2026, 3, 16)
        assert secs16 == pytest.approx(3600.0)  # 1h after midnight

    def test_spans_three_days(self):
        # midnight to midnight + 1s: should have 2 segments
        start = datetime(2026, 3, 10, 22, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 12, 2, 0, 0, tzinfo=timezone.utc)
        segments = _split_span_by_day(start, end)
        days = [d for d, _ in segments]
        assert days == [date(2026, 3, 10), date(2026, 3, 11), date(2026, 3, 12)]
        total = sum(s for _, s in segments)
        assert total == pytest.approx((end - start).total_seconds())

    def test_end_equals_start_returns_empty(self):
        t = self._dt(10, 12)
        segments = _split_span_by_day(t, t)
        assert segments == []

    def test_end_before_start_returns_empty(self):
        segments = _split_span_by_day(self._dt(10, 14), self._dt(10, 10))
        assert segments == []

    def test_total_seconds_preserved(self):
        start = self._dt(12, 18)
        end = self._dt(13, 6)  # 12h spanning midnight
        segments = _split_span_by_day(start, end)
        total = sum(s for _, s in segments)
        assert total == pytest.approx((end - start).total_seconds())


class TestDateRange:
    def test_single_day(self):
        result = _date_range(date(2026, 3, 10), date(2026, 3, 10))
        assert result == [date(2026, 3, 10)]

    def test_consecutive_days(self):
        result = _date_range(date(2026, 3, 10), date(2026, 3, 12))
        assert result == [date(2026, 3, 10), date(2026, 3, 11), date(2026, 3, 12)]

    def test_end_before_start_returns_empty(self):
        result = _date_range(date(2026, 3, 12), date(2026, 3, 10))
        assert result == []

    def test_month_boundary(self):
        result = _date_range(date(2026, 2, 27), date(2026, 3, 2))
        assert len(result) == 4
        assert date(2026, 2, 28) in result
        assert date(2026, 3, 1) in result


# ---------------------------------------------------------------------------
# TrajectoryChain quality flags and to_dict
# ---------------------------------------------------------------------------

def _attributed(
    signal_id: str,
    source: str,
    start: datetime,
    end: datetime,
    mode: str = "coding",
    mode_confidence: float = 0.9,
    project: str | None = "polylogue",
    project_confidence: float = 0.95,
    topic: str | None = None,
) -> AttributedSignal:
    sig = ActivitySignal(
        signal_id=signal_id,
        source=source,
        kind="command",
        start=start,
        end=end,
    )
    return AttributedSignal(
        signal=sig,
        mode=mode,
        mode_confidence=mode_confidence,
        project=project,
        project_confidence=project_confidence,
        reasons=("test",),
        topic=topic,
        topic_confidence=0.0 if topic is None else 0.7,
        topic_scores=(),
    )


def _chain_dt(h: int, m: int = 0, s: int = 0) -> datetime:
    return datetime(2026, 3, 16, h, m, s, tzinfo=timezone.utc)


class TestChainQualityFlags:
    def test_short_flag_when_under_60s(self):
        sigs = [_attributed("a", "atuin.command", _chain_dt(10, 0, 0), _chain_dt(10, 0, 30))]
        chains = build_chains_from_attributed(sigs)
        assert len(chains) == 1
        assert "short" in chains[0].quality_flags

    def test_no_short_flag_when_60s_or_more(self):
        sigs = [_attributed("a", "atuin.command", _chain_dt(10, 0, 0), _chain_dt(10, 1, 0))]
        chains = build_chains_from_attributed(sigs)
        assert "short" not in chains[0].quality_flags

    def test_single_source_flag_when_one_source(self):
        sigs = [
            _attributed("a", "atuin.command", _chain_dt(10, 0), _chain_dt(10, 5)),
            _attributed("b", "atuin.command", _chain_dt(10, 5), _chain_dt(10, 10)),
        ]
        chains = build_chains_from_attributed(sigs)
        assert len(chains) == 1
        assert "single_source" in chains[0].quality_flags

    def test_no_single_source_flag_with_multiple_sources(self):
        sigs = [
            _attributed("a", "atuin.command", _chain_dt(10, 0), _chain_dt(10, 5)),
            _attributed("b", "instrumentation.terminal_session", _chain_dt(10, 5), _chain_dt(10, 10)),
        ]
        chains = build_chains_from_attributed(sigs)
        assert len(chains) == 1
        assert "single_source" not in chains[0].quality_flags

    def test_gap_heavy_flag_when_low_signal_coverage(self):
        # Two 60s signals with a 4m30s gap → chain duration = 6m30s = 390s
        # total_weight = 60 + 60 = 120; coverage = 120/390 ≈ 0.31 → gap_heavy
        sigs = [
            _attributed("a", "atuin.command", _chain_dt(10, 0, 0), _chain_dt(10, 1, 0)),
            _attributed("b", "atuin.command", _chain_dt(10, 5, 30), _chain_dt(10, 6, 30)),
        ]
        chains = build_chains_from_attributed(sigs)
        assert len(chains) == 1
        assert "gap_heavy" in chains[0].quality_flags

    def test_no_gap_heavy_when_dense_coverage(self):
        # Two adjacent 5min signals → coverage = 600/600 = 1.0
        sigs = [
            _attributed("a", "atuin.command", _chain_dt(10, 0), _chain_dt(10, 5)),
            _attributed("b", "atuin.command", _chain_dt(10, 5), _chain_dt(10, 10)),
        ]
        chains = build_chains_from_attributed(sigs)
        assert len(chains) == 1
        assert "gap_heavy" not in chains[0].quality_flags

    def test_to_dict_is_json_serializable(self):
        import json
        sigs = [_attributed("a", "atuin.command", _chain_dt(10, 0), _chain_dt(10, 5))]
        chain = build_chains_from_attributed(sigs)[0]
        d = chain.to_dict()
        json.dumps(d)
        assert "chain_id" in d
        assert "quality_flags" in d
        assert "topic_seconds" in d
        assert d["mode"] == "coding"

    def test_recovery_chain_rejects_contained_signals(self):
        # AFK chain overlaps with a second signal that starts inside it — second chain should be dropped.
        afk = _attributed(
            "afk", "activitywatch.afk",
            _chain_dt(10, 0), _chain_dt(10, 30),
            mode="recovery", project=None, project_confidence=0.0,
        )
        web = _attributed(
            "web", "activitywatch.web",
            _chain_dt(10, 10), _chain_dt(10, 20),
            mode="recovery", project=None, project_confidence=0.0,
        )
        chains = build_chains_from_attributed([afk, web])
        # The web signal starts inside the AFK chain → should be absorbed, not create a new chain
        assert len(chains) == 1
        assert chains[0].mode == "recovery"

    def test_chain_to_dict_includes_signals_list(self):
        sigs = [
            _attributed("a", "atuin.command", _chain_dt(10, 0), _chain_dt(10, 2)),
            _attributed("b", "git.commit", _chain_dt(10, 2), _chain_dt(10, 5)),
        ]
        chain = build_chains_from_attributed(sigs)[0]
        d = chain.to_dict()
        assert isinstance(d["signals"], list)
        assert len(d["signals"]) == 2


# ---------------------------------------------------------------------------
# TrajectoryDay helpers and serialization
# ---------------------------------------------------------------------------

class TestHighlights:
    def test_basic_highlights(self):
        hl = _highlights(
            dominant_mode="coding",
            dominant_project="polylogue",
            top_modes=(("coding", 7200.0),),
            top_projects=(("polylogue", 5400.0),),
            command_count=3,
            transcript_count=0,
            commit_count=2,
        )
        assert any("mode:coding" in h for h in hl)
        assert any("project:polylogue" in h for h in hl)
        assert any("commands:3" in h for h in hl)
        assert any("commits:2" in h for h in hl)
        assert len(hl) <= 5

    def test_empty_when_no_activity(self):
        hl = _highlights(
            dominant_mode=None,
            dominant_project=None,
            top_modes=(),
            top_projects=(),
            command_count=0,
            transcript_count=0,
            commit_count=0,
        )
        assert hl == []

    def test_transcripts_included(self):
        hl = _highlights(
            dominant_mode=None,
            dominant_project=None,
            top_modes=(),
            top_projects=(),
            command_count=0,
            transcript_count=5,
            commit_count=0,
        )
        assert any("transcripts:5" in h for h in hl)

    def test_hours_formatted_correctly(self):
        # 3600 seconds = 1.0 h
        hl = _highlights(
            dominant_mode="research",
            dominant_project=None,
            top_modes=(("research", 3600.0),),
            top_projects=(),
            command_count=0,
            transcript_count=0,
            commit_count=0,
        )
        assert any("1.0h" in h for h in hl)

    def test_capped_at_five(self):
        # All five possible highlight slots populated
        hl = _highlights(
            dominant_mode="coding",
            dominant_project="sinex",
            top_modes=(("coding", 7200.0),),
            top_projects=(("sinex", 5400.0),),
            command_count=10,
            transcript_count=2,
            commit_count=5,
        )
        assert len(hl) == 5


class TestTrajectoryDayToDict:
    def _make_day(self) -> TrajectoryDay:
        return TrajectoryDay(
            date=date(2026, 3, 15),
            active_seconds=7200.0,
            recovery_seconds=1800.0,
            chain_count=3,
            signal_count=12,
            command_count=5,
            transcript_count=1,
            commit_count=2,
            dominant_mode="coding",
            dominant_project="polylogue",
            dominant_topic=None,
            top_modes=(("coding", 7200.0),),
            top_projects=(("polylogue", 5400.0),),
            top_topics=(),
            source_counts={"atuin.command": 5, "git.commit": 2},
            coverage={"has_activitywatch": True, "has_git": True},
            highlights=("mode:coding 2.0h",),
            projects=(),
        )

    def test_to_dict_json_serializable(self):
        import json
        d = self._make_day().to_dict()
        json.dumps(d)

    def test_to_dict_date_is_isoformat(self):
        d = self._make_day().to_dict()
        assert d["date"] == "2026-03-15"

    def test_to_dict_observed_seconds(self):
        d = self._make_day().to_dict()
        assert d["observed_seconds"] == pytest.approx(9000.0)

    def test_to_dict_top_modes_list_of_lists(self):
        d = self._make_day().to_dict()
        assert d["top_modes"] == [["coding", 7200.0]]

    def test_to_dict_includes_all_required_keys(self):
        d = self._make_day().to_dict()
        for key in ("date", "active_seconds", "recovery_seconds", "chain_count",
                    "signal_count", "command_count", "commit_count", "dominant_mode",
                    "top_modes", "top_projects", "source_counts", "coverage", "highlights"):
            assert key in d


class TestSummarizeDaysTopics:
    """Verify that chain-level topics flow into day top_topics via summarize_days."""

    def test_chain_topic_reflected_in_day(self):
        chain = TrajectoryChain(
            chain_id="chain1",
            start=_dt(2026, 3, 16, 10, 0),
            end=_dt(2026, 3, 16, 11, 0),
            mode="coding",
            project="polylogue",
            mode_confidence=0.9,
            project_confidence=0.95,
            signal_count=1,
            source_count=1,
            sources=("atuin.command",),
            apps=(),
            domains=(),
            titles=(),
            reasons=("test",),
            signals=(),
            topic="rust",
            topic_confidence=0.8,
        )
        days = summarize_days(
            signals=[],
            chains=[chain],
            start=_dt(2026, 3, 16, 0, 0),
            end=_dt(2026, 3, 17, 0, 0),
        )
        day = next(d for d in days if d.date == date(2026, 3, 16))
        assert day.dominant_topic == "rust"
        assert any(t == "rust" for t, _ in day.top_topics)

    def test_no_topic_chain_leaves_day_topic_none(self):
        chain = TrajectoryChain(
            chain_id="chain2",
            start=_dt(2026, 3, 16, 10, 0),
            end=_dt(2026, 3, 16, 11, 0),
            mode="coding",
            project="polylogue",
            mode_confidence=0.9,
            project_confidence=0.95,
            signal_count=1,
            source_count=1,
            sources=("atuin.command",),
            apps=(),
            domains=(),
            titles=(),
            reasons=("test",),
            signals=(),
            topic=None,
        )
        days = summarize_days(
            signals=[],
            chains=[chain],
            start=_dt(2026, 3, 16, 0, 0),
            end=_dt(2026, 3, 17, 0, 0),
        )
        day = next(d for d in days if d.date == date(2026, 3, 16))
        assert day.dominant_topic is None
        assert day.top_topics == ()


# ---------------------------------------------------------------------------
# TrajectoryDayProject.to_dict
# ---------------------------------------------------------------------------

class TestTrajectoryDayProjectToDict:
    def _make_project(self) -> TrajectoryDayProject:
        return TrajectoryDayProject(
            date=date(2026, 3, 15),
            project="sinex",
            duration_seconds=7200.0,
            chain_count=4,
            top_modes=(("coding", 5400.0), ("research", 1800.0)),
        )

    def test_to_dict_is_json_serializable(self) -> None:
        import json
        d = self._make_project().to_dict()
        json.dumps(d)

    def test_to_dict_has_required_fields(self) -> None:
        d = self._make_project().to_dict()
        for key in ("date", "project", "duration_seconds", "chain_count", "top_modes"):
            assert key in d

    def test_to_dict_date_is_isoformat(self) -> None:
        d = self._make_project().to_dict()
        assert d["date"] == "2026-03-15"

    def test_top_modes_is_list_of_lists(self) -> None:
        d = self._make_project().to_dict()
        assert isinstance(d["top_modes"], list)
        for entry in d["top_modes"]:
            assert isinstance(entry, list)
            assert len(entry) == 2


# ---------------------------------------------------------------------------
# _chain_id
# ---------------------------------------------------------------------------

class TestChainId:
    _t0 = datetime(2026, 3, 17, 10, 0, 0, tzinfo=timezone.utc)
    _t1 = datetime(2026, 3, 17, 11, 0, 0, tzinfo=timezone.utc)

    def _sig(self, signal_id: str):
        from types import SimpleNamespace
        return SimpleNamespace(signal_id=signal_id)

    def test_returns_16_hex_chars(self) -> None:
        result = _chain_id(self._t0, self._t1, "coding", "sinex", [])
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_same_inputs_same_output(self) -> None:
        sigs = [self._sig("s1"), self._sig("s2")]
        a = _chain_id(self._t0, self._t1, "coding", "sinex", sigs)
        b = _chain_id(self._t0, self._t1, "coding", "sinex", sigs)
        assert a == b

    def test_different_mode_different_output(self) -> None:
        a = _chain_id(self._t0, self._t1, "coding", "sinex", [])
        b = _chain_id(self._t0, self._t1, "research", "sinex", [])
        assert a != b

    def test_none_project_becomes_empty_string(self) -> None:
        # Should not raise; None project → "" in payload
        result = _chain_id(self._t0, self._t1, "coding", None, [])
        assert isinstance(result, str)
        assert len(result) == 16

    def test_signal_ids_affect_output(self) -> None:
        a = _chain_id(self._t0, self._t1, "coding", None, [self._sig("s_a")])
        b = _chain_id(self._t0, self._t1, "coding", None, [self._sig("s_b")])
        assert a != b

    def test_empty_signals_deterministic(self) -> None:
        a = _chain_id(self._t0, self._t1, "misc", None, [])
        b = _chain_id(self._t0, self._t1, "misc", None, [])
        assert a == b
