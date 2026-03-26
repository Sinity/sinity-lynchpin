from __future__ import annotations

from datetime import date

from lynchpin.context.patterns import build_recent_focus_loops, detect_anomalies, detect_episodes
from lynchpin.context.summary_models import DaySummary


def _day(
    target: date,
    *,
    active_seconds: float = 14400.0,
    recovery_seconds: float = 3600.0,
    dominant_mode: str | None = "coding",
    dominant_project: str | None = "sinity-lynchpin",
    top_projects: tuple[tuple[str, float], ...] | None = None,
) -> DaySummary:
    return DaySummary(
        date=target,
        active_seconds=active_seconds,
        recovery_seconds=recovery_seconds,
        chain_count=2,
        signal_count=10,
        command_count=4,
        transcript_count=0,
        commit_count=1,
        dominant_mode=dominant_mode,
        dominant_project=dominant_project,
        dominant_topic=None,
        top_modes=(("coding", active_seconds),),
        top_projects=top_projects or ((dominant_project or "misc", active_seconds),),
        top_topics=(),
        source_counts={},
        coverage={},
        highlights=[],
    )


def test_detect_episodes_builds_context_owned_run() -> None:
    episodes = detect_episodes(
        [
            _day(date(2026, 3, 1)),
            _day(date(2026, 3, 2)),
            _day(date(2026, 3, 3), dominant_mode="review", dominant_project="sinex", top_projects=(("sinex", 14400.0),)),
        ]
    )

    assert len(episodes) == 1
    assert episodes[0].label == "sinity-lynchpin coding"
    assert episodes[0].start_date == date(2026, 3, 1)
    assert episodes[0].end_date == date(2026, 3, 2)


def test_detect_anomalies_flags_project_attention_shift() -> None:
    anomalies = detect_anomalies(
        [
            _day(date(2026, 3, 1), top_projects=(("sinity-lynchpin", 14400.0),)),
            _day(date(2026, 3, 2), top_projects=(("sinity-lynchpin", 14400.0),)),
            _day(date(2026, 3, 3), top_projects=(("sinity-lynchpin", 14400.0),)),
            _day(date(2026, 3, 4), dominant_project="sinex", top_projects=(("sinex", 14400.0),)),
        ],
        rolling_window=3,
        include_processed=False,
    )

    assert any(anomaly.kind == "project_attention_shift" for anomaly in anomalies)


def test_build_recent_focus_loops_sorts_latest_first() -> None:
    packets = build_recent_focus_loops(
        [
            {
                "start": "2026-03-01T09:00:00Z",
                "end_time": "2026-03-01T09:30:00Z",
                "duration_minutes": 30.0,
                "span_count": 4,
                "switch_count": 3,
                "cycle_count": 2,
                "dominant_mode": "coding",
                "dominant_project": "sinity-lynchpin",
                "context_a_app": "zed",
                "context_a_title": "packet_builders.py",
                "context_b_app": "browser",
                "context_b_title": "docs",
            },
            {
                "start": "2026-03-02T10:00:00Z",
                "end_time": "2026-03-02T10:45:00Z",
                "duration_minutes": 45.0,
                "span_count": 6,
                "switch_count": 5,
                "cycle_count": 3,
                "dominant_mode": "coding",
                "dominant_project": "sinex",
                "context_a_app": "zed",
                "context_a_title": "worker.rs",
                "context_b_app": "browser",
                "context_b_title": "crate docs",
            },
        ]
    )

    assert packets[0]["dominant_project"] == "sinex"
    assert packets[0]["contexts"] == ["zed :: worker.rs", "browser :: crate docs"]
    assert packets[1]["dominant_project"] == "sinity-lynchpin"
