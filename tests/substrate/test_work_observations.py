from __future__ import annotations

from datetime import datetime, timezone


def test_promote_work_observations_round_trip(tmp_path):
    from lynchpin.sources.xtask_history import XtaskInvocation
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.work_observations import (
        load_work_observations,
        promote_work_observations,
    )

    row = XtaskInvocation(
        source_id="xtask:1",
        command=("check", "clippy"),
        cwd="/realm/project/sinex",
        started_at=datetime(2026, 5, 31, 19, 47, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 31, 19, 48, tzinfo=timezone.utc),
        duration_s=60.0,
        status="success",
        exit_code=0,
        host="sinnix-prime",
        project="sinex",
        git_commit="abc123",
        git_dirty=True,
        live_stage="clippy",
        args_json='["--all"]',
        cpu_usage_avg=42.0,
        memory_usage_max_mb=512.0,
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
        host_cpu_pressure_some_avg10_max=0.1,
        host_io_pressure_some_avg10_max=0.2,
        host_io_pressure_full_avg10_max=0.0,
        host_memory_pressure_some_avg10_max=0.3,
        host_memory_pressure_full_avg10_max=0.0,
        shm_free_min_mb=1024.0,
        shm_used_max_mb=2048.0,
        process_count_max=11,
        resource_sample_count=6,
    )
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        assert promote_work_observations(conn, refresh_id="r1", rows=[row]) == 1
        loaded = load_work_observations(conn, refresh_id="r1")

    assert loaded[0]["source_id"] == "xtask:1"
    assert loaded[0]["project"] == "sinex"
    assert loaded[0]["command"] == ["check", "clippy"]
    assert loaded[0]["status"] == "success"
