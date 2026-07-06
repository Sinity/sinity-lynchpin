from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lynchpin.analysis.machine.pressure_incidents import analyze_machine_pressure_incidents
from lynchpin.substrate.connection import apply_schema, connect


def _ts(minute: int, second: int = 0) -> datetime:
    return datetime(2026, 7, 6, 0, minute, second, tzinfo=timezone.utc)


def _insert(conn: Any, table: str, **cols: Any) -> None:
    names = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO {table} ({names}) VALUES ({placeholders})", list(cols.values()))


def _metric(conn: Any, minute: int, second: int = 0, **overrides: Any) -> None:
    cols: dict[str, Any] = {
        "observed_at": _ts(minute, second),
        "host": "sinnix-prime",
        "source": "machine.telemetry",
        "source_schema_version": 5,
        "gap_codes": [],
        "refresh_id": "r1",
        "memory_psi_some_avg10": 2.0,
        "io_psi_some_avg10": 2.0,
        "vmstat_workingset_refault_file": 1000,
        "vmstat_oom_kill": 0,
    }
    cols.update(overrides)
    _insert(conn, "machine_metric_sample", **cols)


def test_sustained_memory_psi_spike_is_detected_and_enriched(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)

        # Two quiet samples before the incident.
        _metric(conn, 15, 0)
        _metric(conn, 18, 0)

        # Sustained memory PSI spike: 3 consecutive samples >= threshold,
        # with a growing vmstat_workingset_refault_file counter (reclaim
        # activity during the incident).
        _metric(conn, 20, 0, memory_psi_some_avg10=45.0, vmstat_workingset_refault_file=1000, vmstat_oom_kill=0)
        _metric(conn, 21, 0, memory_psi_some_avg10=60.0, vmstat_workingset_refault_file=5000, vmstat_oom_kill=0)
        _metric(conn, 22, 0, memory_psi_some_avg10=55.0, vmstat_workingset_refault_file=9000, vmstat_oom_kill=1)

        # Quiet again afterward.
        _metric(conn, 30, 0)

        # cgroup memory grew during the window (a workload's slice bloated).
        _insert(
            conn, "machine_cgroup_memory_sample",
            observed_at=_ts(19, 30), host="sinnix-prime", boot_id="boot-a",
            source_schema_version=5, label="nix-daemon.service", scope="system",
            control_group="/system.slice/nix-daemon.service",
            memory_current_bytes=500_000_000, refresh_id="r1",
        )
        _insert(
            conn, "machine_cgroup_memory_sample",
            observed_at=_ts(22, 30), host="sinnix-prime", boot_id="boot-a",
            source_schema_version=5, label="nix-daemon.service", scope="system",
            control_group="/system.slice/nix-daemon.service",
            memory_current_bytes=3_500_000_000, refresh_id="r1",
        )

        # An earlyoom kill landed mid-incident.
        _insert(
            conn, "machine_kill_event",
            observed_at=_ts(21, 30), host="sinnix-prime", boot_id="boot-a",
            source_schema_version=5, killer="earlyoom", victim_comm="cc1plus",
            victim_pid=12345, victim_rss_mib=2048, oom_score=900,
            raw_line="earlyoom: sending SIGKILL to process 12345 cc1plus",
            source_row_id=1, refresh_id="r1",
        )

    analysis = analyze_machine_pressure_incidents(
        start=_ts(0).date(), end=_ts(0).date(), path=db,
        include_workloads=False,
    )

    assert analysis.incident_count == 1
    incident = analysis.incidents[0]
    assert incident.host == "sinnix-prime"
    assert incident.focus == "memory"
    assert incident.sample_count == 3
    assert incident.started_at == _ts(20, 0)
    assert incident.ended_at == _ts(22, 0)
    assert incident.peak_memory_psi_some_avg10 == 60.0

    refault_delta = next(
        d for d in incident.vmstat_deltas if d.field == "vmstat_workingset_refault_file"
    )
    assert refault_delta.delta == 8000  # 9000 - 1000

    oom_delta = next(d for d in incident.vmstat_deltas if d.field == "vmstat_oom_kill")
    assert oom_delta.delta == 1

    assert len(incident.top_cgroup_memory_deltas) == 1
    cgroup_delta = incident.top_cgroup_memory_deltas[0]
    assert cgroup_delta.label == "nix-daemon.service"
    assert cgroup_delta.delta_bytes == 3_000_000_000

    assert len(incident.kill_events) == 1
    kill = incident.kill_events[0]
    assert kill.killer == "earlyoom"
    assert kill.victim_comm == "cc1plus"


def test_no_spike_below_threshold_reports_no_incidents(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        for minute in range(5):
            _metric(conn, minute)

    analysis = analyze_machine_pressure_incidents(
        start=_ts(0).date(), end=_ts(0).date(), path=db,
        include_workloads=False,
    )

    assert analysis.incident_count == 0
    assert any("no sustained PSI spike" in c for c in analysis.caveats)


def test_single_sample_spike_is_not_sustained(tmp_path):
    """A single high-PSI sample must not produce an incident (sustained floor)."""
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        _metric(conn, 10)
        _metric(conn, 11, memory_psi_some_avg10=90.0)
        _metric(conn, 12)

    analysis = analyze_machine_pressure_incidents(
        start=_ts(0).date(), end=_ts(0).date(), path=db,
        include_workloads=False,
    )

    assert analysis.incident_count == 0


def test_io_spike_is_detected_with_io_focus(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        _metric(conn, 40, io_psi_some_avg10=15.0)
        _metric(conn, 41, io_psi_some_avg10=20.0)

    analysis = analyze_machine_pressure_incidents(
        start=_ts(0).date(), end=_ts(0).date(), path=db,
        include_workloads=False,
    )

    assert analysis.incident_count == 1
    assert analysis.incidents[0].focus == "io"
    assert analysis.incidents[0].peak_io_psi_some_avg10 == 20.0
