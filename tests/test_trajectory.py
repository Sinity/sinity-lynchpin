from __future__ import annotations

import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

from lynchpin.sources.exports import chatlog
from lynchpin.trajectory.chains import TrajectoryChain, build_chains
from lynchpin.trajectory.day import TrajectoryDay, summarize_days
from lynchpin.trajectory.period import summarize_months
from lynchpin.trajectory.signal import TrajectorySignal


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
assert [table.name for table in spec.tables] == [
    "trajectory_signal",
    "trajectory_chain",
    "trajectory_day",
    "trajectory_day_project",
    "trajectory_period",
]
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
