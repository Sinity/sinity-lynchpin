from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import duckdb

from lynchpin.substrate.run_steps import (
    measure_phase,
    record_phase_evidence,
    record_run_step,
    reconcile_orphaned_running_steps,
)


def _connect() -> duckdb.DuckDBPyConnection:
    from lynchpin.substrate.connection import apply_schema

    conn = duckdb.connect(":memory:")
    apply_schema(conn)
    return conn


def test_reconcile_marks_stale_running_step_as_orphaned() -> None:
    conn = _connect()
    started = datetime.now(timezone.utc) - timedelta(hours=3)
    record_run_step(
        conn,
        refresh_id="machine-analysis:rolling:today",
        step="promote_machine_tables",
        status="running",
        message="started",
        started_at=started,
    )

    reconciled = reconcile_orphaned_running_steps(
        conn, stale_before=datetime.now(timezone.utc) - timedelta(hours=1)
    )

    assert reconciled == 1
    rows = conn.execute(
        "SELECT status, started_at FROM substrate_run_step "
        "WHERE refresh_id = 'machine-analysis:rolling:today' AND step = 'promote_machine_tables' "
        "ORDER BY recorded_at"
    ).fetchall()
    assert [r[0] for r in rows] == ["running", "orphaned"]
    # The orphaned row preserves the original started_at, not "now".
    assert rows[1][1] == started


def test_reconcile_leaves_fresh_running_step_alone() -> None:
    conn = _connect()
    record_run_step(
        conn,
        refresh_id="current-state:2026-08-13:2026-08-14:all",
        step="promote_machine_tables",
        status="running",
        started_at=datetime.now(timezone.utc),
    )

    reconciled = reconcile_orphaned_running_steps(
        conn, stale_before=datetime.now(timezone.utc) - timedelta(hours=1)
    )

    assert reconciled == 0
    status = conn.execute(
        "SELECT status FROM substrate_run_step ORDER BY recorded_at DESC LIMIT 1"
    ).fetchone()[0]
    assert status == "running"


def test_reconcile_leaves_completed_step_alone() -> None:
    conn = _connect()
    started = datetime.now(timezone.utc) - timedelta(hours=3)
    record_run_step(
        conn,
        refresh_id="machine-analysis:rolling:today",
        step="promote_machine_tables",
        status="running",
        started_at=started,
    )
    record_run_step(
        conn,
        refresh_id="machine-analysis:rolling:today",
        step="promote_machine_tables",
        status="success",
        row_count=803,
        started_at=started,
        finished_at=started + timedelta(minutes=2),
    )

    reconciled = reconcile_orphaned_running_steps(
        conn, stale_before=datetime.now(timezone.utc) - timedelta(hours=1)
    )

    assert reconciled == 0
    rows = conn.execute(
        "SELECT status FROM substrate_run_step ORDER BY recorded_at"
    ).fetchall()
    assert [r[0] for r in rows] == ["running", "success"]


def test_reconcile_handles_multiple_orphaned_steps_independently() -> None:
    conn = _connect()
    stale = datetime.now(timezone.utc) - timedelta(hours=3)
    fresh = datetime.now(timezone.utc)
    record_run_step(conn, refresh_id="r1", step="a", status="running", started_at=stale)
    record_run_step(conn, refresh_id="r1", step="b", status="running", started_at=fresh)
    record_run_step(conn, refresh_id="r2", step="a", status="running", started_at=stale)

    reconciled = reconcile_orphaned_running_steps(
        conn, stale_before=datetime.now(timezone.utc) - timedelta(hours=1)
    )

    assert reconciled == 2
    latest_by_step = dict(
        conn.execute(
            """
            SELECT refresh_id || ':' || step, status
            FROM (
                SELECT refresh_id, step, status,
                       row_number() OVER (PARTITION BY refresh_id, step ORDER BY recorded_at DESC) AS rn
                FROM substrate_run_step
            )
            WHERE rn = 1
            """
        ).fetchall()
    )
    assert latest_by_step == {"r1:a": "orphaned", "r1:b": "running", "r2:a": "orphaned"}


def test_phase_evidence_persists_typed_metrics_and_process_fallback(monkeypatch) -> None:
    import lynchpin.substrate.run_steps as run_steps

    monkeypatch.setattr(run_steps, "_cgroup_io_bytes", lambda: None)
    conn = _connect()
    with measure_phase("source_reads") as measurement:
        _ = sum(range(100))

    record_phase_evidence(
        conn,
        refresh_id="incremental-fixture",
        measurement=measurement,
        metrics=({"name": "completed_materializer_steps", "unit": "steps", "value": 3},),
    )

    step, status, message, row_count, started_at, finished_at = conn.execute(
        "SELECT step, status, message, row_count, started_at, finished_at "
        "FROM substrate_run_step"
    ).fetchone()
    payload = json.loads(message)
    assert step == "incremental_source_reads"
    assert status == "ok"
    assert row_count is None
    assert started_at <= finished_at
    assert payload["schema"] == "lynchpin.incremental-phase.v2"
    assert payload["phase"] == "source_reads"
    assert payload["metrics"] == [
        {"name": "completed_materializer_steps", "unit": "steps", "value": 3}
    ]
    assert payload["wall_seconds"] >= 0
    assert payload["cpu_user_seconds"] >= 0
    assert payload["cpu_system_seconds"] >= 0
    assert payload["io"]["attribution"] == "process"
    assert payload["io"]["scope"] == "process:self"
    assert payload["io"]["read_bytes"] is None or payload["io"]["read_bytes"] >= 0
    assert payload["io"]["write_bytes"] is None or payload["io"]["write_bytes"] >= 0


def test_phase_evidence_prefers_cgroup_unit_io(monkeypatch) -> None:
    import lynchpin.substrate.run_steps as run_steps

    samples = iter(
        (
            {"attribution": "cgroup-dedicated", "scope": "phase-local:cgroup:/system.slice/lynchpin.service", "unit": "lynchpin.service", "read_bytes": 11, "write_bytes": 13},
            {"attribution": "cgroup-dedicated", "scope": "phase-local:cgroup:/system.slice/lynchpin.service", "unit": "lynchpin.service", "read_bytes": 19, "write_bytes": 23},
        )
    )
    monkeypatch.setattr(run_steps, "_cgroup_io_bytes", lambda: next(samples))

    with measure_phase("graph_compute") as measurement:
        pass

    payload = measurement.payload()
    assert payload["io"] == {
        "attribution": "cgroup-dedicated",
        "scope": "phase-local:cgroup:/system.slice/lynchpin.service",
        "unit": "lynchpin.service",
        "read_bytes": 8,
        "write_bytes": 10,
    }


def test_phase_evidence_labels_shared_cgroup_as_aggregate(monkeypatch) -> None:
    import lynchpin.substrate.run_steps as run_steps

    samples = iter(
        (
            {"attribution": "cgroup-shared", "scope": "shared-cgroup-aggregate:/user.slice", "unit": "user.slice", "read_bytes": 11, "write_bytes": 13},
            {"attribution": "cgroup-shared", "scope": "shared-cgroup-aggregate:/user.slice", "unit": "user.slice", "read_bytes": 19, "write_bytes": 23},
        )
    )
    monkeypatch.setattr(run_steps, "_cgroup_io_bytes", lambda: next(samples))

    with measure_phase("graph_compute") as measurement:
        pass

    assert measurement.payload()["io"] == {
        "attribution": "cgroup-shared",
        "scope": "shared-cgroup-aggregate:/user.slice",
        "unit": "user.slice",
        "read_bytes": 8,
        "write_bytes": 10,
    }


def test_incremental_phase_receipt_failure_is_degraded_and_visible(monkeypatch, caplog) -> None:
    from types import SimpleNamespace

    from lynchpin.cli import materialize
    from lynchpin.substrate.run_steps import measure_phase

    monkeypatch.setattr(
        "lynchpin.substrate.connection.connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt unavailable")),
    )
    generation = SimpleNamespace(candidate="candidate.duckdb", phase_evidence=[])
    with measure_phase("graph_compute") as measurement:
        pass

    with caplog.at_level("WARNING", logger="lynchpin.cli.materialize"):
        materialize._record_incremental_phase(
            generation,
            measurement,
            metrics=({"name": "graph_promotions", "unit": "refreshes", "value": 1},),
        )

    assert generation.phase_evidence[0]["receipt_status"] == "degraded"
    assert generation.phase_evidence[0]["refresh_id"] == "unknown"
    assert "receipt write failed" in caplog.text


def test_candidate_seed_receipt_failure_is_visible_without_aborting(monkeypatch, caplog) -> None:
    import duckdb
    from pathlib import Path
    from lynchpin.substrate.connection import _record_candidate_phase

    monkeypatch.setattr(duckdb, "connect", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("seed receipt unavailable")))
    with measure_phase("candidate_write") as measurement:
        pass

    with caplog.at_level("WARNING", logger="lynchpin.substrate.connection"):
        payload = _record_candidate_phase(
            Path("candidate.duckdb"),
            refresh_id="candidate-attempt",
            receipt_refresh_id="publication-refresh",
            measurement=measurement,
            metrics=({"name": "candidate_generation_size", "unit": "bytes", "value": 1},),
        )

    assert payload["receipt_status"] == "degraded"
    assert payload["refresh_id"] == "publication-refresh"
    assert "publication-refresh" in caplog.text


def test_candidate_attempt_receipt_failure_is_visible_after_canonical_work(monkeypatch, caplog) -> None:
    import duckdb
    from pathlib import Path
    from types import SimpleNamespace
    from lynchpin.substrate.connection import _bind_candidate_attempt_evidence

    monkeypatch.setattr(duckdb, "connect", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("attempt receipt unavailable")))
    generation = SimpleNamespace(
        expected_refresh_id="publication-refresh",
        candidate=Path("candidate.duckdb"),
        refresh_id="candidate-attempt",
        seed_mode="reflink",
        seed_source=Path("source.duckdb"),
        seed_logical_rows=0,
        phase_evidence=[],
    )

    with caplog.at_level("WARNING", logger="lynchpin.substrate.connection"):
        _bind_candidate_attempt_evidence(generation)

    assert generation.phase_evidence[-1]["receipt_status"] == "degraded"
    assert generation.phase_evidence[-1]["refresh_id"] == "publication-refresh"
    assert generation.phase_evidence[-1]["published_refresh_id"] == "publication-refresh"
    assert "attempt receipt unavailable" in caplog.text
