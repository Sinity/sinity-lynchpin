from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace


def test_machine_work_observation_read_models(tmp_path):
    from lynchpin.analysis.machine.work_observations import (
        daily_work_observation_series,
        failure_taxonomy_summary,
        stage_timing_summary,
        test_duration_summary,
    )
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.work_observations import (
        promote_work_observation_stages,
        promote_work_observation_test_results,
        promote_work_observations,
    )

    invocation = SimpleNamespace(
        source_id="xtask:live:1",
        command=("check",),
        cwd="/realm/project/sinex",
        started_at=datetime(2026, 5, 31, 12, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 31, 12, 1, tzinfo=timezone.utc),
        duration_s=60.0,
        status="success",
        exit_code=0,
        host="sinnix-prime",
        project="sinex",
        git_commit="abc123",
        git_dirty=False,
        live_stage=None,
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
    stage = SimpleNamespace(
        source_id="xtask:live:stage:1",
        invocation_source_id="xtask:live:1",
        stage_name="test",
        started_at=datetime(2026, 5, 31, 12, tzinfo=timezone.utc),
        duration_s=45.0,
        success=True,
    )
    failed_stage = SimpleNamespace(
        source_id="xtask:live:stage:2",
        invocation_source_id="xtask:live:1",
        stage_name="deploy",
        started_at=datetime(2026, 5, 31, 12, tzinfo=timezone.utc),
        duration_s=10.0,
        success=False,
    )
    test = SimpleNamespace(
        source_id="xtask:live:test:1",
        invocation_source_id="xtask:live:1",
        test_name="pkg::test",
        package="pkg",
        status="pass",
        duration_s=0.2,
        attempt=1,
        slot_name=None,
        slot_wait_ms=None,
        cleanup_ms=None,
        failure_type=None,
        test_mode="nextest",
        nats_context=None,
    )
    failed_test = SimpleNamespace(
        source_id="xtask:live:test:2",
        invocation_source_id="xtask:live:1",
        test_name="pkg::failing",
        package="pkg",
        status="fail",
        duration_s=1.2,
        attempt=1,
        slot_name=None,
        slot_wait_ms=None,
        cleanup_ms=None,
        failure_type="assertion",
        test_mode="nextest",
        nats_context=None,
    )

    with connect(tmp_path / "sub.duckdb") as conn:
        apply_schema(conn)
        promote_work_observations(conn, refresh_id="r1", rows=[invocation])
        promote_work_observation_stages(conn, refresh_id="r1", rows=[stage, failed_stage])
        promote_work_observation_test_results(conn, refresh_id="r1", rows=[test, failed_test])

        daily = daily_work_observation_series(
            conn,
            refresh_id="r1",
            project="sinex",
            command_contains="check",
        )
        stages = stage_timing_summary(conn, refresh_id="r1")
        tests = test_duration_summary(conn, refresh_id="r1")
        failures = failure_taxonomy_summary(conn, refresh_id="r1")

    assert daily[0].observation_count == 1
    assert daily[0].command == ("check",)
    assert daily[0].median_duration_s == 60.0
    assert stages[0].stage_name == "test"
    assert stages[0].success_count == 1
    assert tests[0].package == "pkg"
    assert tests[0].test_count == 1
    assert {row.failure_kind for row in failures} == {"stage", "test"}
    assert any(row.stage_name == "deploy" for row in failures)
    assert any(row.package == "pkg" and row.failure_type == "assertion" for row in failures)


def test_write_work_observation_analysis(tmp_path):
    from lynchpin.analysis.machine.work_observations import write_work_observation_analysis
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.work_observations import promote_work_observations

    db = tmp_path / "sub.duckdb"
    out = tmp_path / "machine_work_observations.json"
    invocation = SimpleNamespace(
        source_id="xtask:live:1",
        command=("check",),
        cwd="/realm/project/sinex",
        started_at=datetime(2026, 5, 31, 12, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 31, 12, 1, tzinfo=timezone.utc),
        duration_s=60.0,
        status="success",
        exit_code=0,
        host="sinnix-prime",
        project="sinex",
        git_commit="abc123",
        git_dirty=False,
        live_stage=None,
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
    with connect(db) as conn:
        apply_schema(conn)
        promote_work_observations(conn, refresh_id="r1", rows=[invocation])

    payload = write_work_observation_analysis(out, path=db, refresh_id="r1")

    assert out.exists()
    assert payload["daily"][0]["observation_count"] == 1
    assert payload["sinex_check_daily"][0]["median_duration_s"] == 60.0
    assert "not a controlled benchmark" in payload["caveats"][0]
