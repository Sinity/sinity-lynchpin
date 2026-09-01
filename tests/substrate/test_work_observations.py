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


def test_promote_agentctl_observations_is_idempotent_and_keeps_explicit_refs(tmp_path):
    from lynchpin.sources.agentctl import read_observation_snapshot
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.work_observations import (
        promote_agentctl_observations,
        promote_agentctl_receipt_refs,
    )

    envelope = {
        "schema": 1,
        "ok": True,
        "payload": {"kind": "inline", "value": {"snapshot": {"ordering": "created_at_desc_job_id_desc", "ceiling": []}, "truncated": False, "jobs": [{
            "job_id": "22222222-2222-2222-2222-222222222222", "kind": "declared-operation", "project_id": "sinex", "operation": "check", "created_at": "2026-08-24T00:00:00+00:00", "timeout_seconds": 60,
            "artifacts": {"log": {"ref": "sinnix://jobs/222/log"}, "result": None},
            "semantic_receipts": [{"owner": "polylogue", "ref": "polylogue://receipts/222"}],
            "state": {"phase": "cancelled", "terminal": True, "observed_at": "2026-08-24T00:01:00+00:00", "systemd": {"MemoryPeak": "1048576", "ExecMainStatus": "143"}},
        }]}},
    }
    rows = read_observation_snapshot(loader=lambda: envelope).observations
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        assert promote_agentctl_observations(conn, refresh_id="r1", rows=rows) == 1
        assert promote_agentctl_observations(conn, refresh_id="r1", rows=rows) == 1
        assert promote_agentctl_receipt_refs(conn, refresh_id="r1", rows=rows) == 1
        assert promote_agentctl_receipt_refs(conn, refresh_id="r1", rows=rows) == 1
        observation = conn.execute("SELECT source_revision, source_generation, artifact_refs, outcome_known, cancellation_requested, recovery_state FROM work_observation").fetchone()
        refs = conn.execute("SELECT receipt_owner, receipt_ref FROM work_observation_receipt_ref").fetchall()
        count = conn.execute("SELECT COUNT(*) FROM work_observation").fetchone()[0]

    assert count == 1
    assert observation[0].startswith("sha256:")
    assert '"contract_schema":1' in observation[1]
    assert observation[2] == '["sinnix://jobs/222/log"]'
    assert observation[3:] == (True, True, None)
    assert refs == [("polylogue", "polylogue://receipts/222")]


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
