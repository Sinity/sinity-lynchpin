from __future__ import annotations

from datetime import datetime, timezone

from lynchpin.analysis.machine.attribution import (
    analyze_below_attribution,
    export_below_windows_for_pressure_episodes,
    plan_below_windows_for_pressure_episodes,
)
from lynchpin.analysis.machine.below import BelowWindowExport
from lynchpin.substrate.connection import apply_schema, connect


def test_below_attribution_joins_episode_to_bounded_capture(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, source_schema_version,
                load_1m, gap_codes, refresh_id
            ) VALUES
                (?, 'host', 'machine.telemetry', 2, 30, [], 'r1'),
                (?, 'host', 'machine.telemetry', 2, 31, [], 'r1')
            """,
            [
                datetime(2026, 5, 1, 10, 1, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 10, 2, tzinfo=timezone.utc),
            ],
        )

    report = tmp_path / "20260501-120000-auto" / "report"
    report.mkdir(parents=True)
    (report / "below-system.csv").write_text(
        "\n".join(
            [
                "Datetime,Usage,IOWait,Available,OOM Kills,Running Procs,",
                "2026-05-01 12:00:00,10.00%,1.00%,30.0 GB,0,2,",
                "2026-05-01 12:03:00,20.00%,2.00%,29.0 GB,0,3,",
            ]
        )
        + "\n"
    )
    (report / "below-top-processes.csv").write_text(
        "\n".join(
            [
                "Datetime,Pid,Comm,State,CPU,RSS,Cmdline,",
                "2026-05-01 12:01:00,10,pytest,RUNNING,40.00%,200 MB,pytest -q,",
            ]
        )
        + "\n"
    )
    (report / "below-top-cgroups.csv").write_text(
        "\n".join(
            [
                "Datetime,Name,Full Path,CPU Usage,Mem Total,CPU Some Pressure,Mem Pressure,RW Total,",
                "2026-05-01 12:01:00,user.slice,/user.slice,25.00%,1.0 GB,0.0%,0.0%,1 MB/s,",
            ]
        )
        + "\n"
    )

    analysis = analyze_below_attribution(path=db, root=tmp_path, live_store=tmp_path / "missing-store")

    assert analysis.episode_count == 1
    assert analysis.attributed_episode_count == 1
    assert analysis.unattributed_pressure_episode_count == 0
    row = analysis.attributions[0]
    assert row.episode_kind == "load_pressure"
    assert row.capture_id == "20260501-120000-auto"
    assert row.overlap_seconds == 60.0
    assert row.top_processes[0].key == "pytest -q"
    assert row.top_cgroups[0].key == "/user.slice"
    assert "observational" in row.caveats[0]


def test_below_attribution_reports_unattributed_pressure(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, source_schema_version,
                load_1m, gap_codes, refresh_id
            ) VALUES
                (?, 'host', 'machine.telemetry', 2, 30, [], 'r1'),
                (?, 'host', 'machine.telemetry', 2, 31, [], 'r1')
            """,
            [
                datetime(2026, 5, 1, 10, 1, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 10, 2, tzinfo=timezone.utc),
            ],
        )

    live_store = tmp_path / "live-below-store"
    live_store.mkdir()
    epoch = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
    (live_store / f"index_{epoch}").write_text("")

    analysis = analyze_below_attribution(path=db, root=tmp_path, live_store=live_store)

    assert analysis.pressure_episode_count == 1
    assert analysis.attributed_episode_count == 0
    assert analysis.unattributed_pressure_episode_count == 1
    assert analysis.live_store_index_count == 1
    assert analysis.live_store_first_observed_at == datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert analysis.live_store_last_observed_at == datetime(2026, 5, 2, tzinfo=timezone.utc)
    assert any("pressure episodes have no overlapping bounded below capture" in caveat for caveat in analysis.caveats)
    assert any("live below store overlaps pressure episodes" in caveat for caveat in analysis.caveats)


def test_below_attribution_filters_contributors_to_episode_overlap(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, source_schema_version,
                load_1m, gap_codes, refresh_id
            ) VALUES
                (?, 'host', 'machine.telemetry', 2, 30, [], 'r1'),
                (?, 'host', 'machine.telemetry', 2, 31, [], 'r1')
            """,
            [
                datetime(2026, 5, 1, 10, 1, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 10, 2, tzinfo=timezone.utc),
            ],
        )

    report = tmp_path / "20260501-120000-auto" / "report"
    report.mkdir(parents=True)
    (report / "below-system.csv").write_text(
        "\n".join(
            [
                "Datetime,Usage,IOWait,Available,OOM Kills,Running Procs,",
                "2026-05-01 12:00:00,10.00%,1.00%,30.0 GB,0,2,",
                "2026-05-01 12:03:00,20.00%,2.00%,29.0 GB,0,3,",
            ]
        )
        + "\n"
    )
    (report / "below-top-processes.csv").write_text(
        "\n".join(
            [
                "Datetime,Pid,Comm,State,CPU,RSS,Cmdline,",
                "2026-05-01 12:30:00,10,pytest,RUNNING,90.00%,200 MB,pytest -q,",
            ]
        )
        + "\n"
    )
    (report / "below-top-cgroups.csv").write_text(
        "\n".join(
            [
                "Datetime,Name,Full Path,CPU Usage,Mem Total,CPU Some Pressure,Mem Pressure,RW Total,",
                "2026-05-01 12:01:00,user.slice,/user.slice,25.00%,1.0 GB,0.0%,0.0%,1 MB/s,",
            ]
        )
        + "\n"
    )

    analysis = analyze_below_attribution(path=db, root=tmp_path, live_store=tmp_path / "missing-store")
    row = analysis.attributions[0]

    assert row.top_processes == ()
    assert row.top_cgroups[0].key == "/user.slice"
    assert "pressure episode overlaps below system capture but has no process/cgroup contributor rows" not in row.caveats


def test_below_attribution_uses_workload_resource_windows_without_below_capture(tmp_path):
    db = tmp_path / "sub.duckdb"
    started = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, source_schema_version,
                load_1m, gap_codes, refresh_id
            ) VALUES
                (?, 'host', 'machine.telemetry', 2, 30, [], 'r1'),
                (?, 'host', 'machine.telemetry', 2, 31, [], 'r1')
            """,
            [
                datetime(2026, 5, 1, 10, 1, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 10, 2, tzinfo=timezone.utc),
            ],
        )
        conn.execute(
            """
            INSERT INTO work_observation (
                source, source_id, work_kind, project, command, started_at,
                ended_at, duration_s, status, host, live_stage,
                process_cpu_usage_avg, process_memory_usage_max_mb,
                host_io_pressure_full_avg10_max, process_count_max,
                resource_sample_count, refresh_id
            ) VALUES (
                'xtask_history', 'xtask:1', 'xtask_invocation', 'sinex',
                ['check'], ?, ?, 180.0, 'success', 'host', 'test',
                42.5, 512.0, 8.0, 12, 30, 'r1'
            )
            """,
            [started, datetime(2026, 5, 1, 10, 3, tzinfo=timezone.utc)],
        )

    analysis = analyze_below_attribution(path=db, root=tmp_path, live_store=tmp_path / "missing-store")

    assert analysis.attributed_episode_count == 0
    assert analysis.workload_resource_attributed_pressure_episode_count == 1
    assert analysis.residual_unattributed_pressure_episode_count == 0
    row = analysis.workload_resource_attributions[0]
    assert row.work_source_id == "xtask:1"
    assert row.project == "sinex"
    assert row.live_stage == "test"
    assert row.command == ("check",)
    assert row.process_cpu_usage_avg == 42.5
    assert row.process_memory_usage_max_mb == 512.0
    assert row.host_io_pressure_full_avg10_max == 8.0
    assert "process_cpu_usage_avg" in row.attribution_basis
    assert "host_io_pressure_full_avg10_max" in row.attribution_basis


def test_plan_below_windows_targets_unattributed_pressure_in_live_store(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, source_schema_version,
                load_1m, gap_codes, refresh_id
            ) VALUES
                (?, 'host', 'machine.telemetry', 2, 30, [], 'r1'),
                (?, 'host', 'machine.telemetry', 2, 31, [], 'r1')
            """,
            [
                datetime(2026, 5, 1, 10, 1, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 10, 2, tzinfo=timezone.utc),
            ],
        )
    live_store = tmp_path / "live-below-store"
    live_store.mkdir()
    epoch = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
    (live_store / f"index_{epoch}").write_text("")

    plans = plan_below_windows_for_pressure_episodes(
        path=db,
        root=tmp_path,
        live_store=live_store,
        limit=5,
        padding_seconds=60,
        min_duration_seconds=120,
    )

    assert len(plans) == 1
    assert plans[0].episode_kind == "load_pressure"
    assert plans[0].capture_id.startswith("pressure-load_pressure-")
    assert plans[0].reason == "pressure episode lacks bounded below attribution"


def test_plan_below_windows_deduplicates_overlapping_pressure_windows(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, source_schema_version,
                load_1m, dstate_task_count, gap_codes, refresh_id
            ) VALUES
                (?, 'host', 'machine.telemetry', 2, 30, 2, [], 'r1'),
                (?, 'host', 'machine.telemetry', 2, 31, 2, [], 'r1')
            """,
            [
                datetime(2026, 5, 1, 10, 1, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 10, 2, tzinfo=timezone.utc),
            ],
        )
    live_store = tmp_path / "live-below-store"
    live_store.mkdir()
    epoch = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
    (live_store / f"index_{epoch}").write_text("")

    plans = plan_below_windows_for_pressure_episodes(
        path=db,
        root=tmp_path,
        live_store=live_store,
        limit=5,
        padding_seconds=60,
        min_duration_seconds=120,
    )

    assert len(plans) == 1


def test_export_below_windows_for_pressure_episodes_can_write_planned_windows(monkeypatch, tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, source_schema_version,
                load_1m, gap_codes, refresh_id
            ) VALUES
                (?, 'host', 'machine.telemetry', 2, 30, [], 'r1'),
                (?, 'host', 'machine.telemetry', 2, 31, [], 'r1')
            """,
            [
                datetime(2026, 5, 1, 10, 1, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 10, 2, tzinfo=timezone.utc),
            ],
        )
    live_store = tmp_path / "live-below-store"
    live_store.mkdir()
    epoch = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
    (live_store / f"index_{epoch}").write_text("")
    calls = []

    def fake_export(**kwargs):
        calls.append(kwargs)
        return BelowWindowExport(
            capture_id=kwargs["capture_id"],
            report_path=str(tmp_path / kwargs["capture_id"] / "report"),
            system_rows=1,
            process_rows=2,
            cgroup_rows=3,
            errors=(),
        )

    monkeypatch.setattr("lynchpin.analysis.machine.attribution.export_live_below_window", fake_export)

    exports = export_below_windows_for_pressure_episodes(
        path=db,
        root=tmp_path,
        live_store=live_store,
        limit=1,
        dry_run=False,
    )

    assert len(exports) == 1
    assert exports[0].export is not None
    assert exports[0].export.system_rows == 1
    assert calls[0]["duration"] is None
    assert calls[0]["capture_id"].startswith("pressure-load_pressure-")
