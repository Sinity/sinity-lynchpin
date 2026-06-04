from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace


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


def test_promote_work_observation_stage_and_test_children(tmp_path):
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.work_observations import (
        promote_work_observation_stages,
        promote_work_observation_test_results,
    )

    db = tmp_path / "sub.duckdb"
    stage = SimpleNamespace(
        source_id="xtask:live:stage:1",
        invocation_source_id="xtask:live:9",
        stage_name="clippy",
        started_at=datetime(2026, 5, 31, 19, 47, tzinfo=timezone.utc),
        duration_s=2.5,
        success=True,
    )
    test = SimpleNamespace(
        source_id="xtask:live:test:2",
        invocation_source_id="xtask:live:9",
        test_name="pkg::mod::test_name",
        package="pkg",
        status="pass",
        duration_s=0.12,
        attempt=1,
        slot_name="slot-a",
        slot_wait_ms=10,
        cleanup_ms=3,
        failure_type=None,
        test_mode="nextest",
        nats_context=None,
    )
    with connect(db) as conn:
        apply_schema(conn)
        assert promote_work_observation_stages(conn, refresh_id="r1", rows=[stage]) == 1
        assert promote_work_observation_test_results(conn, refresh_id="r1", rows=[test]) == 1
        stages = conn.execute(
            "SELECT source_id, invocation_source_id, stage_name, success FROM work_observation_stage"
        ).fetchall()
        tests = conn.execute(
            "SELECT source_id, invocation_source_id, package, test_mode FROM work_observation_test_result"
        ).fetchall()

    assert stages == [("xtask:live:stage:1", "xtask:live:9", "clippy", True)]
    assert tests == [("xtask:live:test:2", "xtask:live:9", "pkg", "nextest")]


def test_promote_polylogue_devtools_observations_round_trip(tmp_path):
    from lynchpin.sources.polylogue_devtools import PolylogueDevtoolsInvocation
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.work_observations import (
        load_work_observations,
        promote_polylogue_devtools_observations,
    )

    row = PolylogueDevtoolsInvocation(
        source="polylogue_devtools",
        source_id="polylogue:log:run",
        work_kind="polylogue_log_run",
        command=("run-all",),
        cwd="/realm/project/polylogue",
        started_at=datetime(2026, 4, 12, 0, 42, tzinfo=timezone.utc),
        ended_at=datetime(2026, 4, 12, 0, 45, tzinfo=timezone.utc),
        duration_s=180.0,
        status="unknown",
        exit_code=None,
        host="sinnix-prime",
        project="polylogue",
        git_commit="abc123",
        git_dirty=False,
        live_stage="run-all",
        args_json="{}",
        cpu_usage_avg=None,
        memory_usage_max_mb=None,
        process_cpu_usage_avg=20.0,
        process_memory_usage_max_mb=4.0,
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
        process_count_max=4,
        resource_sample_count=2,
    )
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        assert promote_polylogue_devtools_observations(conn, refresh_id="r1", rows=[row]) == 1
        loaded = load_work_observations(conn, refresh_id="r1")
        resource = conn.execute(
            "SELECT process_cpu_usage_avg, process_memory_usage_max_mb FROM work_observation"
        ).fetchone()

    assert loaded[0]["source"] == "polylogue_devtools"
    assert loaded[0]["work_kind"] == "polylogue_log_run"
    assert loaded[0]["project"] == "polylogue"
    assert resource == (20.0, 4.0)


def test_two_sources_coexist_in_work_observation_under_one_refresh_id(tmp_path):
    """Regression: xtask + polylogue devtools share the work_observation table
    under one refresh_id. promote_rows deletes by refresh_id alone, so the
    second writer must NOT delete the first's rows. The refresh deletes once
    and appends both sources with delete_existing=False."""
    from lynchpin.sources.polylogue_devtools import PolylogueDevtoolsInvocation
    from lynchpin.sources.xtask_history import XtaskInvocation
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.work_observations import (
        load_work_observations,
        promote_polylogue_devtools_observations,
        promote_work_observations,
    )

    def _none_resource() -> dict:
        return dict.fromkeys(
            (
                "cpu_usage_avg", "memory_usage_max_mb",
                "process_memory_usage_max_mb",
                "root_process_cpu_usage_avg", "root_process_memory_usage_max_mb",
                "shared_nix_daemon_cpu_usage_avg", "shared_nix_daemon_memory_usage_max_mb",
                "shared_nix_build_slice_cpu_usage_avg", "shared_nix_build_slice_memory_usage_max_mb",
                "shared_background_slice_cpu_usage_avg", "shared_background_slice_memory_usage_max_mb",
                "host_cpu_pressure_some_avg10_max", "host_io_pressure_some_avg10_max",
                "host_io_pressure_full_avg10_max", "host_memory_pressure_some_avg10_max",
                "host_memory_pressure_full_avg10_max", "shm_free_min_mb", "shm_used_max_mb",
            ),
            None,
        )

    xtask = XtaskInvocation(
        source_id="xtask:live:1",
        command=("test",),
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
        live_stage="test",
        args_json="[]",
        process_cpu_usage_avg=3.5,
        process_count_max=11,
        resource_sample_count=6,
        **_none_resource(),
    )
    polylogue = PolylogueDevtoolsInvocation(
        source="polylogue_devtools",
        source_id="polylogue:log:run",
        work_kind="polylogue_log_run",
        command=("run-all",),
        cwd="/realm/project/polylogue",
        started_at=datetime(2026, 4, 12, 0, 42, tzinfo=timezone.utc),
        ended_at=datetime(2026, 4, 12, 0, 45, tzinfo=timezone.utc),
        duration_s=180.0,
        status="unknown",
        exit_code=None,
        host="sinnix-prime",
        project="polylogue",
        git_commit="abc123",
        git_dirty=False,
        live_stage="run-all",
        args_json="{}",
        process_cpu_usage_avg=20.0,
        process_count_max=4,
        resource_sample_count=2,
        **_none_resource(),
    )

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        # Mirror the refresh: one delete, then both sources append.
        conn.execute("DELETE FROM work_observation WHERE refresh_id = ?", ["r1"])
        assert promote_work_observations(
            conn, refresh_id="r1", rows=[xtask], delete_existing=False
        ) == 1
        assert promote_polylogue_devtools_observations(
            conn, refresh_id="r1", rows=[polylogue], delete_existing=False
        ) == 1
        loaded = load_work_observations(conn, refresh_id="r1")
        # The xtask telemetry survived the second writer — the bug was that it did not.
        telemetry = conn.execute(
            "SELECT process_cpu_usage_avg FROM work_observation WHERE source = 'xtask_history'"
        ).fetchall()

    sources = {row["source"] for row in loaded}
    assert sources == {"xtask_history", "polylogue_devtools"}
    assert telemetry == [(3.5,)]
