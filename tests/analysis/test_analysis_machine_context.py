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


def test_machine_context_collects_work_observation_windows(tmp_path, monkeypatch):
    from lynchpin.analysis.machine import context as context_mod
    from lynchpin.sources.xtask_history import XtaskInvocation
    from lynchpin.substrate.work_observations import promote_work_observations

    db = tmp_path / "sub.duckdb"
    started = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    ended = datetime(2026, 5, 1, 12, 1, tzinfo=timezone.utc)
    with connect(db) as conn:
        apply_schema(conn)
        promote_work_observations(
            conn,
            refresh_id="r1",
            rows=[
                XtaskInvocation(
                    source_id="xtask:1",
                    command=("check", "clippy"),
                    cwd="/realm/project/sinex",
                    started_at=started,
                    ended_at=ended,
                    duration_s=60.0,
                    status="success",
                    exit_code=0,
                    host="sinnix-prime",
                    project="sinex",
                    git_commit="abc123",
                    git_dirty=True,
                    live_stage="clippy",
                    args_json="[]",
                    cpu_usage_avg=None,
                    memory_usage_max_mb=None,
                    process_cpu_usage_avg=None,
                    process_memory_usage_max_mb=None,
                    root_process_cpu_usage_avg=None,
                    root_process_memory_usage_max_mb=None,
                    shared_nix_daemon_cpu_usage_avg=None,
                    shared_nix_daemon_memory_usage_max_mb=None,
                    shared_nix_build_slice_cpu_usage_avg=None,
                    shared_nix_build_slice_memory_usage_max_mb=None,
                    shared_background_slice_cpu_usage_avg=None,
                    shared_background_slice_memory_usage_max_mb=None,
                    host_cpu_pressure_some_avg10_max=None,
                    host_io_pressure_some_avg10_max=None,
                    host_io_pressure_full_avg10_max=None,
                    host_memory_pressure_some_avg10_max=None,
                    host_memory_pressure_full_avg10_max=None,
                    shm_free_min_mb=None,
                    shm_used_max_mb=None,
                    process_count_max=None,
                    resource_sample_count=None,
                )
            ],
        )

    monkeypatch.setattr(context_mod, "_polylogue_windows", lambda **_: [])
    monkeypatch.setattr(context_mod, "_terminal_windows", lambda **_: [])
    monkeypatch.setattr(context_mod, "_git_windows", lambda **_: [])
    monkeypatch.setattr(context_mod, "_deep_work_windows", lambda **_: [])

    analysis = analyze_machine_context_windows(
        start=started.date(),
        end=started.date(),
        path=db,
    )

    assert analysis.source_counts == {"xtask_history": 1}
    assert analysis.windows[0].projects == ("sinex",)
    assert analysis.windows[0].summary == "check clippy"
