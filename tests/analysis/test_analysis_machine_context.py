from __future__ import annotations

from datetime import datetime, timezone

from lynchpin.analysis.machine.context import WorkloadWindow, analyze_machine_context_windows
from lynchpin.substrate.connection import apply_schema, connect


def test_machine_context_joins_partial_episode_overlap(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, source_schema_version,
                load_1m, mem_avail_mb, gap_codes, refresh_id
            ) VALUES
                (?, 'host', 'machine.telemetry', 2, 30, 1000, [], 'r1'),
                (?, 'host', 'machine.telemetry', 2, 31, 1000, [], 'r1')
            """,
            [
                datetime(2026, 5, 1, 12, 1, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 12, 2, tzinfo=timezone.utc),
            ],
        )

    analysis = analyze_machine_context_windows(
        path=db,
        windows=[
            WorkloadWindow(
                source="polylogue_session",
                window_id="conv1",
                started_at=datetime(2026, 5, 1, 12, 0, 30, tzinfo=timezone.utc),
                ended_at=datetime(2026, 5, 1, 12, 1, 30, tzinfo=timezone.utc),
                projects=("sinity-lynchpin",),
                provider="codex",
                work_kind="implementation",
                summary="machine analysis",
            )
        ],
    )

    assert analysis.window_count == 1
    assert analysis.windows_with_machine_episodes == 1
    assert analysis.source_counts == {"polylogue_session": 1}
    assert analysis.episode_kind_counts == {"load_pressure": 1, "memory_pressure": 1}
    window = analysis.windows[0]
    assert window.duration_seconds == 60.0
    assert window.overlap_seconds == 30.0
    assert window.interpretation == "observed overlap with load_pressure, memory_pressure"
    assert {episode.kind for episode in window.episodes} == {"load_pressure", "memory_pressure"}
    assert "episode overlap is observational" in window.caveats[0]
    assert "pressure episode is unattributed" in window.caveats[1]


def test_machine_context_keeps_windows_without_episode_support(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)

    analysis = analyze_machine_context_windows(
        path=db,
        windows=[
            WorkloadWindow(
                source="terminal_session",
                window_id="sh1",
                started_at=datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
                ended_at=datetime(2026, 5, 1, 12, 5, tzinfo=timezone.utc),
                projects=(),
                provider=None,
                work_kind="development:other",
                summary="pytest",
            )
        ],
    )

    assert analysis.window_count == 1
    assert analysis.windows_with_machine_episodes == 0
    assert analysis.episode_kind_counts == {}
    assert analysis.windows[0].interpretation == "no detected machine episode overlap"
    assert "workload window has no project attribution" in analysis.windows[0].caveats
    assert "no detected machine episode overlaps this window" in analysis.windows[0].caveats
