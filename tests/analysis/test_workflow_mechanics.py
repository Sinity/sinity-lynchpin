from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from lynchpin.analysis.workflow_mechanics import (
    analyze_workflow_mechanics,
    write_workflow_mechanics_report,
)
from lynchpin.substrate.connection import apply_schema, connect
from lynchpin.substrate.work_observations import promote_work_observations


UTC = timezone.utc


def test_workflow_mechanics_detects_retry_chain(tmp_path) -> None:
    db = tmp_path / "sub.duckdb"
    base = datetime(2026, 5, 31, 12, tzinfo=UTC)
    with connect(db) as conn:
        apply_schema(conn)
        promote_work_observations(conn, refresh_id="r1", rows=[
            _invocation("i1", base, status="failed", exit_code=1),
            _invocation("i2", base + timedelta(minutes=3), status="failed", exit_code=1),
            _invocation("i3", base + timedelta(minutes=6), status="success", exit_code=0),
            _invocation("i4", base + timedelta(hours=2), status="success", exit_code=0),
        ])

    report = analyze_workflow_mechanics(path=str(db), refresh_id="r1", retry_gap_min=10)
    payload = report.to_json()

    assert report.invocation_count == 4
    assert report.failure_count == 2
    assert report.retry_chain_count == 1
    assert payload["retry_chains"][0]["source_ids"] == ["i1", "i2", "i3"]
    summary = payload["command_summaries"][0]
    assert summary["command_key"] == "xtask test"
    assert summary["failure_count"] == 2
    assert summary["retry_chain_count"] == 1


def test_write_workflow_mechanics_report_persists_artifact(tmp_path) -> None:
    db = tmp_path / "sub.duckdb"
    out = tmp_path / "workflow_mechanics.json"
    base = datetime(2026, 5, 31, 12, tzinfo=UTC)
    with connect(db) as conn:
        apply_schema(conn)
        promote_work_observations(conn, refresh_id="r1", rows=[
            _invocation("i1", base, status="failed", exit_code=1),
            _invocation("i2", base + timedelta(minutes=3), status="success", exit_code=0),
        ])

    write_workflow_mechanics_report(out, path=str(db), refresh_id="r1")
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert payload["invocation_count"] == 2
    assert payload["retry_chain_count"] == 1


def _invocation(
    source_id: str,
    started_at: datetime,
    *,
    status: str,
    exit_code: int,
) -> SimpleNamespace:
    return SimpleNamespace(
        source_id=source_id,
        work_kind="xtask_invocation",
        command=("xtask", "test"),
        cwd="/realm/project/sinex",
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=30),
        duration_s=30.0,
        status=status,
        exit_code=exit_code,
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
