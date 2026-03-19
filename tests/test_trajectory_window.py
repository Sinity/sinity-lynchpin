from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from lynchpin.trajectory.day import TrajectoryDay
from lynchpin.trajectory import window as trajectory_window
from lynchpin.trajectory.window import load_date_window


def _sample_day(target: date) -> TrajectoryDay:
    return TrajectoryDay(
        date=target,
        active_seconds=3600.0,
        recovery_seconds=1800.0,
        chain_count=1,
        signal_count=1,
        command_count=1,
        transcript_count=0,
        commit_count=0,
        dominant_mode="build",
        dominant_project="sinity-lynchpin",
        top_modes=(("build", 3600.0),),
        top_projects=(("sinity-lynchpin", 3600.0),),
        source_counts={"git.commit": 1},
        coverage={"sources": ["git.commit"]},
        highlights=("mode:build",),
        projects=(),
    )


def test_load_date_window_annotates_anomalies(monkeypatch) -> None:
    target = date(2026, 3, 17)
    captured = {}

    def fake_load_signals(*, start, end, days):
        captured["start"] = start
        captured["end"] = end
        captured["days"] = days
        return ["signal"]

    monkeypatch.setattr(trajectory_window, "resolve_window", lambda **kwargs: (kwargs["start"], kwargs["end"]))
    monkeypatch.setattr(trajectory_window, "load_signals", fake_load_signals)
    monkeypatch.setattr(trajectory_window, "classify_signals", lambda signals: ("attributed",))
    monkeypatch.setattr(trajectory_window, "build_chains_from_attributed", lambda attributed: ("chain",))
    monkeypatch.setattr(trajectory_window, "summarize_days", lambda **kwargs: [_sample_day(target)])
    monkeypatch.setattr(
        trajectory_window,
        "detect_anomalies",
        lambda days: [SimpleNamespace(date=target, description="timeline gap")],
    )

    window = load_date_window(
        target,
        target,
        annotate_anomalies=True,
        local_tz=timezone.utc,
    )

    assert captured["days"] == 1
    assert captured["start"] == datetime(2026, 3, 17, 0, 0, tzinfo=timezone.utc)
    assert captured["end"] == datetime(2026, 3, 18, 0, 0, tzinfo=timezone.utc)
    assert window.span_days == 1
    assert window.day_map()[target].anomalies == ("timeline gap",)
