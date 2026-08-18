from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lynchpin.analysis.machine.explain import (
    build_machine_explain_report,
    machine_explain,
    render_machine_explain_text,
)
from lynchpin.sources.machine_models import (
    MachineKillEvent,
    MachineMetricSample,
    MachineProcessIODeltaSample,
    MachineProcessMemorySample,
)

WINDOW_START = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def _ts(minutes: float) -> datetime:
    return WINDOW_START + timedelta(minutes=minutes)


def _metric(minutes: float, **overrides: Any) -> MachineMetricSample:
    defaults: dict[str, Any] = {
        "observed_at": _ts(minutes),
        "host": "sinnix-prime",
        "boot_id": "boot-a",
        "source": "machine.telemetry",
        "source_schema_version": 5,
        "mem_avail_mb": 14_000,
        "swap_used_mb": 2_000,
        "memory_psi_some_avg10": 1.0,
        "memory_psi_full_avg10": 0.2,
        "io_psi_full_avg10": 0.5,
    }
    defaults.update(overrides)
    return MachineMetricSample(**defaults)


def _process(minutes: float, *, comm: str, unit: str, pss_kb: int, swap_kb: int) -> MachineProcessMemorySample:
    return MachineProcessMemorySample(
        observed_at=_ts(minutes),
        host="sinnix-prime",
        boot_id="boot-a",
        source_schema_version=5,
        pid=4242,
        process_start_time_ticks=99,
        comm=comm,
        exe=None,
        cgroup=None,
        unit=unit,
        scope="user",
        command_line=None,
        rss_kb=pss_kb,
        pss_kb=pss_kb,
        pss_anon_kb=pss_kb,
        pss_file_kb=0,
        pss_shmem_kb=0,
        private_clean_kb=0,
        private_dirty_kb=pss_kb,
        shared_clean_kb=0,
        shared_dirty_kb=0,
        swap_kb=swap_kb,
    )


def _kill(minutes: float, *, comm: str, rss_mib: int, pid: int = 777, row: int = 1) -> MachineKillEvent:
    return MachineKillEvent(
        observed_at=_ts(minutes),
        host="sinnix-prime",
        boot_id="boot-a",
        source_schema_version=5,
        killer="earlyoom",
        victim_comm=comm,
        victim_pid=pid,
        victim_rss_mib=rss_mib,
        cgroup_path=None,
        oom_score=1039,
        raw_line=f"earlyoom: sending SIGKILL to process {pid} {comm}",
        source_row_id=row,
    )


def _io(minutes: float, *, unit: str, total_bytes: int) -> MachineProcessIODeltaSample:
    return MachineProcessIODeltaSample(
        observed_at=_ts(minutes),
        host="sinnix-prime",
        boot_id="boot-a",
        source_schema_version=5,
        interval_s=10.0,
        pid=555,
        process_start_time_ticks=1,
        comm="borg",
        exe=None,
        cgroup=None,
        unit=unit,
        scope="system",
        read_bytes_delta=0,
        write_bytes_delta=total_bytes,
        cancelled_write_bytes_delta=0,
        read_chars_delta=0,
        write_chars_delta=0,
        read_syscalls_delta=0,
        write_syscalls_delta=0,
        total_bytes_delta=total_bytes,
        total_syscalls_delta=1,
    )


def _build(**kwargs: Any) -> Any:
    defaults: dict[str, Any] = {
        "window_start": WINDOW_START,
        "window_end": WINDOW_END,
        "samples": [],
        "kills": [],
        "process_samples": [],
        "io_samples": [],
        "health_ledger": Path("/nonexistent/health-transitions.jsonl"),
    }
    defaults.update(kwargs)
    return build_machine_explain_report(**defaults)


def test_c1_swap_saturation_thrash_classified_with_memory_culprit():
    # C1 signature: swap >= 75% consumed while memory psi_full is degraded;
    # mem_avail stays "healthy" throughout (the gauge that lies during C1).
    samples = [_metric(0), _metric(1)]
    samples += [
        _metric(
            10 + i,
            swap_used_mb=19_000,
            mem_avail_mb=12_000,
            memory_psi_some_avg10=65.0,
            memory_psi_full_avg10=55.0,
        )
        for i in range(6)
    ]
    samples.append(_metric(30))
    process = [_process(12, comm="claude", unit="agent.slice", pss_kb=9 * 1024 * 1024, swap_kb=14 * 1024 * 1024)]

    report = _build(samples=samples, process_samples=process)

    assert len(report.episodes) == 1
    episode = report.episodes[0]
    assert episode.cluster == "C1 swap-saturation thrash"
    assert episode.max_swap_ratio is not None and episode.max_swap_ratio >= 0.75
    assert any("claude" in culprit and "agent.slice" in culprit for culprit in episode.culprits)
    # psi_full 55 for ~5 sustained minutes counts as frozen stall time.
    assert report.aggregates.memory_frozen_hours > 0


def test_c2_big_job_spike_and_kill_verdict_machine_was_fine():
    # C2 signature: mem_avail dives under the floor while memory psi_full
    # stays calm — the bwa-mem2 shape. The kill lands at PSI some 3.0.
    samples = [_metric(0), _metric(1)]
    samples += [
        _metric(
            5 + i * 0.2,
            mem_avail_mb=1_000,
            memory_psi_some_avg10=3.0,
            memory_psi_full_avg10=1.0,
        )
        for i in range(4)
    ]
    samples.append(_metric(10))
    kills = [
        _kill(5.3, comm="bwa-mem2", rss_mib=18_337, row=1),
        # Escalation warning against the same victim seconds later: one kill.
        _kill(5.5, comm="bwa-mem2", rss_mib=18_100, row=2),
    ]

    report = _build(samples=samples, kills=kills)

    assert len(report.episodes) == 1
    assert report.episodes[0].cluster == "C2 big-job spike"
    assert report.episodes[0].kill_count == 1
    assert len(report.kills) == 1
    kill = report.kills[0]
    assert kill.verdict == "machine-was-fine"
    assert kill.memory_psi_some_at_kill == 3.0
    assert report.aggregates.kills_machine_was_fine == 1


def test_kill_during_genuine_stall_is_justified():
    samples = [_metric(5 + i, memory_psi_some_avg10=60.0, memory_psi_full_avg10=45.0, mem_avail_mb=900) for i in range(3)]
    report = _build(samples=samples, kills=[_kill(6, comm="rustc", rss_mib=9_000)])
    assert report.kills[0].verdict == "justified-stall"


def test_c5_io_stall_attributed_to_maintenance_unit():
    samples = [_metric(0)]
    samples += [
        _metric(20 + i, io_psi_full_avg10=55.0, mem_avail_mb=15_000, memory_psi_some_avg10=2.0)
        for i in range(5)
    ]
    io = [_io(21, unit="borgbackup-job-realm.service", total_bytes=40 * 1024**3)]

    report = _build(samples=samples, io_samples=io)

    assert len(report.episodes) == 1
    episode = report.episodes[0]
    assert episode.cluster == "C5 maintenance/backup IO stall"
    assert any("borgbackup-job-realm.service" in culprit for culprit in episode.culprits)
    assert report.aggregates.io_frozen_hours > 0


def test_coverage_gap_is_reported_not_treated_as_calm():
    # Samples only in the first hour and the last hour: a ~22h hole.
    samples = [_metric(i) for i in range(0, 60, 10)] + [_metric(23 * 60 + i) for i in range(0, 60, 10)]
    report = _build(samples=samples)
    assert len(report.coverage_gaps) == 1
    gap = report.coverage_gaps[0]
    assert gap.seconds > 21 * 3600
    text = render_machine_explain_text(report)
    assert "missing coverage, not calm" in text


def test_empty_window_reports_absence_and_full_gap():
    report = _build()
    assert report.aggregates.episode_count == 0
    assert len(report.coverage_gaps) == 1
    assert any("no rows" in caveat for caveat in report.caveats)
    assert not report.health_ledger_available
    assert "signal unavailable" in report.health_ledger_note


def test_health_ledger_transitions_grouped(tmp_path):
    ledger = tmp_path / "health-transitions.jsonl"
    rows = [
        {"schema": "sinnix-health-transition-v1", "ts": _ts(30).isoformat(), "type": "capture_stale",
         "unit": "agent-job-manifests", "status": "stale", "ok": False, "evidence": "age=1"},
        {"schema": "sinnix-health-transition-v1", "ts": _ts(90).isoformat(), "type": "capture_stale",
         "unit": "agent-job-manifests", "status": "stale", "ok": False, "evidence": "age=2"},
        {"schema": "sinnix-health-transition-v1", "ts": _ts(40).isoformat(), "type": "capture_stale",
         "unit": "activitywatch", "status": "healthy", "ok": True, "evidence": "fine"},
        # Outside the window: excluded.
        {"schema": "sinnix-health-transition-v1", "ts": (WINDOW_END + timedelta(hours=1)).isoformat(),
         "type": "unit_failed", "unit": "later.service", "status": "failed", "ok": False, "evidence": "x"},
    ]
    ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    report = _build(samples=[_metric(0)], health_ledger=ledger)

    assert report.health_ledger_available
    assert len(report.health_transitions) == 1
    item = report.health_transitions[0]
    assert item.unit == "agent-job-manifests"
    assert item.bad_count == 2
    assert item.last_bad_at == _ts(90)


def test_week_over_week_uses_prior_window():
    current = [_metric(0), _metric(1)]
    prior = [
        _metric(
            10 + i,
            observed_at=_ts(10 + i) - timedelta(days=7),
            memory_psi_some_avg10=65.0,
            memory_psi_full_avg10=55.0,
            swap_used_mb=19_500,
        )
        for i in range(6)
    ]
    report = _build(samples=current, prior_samples=prior, prior_kills=[])
    assert report.prior_week is not None
    assert report.prior_week.episode_count == 1
    assert "C1 swap-saturation thrash" in report.prior_week.episode_hours_by_cluster
    text = render_machine_explain_text(report)
    assert "WEEK-OVER-WEEK" in text
    assert "C1 swap-saturation thrash" in text


def _seed_live_sqlite(db: Path) -> None:
    """A minimal live telemetry DB satisfying the source schema contract."""
    base = WINDOW_END - timedelta(hours=2)
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE metric_sample (
              observed_at TEXT, host TEXT, boot_id TEXT, schema_version INTEGER,
              cpu_package_w REAL, cpu_core_w REAL, cpu_pkg_c REAL, cpu_max_core_c REAL,
              gpu_power_w REAL, gpu_fan_pct REAL, gpu_temp_c REAL, gpu_util_pct REAL,
              gpu_pstate TEXT, gpu_pcie_gen INTEGER, gpu_pcie_width INTEGER,
              load_1m REAL, mem_avail_mb INTEGER, swap_used_mb INTEGER,
              io_psi_some_avg10 REAL, io_psi_full_avg10 REAL,
              memory_psi_some_avg10 REAL, memory_psi_full_avg10 REAL,
              latency_oversleep_ms REAL, dstate_task_count INTEGER, gap_codes_json TEXT
            );
            CREATE TABLE process_memory_sample (
              observed_at TEXT, host TEXT, boot_id TEXT, schema_version INTEGER,
              pid INTEGER, process_start_time_ticks INTEGER,
              comm TEXT, exe TEXT, cgroup TEXT, unit TEXT, scope TEXT,
              command_line TEXT, rss_kb INTEGER, pss_kb INTEGER,
              pss_anon_kb INTEGER, pss_file_kb INTEGER, pss_shmem_kb INTEGER,
              private_clean_kb INTEGER, private_dirty_kb INTEGER,
              shared_clean_kb INTEGER, shared_dirty_kb INTEGER, swap_kb INTEGER
            );
            CREATE TABLE kill_event (
              id INTEGER PRIMARY KEY, observed_at TEXT, host TEXT, boot_id TEXT,
              schema_version INTEGER, killer TEXT, victim_comm TEXT,
              victim_pid INTEGER, victim_rss_mib INTEGER, cgroup_path TEXT,
              oom_score INTEGER, raw_line TEXT, journal_cursor TEXT
            );
            """
        )
        for i in range(8):
            observed = (base + timedelta(minutes=i)).isoformat()
            swap = 19_000 if 2 <= i <= 6 else 2_000
            psi_some = 65.0 if 2 <= i <= 6 else 1.0
            psi_full = 55.0 if 2 <= i <= 6 else 0.2
            conn.execute(
                "INSERT INTO metric_sample VALUES (?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?, ?,?, ?,?,?)",
                (
                    observed, "sinnix-prime", "boot-a", 5,
                    None, None, None, None, None, None, None, None,
                    None, None, None,
                    1.0, 12_000, swap,
                    0.1, 0.1, psi_some, psi_full,
                    1.0, 0, "[]",
                ),
            )
        conn.execute(
            "INSERT INTO process_memory_sample VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                (base + timedelta(minutes=3)).isoformat(), "sinnix-prime", "boot-a", 5,
                901, 12345, "claude", None, None, "agent.slice", "user", None,
                9_000_000, 9_000_000, 9_000_000, 0, 0, 0, 9_000_000, 0, 0, 14_000_000,
            ),
        )
        conn.execute(
            "INSERT INTO kill_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1, (base + timedelta(minutes=4)).isoformat(), "sinnix-prime", "boot-a", 5,
                "earlyoom", "rg", 902, 8_200, None, 1039,
                "earlyoom: sending SIGKILL to process 902 rg", None,
            ),
        )


def test_machine_explain_reads_live_sqlite_end_to_end(tmp_path):
    # Production route: the real sources.machine SQLite readers (schema
    # validation + column mapping) feed the report builder. Breaking the
    # reader contract, the swap-ratio computation, or the C1 classification
    # thresholds fails this test.
    db = tmp_path / "telemetry.sqlite"
    _seed_live_sqlite(db)

    report = machine_explain(
        window_hours=24.0,
        end=WINDOW_END,
        telemetry_db=db,
        health_ledger=tmp_path / "absent.jsonl",
    )

    assert report.host == "sinnix-prime"
    assert any(e.cluster == "C1 swap-saturation thrash" for e in report.episodes)
    episode = next(e for e in report.episodes if e.cluster.startswith("C1"))
    assert any("claude" in culprit for culprit in episode.culprits)
    assert len(report.kills) == 1
    assert report.kills[0].verdict == "justified-stall"
    # No telemetry one week earlier in this fixture: the delta must say so,
    # not silently render zeros.
    assert report.prior_week is None
    text = render_machine_explain_text(report)
    assert "MACHINE EXPLAIN — sinnix-prime" in text
    assert "C1 swap-saturation thrash" in text
    assert "taxonomy" in text


def test_cli_json_output(tmp_path, capsys):
    db = tmp_path / "telemetry.sqlite"
    _seed_live_sqlite(db)
    from lynchpin.cli import machine_explain as cli

    code = cli.main(
        [
            "--db", str(db),
            "--end", WINDOW_END.isoformat(),
            "--health-ledger", str(tmp_path / "absent.jsonl"),
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["host"] == "sinnix-prime"
    assert payload["calibration"]["swap_total_mb"] == 20479
    assert any(e["cluster"].startswith("C1") for e in payload["episodes"])
