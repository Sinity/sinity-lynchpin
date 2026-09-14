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
        host_block_read_mib_delta=12.5,
        host_block_write_mib_delta=3.25,
        host_block_read_iops_avg=100.0,
        host_block_write_iops_avg=25.0,
        host_block_busiest_device="nvme0n1",
        host_block_busiest_device_total_mib_delta=15.75,
        host_block_busiest_device_read_iops_avg=90.0,
        host_block_busiest_device_write_iops_avg=20.0,
        host_block_busiest_device_weighted_io_ms_per_s=250.0,
    )
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        assert promote_work_observations(conn, refresh_id="r1", rows=[row]) == 1
        loaded = load_work_observations(conn, refresh_id="r1")
        block_io = conn.execute(
            """
            SELECT host_block_busiest_device,
                   host_block_read_mib_delta,
                   host_block_busiest_device_weighted_io_ms_per_s
            FROM work_observation
            """
        ).fetchone()

    assert loaded[0]["source_id"] == "xtask:1"
    assert loaded[0]["project"] == "sinex"
    assert loaded[0]["command"] == ["check", "clippy"]
    assert loaded[0]["status"] == "success"
    assert block_io == ("nvme0n1", 12.5, 250.0)


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
        io_full_avg10=8.0,
        cpu_some_avg10=2.0,
        memory_some_avg10=0.5,
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
        assert (
            promote_work_observation_test_results(conn, refresh_id="r1", rows=[test])
            == 1
        )
        stages = conn.execute(
            "SELECT source_id, invocation_source_id, stage_name, success FROM work_observation_stage"
        ).fetchall()
        tests = conn.execute(
            "SELECT source_id, invocation_source_id, package, test_mode FROM work_observation_test_result"
        ).fetchall()

    assert stages == [("xtask:live:stage:1", "xtask:live:9", "clippy", True)]
    assert tests == [("xtask:live:test:2", "xtask:live:9", "pkg", "nextest")]


def test_promote_agentctl_observations_is_idempotent(tmp_path):
    from lynchpin.sources.agentctl import read_observation_snapshot
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.work_observations import (
        promote_agentctl_observations,
        promote_agentctl_receipt_refs,
    )

    rows = read_observation_snapshot(loader=lambda: [{
        "job_id": 222, "kind": "declared-operation", "project": "sinex", "operation": "check",
        "group": "normal", "phase": "cancelled", "terminal": True, "result": "Killed",
        "exit_code": 143, "enqueued_at": "2026-08-24T00:00:00+00:00",
        "started_at": "2026-08-24T00:00:01+00:00", "ended_at": "2026-08-24T00:01:00+00:00",
    }]).observations
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        assert promote_agentctl_observations(conn, refresh_id="r1", rows=rows) == 1
        assert promote_agentctl_observations(conn, refresh_id="r1", rows=rows) == 1
        assert promote_agentctl_receipt_refs(conn, refresh_id="r1", rows=rows) == 0
        assert promote_agentctl_receipt_refs(conn, refresh_id="r1", rows=rows) == 0
        observation = conn.execute("SELECT source_revision, source_generation, artifact_refs, outcome_known, cancellation_requested, recovery_state FROM work_observation").fetchone()
        refs = conn.execute("SELECT receipt_owner, receipt_ref FROM work_observation_receipt_ref").fetchall()
        count = conn.execute("SELECT COUNT(*) FROM work_observation").fetchone()[0]

    assert count == 1
    assert observation[0].startswith("sha256:")
    assert '"contract_schema":3' in observation[1]
    assert observation[2] == '[]'
    assert observation[3:] == (True, True, None)
    assert refs == []


def test_work_observation_promotion_can_append_under_one_refresh_id(tmp_path):
    """Promotion can append rows after a single refresh-scoped delete."""
    from lynchpin.sources.xtask_history import XtaskInvocation
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.work_observations import (
        load_work_observations,
        promote_work_observations,
    )

    def _none_resource() -> dict:
        return dict.fromkeys(
            (
                "cpu_usage_avg",
                "memory_usage_max_mb",
                "process_memory_usage_max_mb",
                "root_process_cpu_usage_avg",
                "root_process_memory_usage_max_mb",
                "shared_nix_daemon_cpu_usage_avg",
                "shared_nix_daemon_memory_usage_max_mb",
                "shared_nix_build_slice_cpu_usage_avg",
                "shared_nix_build_slice_memory_usage_max_mb",
                "shared_background_slice_cpu_usage_avg",
                "shared_background_slice_memory_usage_max_mb",
                "host_cpu_pressure_some_avg10_max",
                "host_io_pressure_some_avg10_max",
                "host_io_pressure_full_avg10_max",
                "host_memory_pressure_some_avg10_max",
                "host_memory_pressure_full_avg10_max",
                "shm_free_min_mb",
                "shm_used_max_mb",
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
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        # Mirror the materialization: one delete, then source rows append.
        conn.execute("DELETE FROM work_observation WHERE refresh_id = ?", ["r1"])
        assert (
            promote_work_observations(
                conn, refresh_id="r1", rows=[xtask], delete_existing=False
            )
            == 1
        )
        loaded = load_work_observations(conn, refresh_id="r1")
        # The xtask telemetry is retained after appending rows.
        telemetry = conn.execute(
            "SELECT process_cpu_usage_avg FROM work_observation WHERE source = 'xtask_history'"
        ).fetchall()

    sources = {row["source"] for row in loaded}
    assert sources == {"xtask_history"}
    assert telemetry == [(3.5,)]


def test_agentctl_operation_survives_the_substrate_round_trip(tmp_path):
    """work_observation persists and returns the AgentCTL operation name.

    Anti-vacuity: dropping the ``operation`` column from the DDL, from
    ``_WORK_OBSERVATION_COLUMNS``/the extractor, or from the
    ``load_work_observations`` projection makes this red. Without it, per-
    operation duration questions ("how long does verify_all take") are only
    answerable by joining externally on the job id.
    """
    from lynchpin.sources.agentctl import read_observation_snapshot
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.work_observations import (
        load_work_observations,
        promote_agentctl_observations,
    )

    rows = read_observation_snapshot(loader=lambda: [{
        "job_id": 333, "kind": "declared-operation", "project": "polylogue",
        "operation": "verify_all", "group": "normal", "phase": "succeeded",
        "terminal": True, "result": "Success", "exit_code": 0,
        "enqueued_at": "2026-08-24T00:00:00+00:00",
        "started_at": "2026-08-24T00:00:01+00:00",
        "ended_at": "2026-08-24T00:05:01+00:00",
    }]).observations

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        assert promote_agentctl_observations(conn, refresh_id="r1", rows=rows) == 1
        stored = conn.execute(
            "SELECT operation, duration_s FROM work_observation WHERE project = 'polylogue'"
        ).fetchall()
        loaded = load_work_observations(conn, refresh_id="r1")

    assert stored == [("verify_all", 300.0)]
    assert [row["operation"] for row in loaded] == ["verify_all"]


def test_daily_work_observation_series_separates_operations(tmp_path):
    """Daily rollups group by operation instead of collapsing agentctl work.

    Anti-vacuity: agentctl rows all carry an empty ``command``, so without
    ``operation`` in the SELECT/GROUP BY every operation for a project and day
    collapses into one undifferentiated row and this test sees a single row
    with the pooled count.
    """
    from lynchpin.analysis.machine.work_observations import daily_work_observation_series
    from lynchpin.sources.agentctl import read_observation_snapshot
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.work_observations import promote_agentctl_observations

    def _job(job_id: int, operation: str, end: str) -> dict:
        return {
            "job_id": job_id, "kind": "declared-operation", "project": "polylogue",
            "operation": operation, "group": "normal", "phase": "succeeded",
            "terminal": True, "result": "Success", "exit_code": 0,
            "enqueued_at": "2026-08-24T00:00:00+00:00",
            "started_at": "2026-08-24T00:00:00+00:00", "ended_at": end,
        }

    rows = read_observation_snapshot(loader=lambda: [
        _job(1, "verify_all", "2026-08-24T00:10:00+00:00"),
        _job(2, "pytest_focused", "2026-08-24T00:00:30+00:00"),
        _job(3, "pytest_focused", "2026-08-24T00:00:10+00:00"),
    ]).observations

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_agentctl_observations(conn, refresh_id="r1", rows=rows)
        series = daily_work_observation_series(conn, refresh_id="r1")
        focused = daily_work_observation_series(
            conn, refresh_id="r1", operation="pytest_focused"
        )

    by_operation = {row.operation: row for row in series}
    assert set(by_operation) == {"verify_all", "pytest_focused"}
    assert by_operation["verify_all"].observation_count == 1
    assert by_operation["verify_all"].max_duration_s == 600.0
    assert by_operation["pytest_focused"].observation_count == 2
    assert by_operation["pytest_focused"].max_duration_s == 30.0
    assert [row.operation for row in focused] == ["pytest_focused"]
