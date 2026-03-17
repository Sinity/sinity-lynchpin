from __future__ import annotations

import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from lynchpin.sources.exports import chatlog
from lynchpin.trajectory.chains import TrajectoryChain, build_chains
from lynchpin.trajectory.day import TrajectoryDay, summarize_days
from lynchpin.trajectory.period import summarize_months
from lynchpin.trajectory.day import _date_range, _split_span_by_day
from lynchpin.trajectory.signal import TrajectorySignal, resolve_window


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
        TrajectorySignal(
            signal_id="a",
            source="atuin.command",
            kind="command",
            start=_dt(2026, 3, 16, 10, 0),
            end=_dt(2026, 3, 16, 10, 5),
            project_hint="polylogue",
            cwd="/realm/project/polylogue",
            detail="codex resume --last",
        ),
        TrajectorySignal(
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
        TrajectorySignal(
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
        TrajectorySignal(
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
        TrajectorySignal(
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
        TrajectorySignal(
            signal_id="cmd",
            source="atuin.command",
            kind="command",
            start=_dt(2026, 3, 15, 23, 30),
            end=_dt(2026, 3, 15, 23, 45),
            project_hint="polylogue",
            cwd="/realm/project/polylogue",
            detail="codex resume --last",
        ),
        TrajectorySignal(
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


def test_trajectory_source_is_registered_in_warehouse() -> None:
    result = _run_repo_python(
        """
from lynchpin.views import warehouse
spec = next(spec for spec in warehouse.SOURCE_SPECS if spec.name == "trajectory")
table_names = [table.name for table in spec.tables]
# Core trajectory tables (Sprint 1-5 schema)
for expected in [
    "trajectory_signal",
    "trajectory_chain",
    "trajectory_day",
    "trajectory_day_project",
    "trajectory_period",
    "trajectory_day_event",
    "trajectory_period_project",
    "trajectory_period_topic",
]:
    assert expected in table_names, f"{expected!r} missing from trajectory source; got {table_names}"
print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_summarize_months_groups_trajectory_days() -> None:
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
        top_modes=(("coding", 7200.0),),
        top_projects=(("polylogue", 5400.0),),
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
        top_modes=(("research", 3600.0),),
        top_projects=(("lynchpin", 2400.0),),
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
        with pytest.raises(ValueError, match="Invalid trajectory window"):
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
# TrajectorySignal
# ---------------------------------------------------------------------------

class TestTrajectorySignal:
    def _dt(self, h: int, m: int = 0) -> datetime:
        return datetime(2026, 3, 17, h, m, tzinfo=timezone.utc)

    def test_duration_seconds_basic(self):
        sig = TrajectorySignal(
            signal_id="s1",
            source="atuin.command",
            kind="command",
            start=self._dt(10, 0),
            end=self._dt(10, 30),
        )
        assert sig.duration_seconds == pytest.approx(1800.0)

    def test_duration_zero_for_point_signal(self):
        t = self._dt(10)
        sig = TrajectorySignal(
            signal_id="s2",
            source="git.commit",
            kind="git_commit",
            start=t,
            end=t,
        )
        assert sig.duration_seconds == 0.0

    def test_to_dict_is_json_serializable(self):
        import json
        sig = TrajectorySignal(
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
        sig = TrajectorySignal(
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
