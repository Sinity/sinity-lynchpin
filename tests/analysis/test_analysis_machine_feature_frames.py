from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace


def test_machine_feature_frame_tracks_windows_missingness_and_censoring(tmp_path):
    from lynchpin.analysis.machine.feature_frames import analyze_machine_feature_frames
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.work_observations import (
        promote_work_observation_stages,
        promote_work_observations,
    )

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_work_observations(conn, refresh_id="r1", rows=[
            _invocation("inv1", pressure=0.2),
            _invocation("inv2", pressure=None),
        ])
        promote_work_observation_stages(conn, refresh_id="r1", rows=[
            _stage("stage1", "inv1", "nextest", 12.5, success=True),
            _stage("stage2", "inv2", "nextest", 30.0, success=False),
        ])

    frame = analyze_machine_feature_frames(path=db, refresh_id="r1")

    assert frame.row_count == 2
    assert frame.leakage_status == "ok"
    assert frame.outcome_columns == ("stage.duration_s",)
    assert "host_cpu_pressure_some_avg10_max" in frame.exposure_columns
    assert "stage_name" in frame.covariate_columns
    assert frame.source_refresh_ids == ("r1",)
    assert frame.censored_count == 1
    assert frame.censoring_summary == {"failed_or_cancelled": 1, "observed": 1}
    assert frame.leakage_summary == {"ok": 2}
    assert frame.missingness_summary["host_cpu_pressure_some_avg10_max"] == 1
    assert frame.rows[0].unit_type == "work_observation_stage"
    assert frame.rows[0].outcome_metric == "stage.duration_s"
    assert frame.rows[0].outcome_window_end is not None
    assert frame.rows[0].exposure_policy == "concurrent_context"
    assert frame.rows[0].covariates["stage_name"] == "nextest"
    assert frame.rows[0].missingness["host_cpu_pressure_some_avg10_max"] is False
    assert frame.rows[1].censoring_status == "failed_or_cancelled"
    assert frame.rows[1].missingness["host_cpu_pressure_some_avg10_max"] is True
    assert "concurrent pressure" in frame.rows[0].caveats[0]


def test_machine_feature_frame_supports_invocation_units(tmp_path):
    from lynchpin.analysis.machine.feature_frames import analyze_machine_feature_frames
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.work_observations import promote_work_observations

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_work_observations(conn, refresh_id="r1", rows=[
            _invocation("inv1", pressure=0.2),
            _invocation("inv2", pressure=None),
        ])

    frame = analyze_machine_feature_frames(
        path=db,
        refresh_id="r1",
        unit_type="work_observation",
    )

    assert frame.row_count == 2
    assert frame.unit_type == "work_observation"
    assert frame.outcome_metric == "invocation.duration_s"
    assert frame.outcome_columns == ("invocation.duration_s",)
    assert frame.rows[0].unit_type == "work_observation"
    assert frame.rows[0].parent_unit_id is None
    assert frame.rows[0].covariates["work_kind"] == "xtask_invocation"
    assert frame.rows[0].covariates["cwd"] == "/realm/project/sinex"
    assert frame.rows[0].censoring_status == "observed"
    assert frame.missingness_summary["stage_name"] == 2
    assert frame.missingness_summary["host_cpu_pressure_some_avg10_max"] == 1


def _invocation(source_id: str, *, pressure: float | None) -> SimpleNamespace:
    return SimpleNamespace(
        source_id=source_id,
        work_kind="xtask_invocation",
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
        host_cpu_pressure_some_avg10_max=pressure,
        host_io_pressure_some_avg10_max=None,
        host_io_pressure_full_avg10_max=None,
        host_memory_pressure_some_avg10_max=None,
        host_memory_pressure_full_avg10_max=None,
        shm_free_min_mb=None,
        shm_used_max_mb=None,
        process_count_max=None,
        resource_sample_count=None,
    )


def _stage(source_id: str, invocation_id: str, stage: str, duration: float, *, success: bool) -> SimpleNamespace:
    return SimpleNamespace(
        source_id=source_id,
        invocation_source_id=invocation_id,
        stage_name=stage,
        started_at=datetime(2026, 5, 31, 12, tzinfo=timezone.utc),
        duration_s=duration,
        success=success,
    )
