from __future__ import annotations

from datetime import datetime, timezone

from lynchpin.analysis.machine.below_export_handoff import analyze_below_export_handoff, write_below_export_handoff
from lynchpin.substrate.connection import apply_schema, connect


def _insert_sustained_load_pressure(conn) -> None:
    conn.execute(
        """
        INSERT INTO machine_metric_sample (
            observed_at, host, source, source_schema_version,
            load_1m, gap_codes, refresh_id
        ) VALUES
            (?, 'host', 'machine.telemetry', 2, 30, [], 'r1'),
            (?, 'host', 'machine.telemetry', 2, 31, [], 'r1'),
            (?, 'host', 'machine.telemetry', 2, 32, [], 'r1')
        """,
        [
            datetime(2026, 5, 1, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 1, 10, 2, tzinfo=timezone.utc),
            datetime(2026, 5, 1, 10, 3, tzinfo=timezone.utc),
        ],
    )


def test_below_export_handoff_plans_residual_pressure_windows(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        _insert_sustained_load_pressure(conn)
    live_store = tmp_path / "live-below-store"
    live_store.mkdir()
    epoch = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
    (live_store / f"index_{epoch}").write_text("")

    handoff = analyze_below_export_handoff(path=db, root=tmp_path, live_store=live_store, limit=5)

    assert handoff.planned_window_count == 1
    assert handoff.items[0].episode_kind == "load_pressure"
    assert handoff.root == str(tmp_path)
    assert handoff.live_store == str(live_store)
    assert "dry-run planning only" in handoff.caveats[0]


def test_write_below_export_handoff_serializes_plan(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        _insert_sustained_load_pressure(conn)
    live_store = tmp_path / "live-below-store"
    live_store.mkdir()
    epoch = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
    (live_store / f"index_{epoch}").write_text("")

    out = tmp_path / "machine_below_export_handoff.json"
    handoff = write_below_export_handoff(out, path=db, root=tmp_path, live_store=live_store, limit=1)

    text = out.read_text(encoding="utf-8")
    assert handoff.planned_window_count == 1
    assert "pressure-load_pressure" in text


def test_below_export_handoff_skips_failed_header_only_capture(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        _insert_sustained_load_pressure(conn)
    live_store = tmp_path / "live-below-store"
    live_store.mkdir()
    epoch = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
    (live_store / f"index_{epoch}").write_text("")

    first = analyze_below_export_handoff(path=db, root=tmp_path, live_store=live_store, limit=1)
    report = tmp_path / first.items[0].capture_id / "report"
    report.mkdir(parents=True)
    (report / "below-system.csv").write_text("Datetime,Usage,IOWait,Available,OOM Kills,Running Procs,\n")
    (report / "below-top-processes.csv").write_text("Datetime,Pid,Comm,State,CPU,RSS,Cmdline,\n")
    (report / "below-top-cgroups.csv").write_text("Datetime,Name,Full Path,CPU Usage,Mem Total,CPU Some Pressure,Mem Pressure,RW Total,\n")

    handoff = analyze_below_export_handoff(path=db, root=tmp_path, live_store=live_store, limit=5)

    assert handoff.planned_window_count == 0
    assert handoff.failed_capture_count == 1
    assert handoff.failed_captures[0]["capture_id"] == first.items[0].capture_id
    assert "failed/header-only bounded below exports are skipped" in handoff.caveats[1]
