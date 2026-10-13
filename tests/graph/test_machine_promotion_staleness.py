"""Tests for the sinnix-kx4 machine-promotion freshness readiness gate.

``_machine_promotion_staleness`` (and its caller ``_machine_source``) compare
the DuckDB substrate's machine tables against the live SQLite source and turn
a silent promotion stall (see
.agent/scratch/2026-07-06-machine-promotion-stall-diagnosis.md) into a
visible readiness caveat / status downgrade.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from lynchpin.graph.source_readiness import _machine_promotion_staleness, _machine_source
from lynchpin.sources.machine_models import MachineSourceReadiness


def _seed_live_sqlite(path, tables_and_max_observed_at):
    conn = sqlite3.connect(str(path))
    try:
        for table, max_observed_at in tables_and_max_observed_at.items():
            conn.execute(f"CREATE TABLE {table} (observed_at TEXT)")
            conn.execute(
                f"INSERT INTO {table} (observed_at) VALUES (?)", [max_observed_at]
            )
        conn.commit()
    finally:
        conn.close()


def _seed_substrate(path, *, cgroup_memory_observed_at=None, metric_observed_at=None):
    from lynchpin.substrate.connection import apply_schema, connect

    with connect(path) as conn:
        apply_schema(conn)
        if cgroup_memory_observed_at is not None:
            conn.execute(
                """
                INSERT INTO machine_cgroup_memory_sample (
                    observed_at, host, boot_id, source_schema_version, label,
                    scope, control_group, refresh_id
                ) VALUES (?, 'sinnix-prime', 'boot-a', 5, 'system.slice',
                    'system', '/system.slice', 'r1')
                """,
                [cgroup_memory_observed_at],
            )
        if metric_observed_at is not None:
            conn.execute(
                """
                INSERT INTO machine_metric_sample (
                    observed_at, host, source, source_schema_version, refresh_id
                ) VALUES (?, 'sinnix-prime', 'machine.telemetry', 1, 'r1')
                """,
                [metric_observed_at],
            )


def test_machine_promotion_staleness_flags_lagging_table(tmp_path, monkeypatch):
    live_db = tmp_path / "telemetry.sqlite"
    _seed_live_sqlite(
        live_db,
        {
            "cgroup_memory_sample": "2026-07-06T10:00:00+00:00",
            "metric_sample": "2026-07-06T10:00:00+00:00",
        },
    )
    sub_db = tmp_path / "substrate.duckdb"
    _seed_substrate(
        sub_db,
        cgroup_memory_observed_at=datetime(2026, 6, 26, 6, 0, tzinfo=timezone.utc),
        metric_observed_at=datetime(2026, 7, 6, 9, 55, tzinfo=timezone.utc),
    )

    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: sub_db)
    monkeypatch.setattr(
        "lynchpin.core.config.get_config",
        lambda: SimpleNamespace(machine_telemetry_db=live_db),
    )

    caveats = _machine_promotion_staleness(max_lag_hours=24.0)
    joined = " ".join(caveats)
    assert "machine_cgroup_memory_sample" in joined
    assert "behind live telemetry" in joined
    assert "machine_metric_sample" not in joined


def test_machine_promotion_staleness_empty_when_all_fresh(tmp_path, monkeypatch):
    live_db = tmp_path / "telemetry.sqlite"
    _seed_live_sqlite(live_db, {"metric_sample": "2026-07-06T10:00:00+00:00"})
    sub_db = tmp_path / "substrate.duckdb"
    _seed_substrate(sub_db, metric_observed_at=datetime(2026, 7, 6, 9, 55, tzinfo=timezone.utc))

    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: sub_db)
    monkeypatch.setattr(
        "lynchpin.core.config.get_config",
        lambda: SimpleNamespace(machine_telemetry_db=live_db),
    )

    assert _machine_promotion_staleness(max_lag_hours=24.0) == ()


def test_machine_source_downgrades_to_partial_when_stale(tmp_path, monkeypatch):
    live_db = tmp_path / "telemetry.sqlite"
    _seed_live_sqlite(live_db, {"cgroup_memory_sample": "2026-07-06T10:00:00+00:00"})
    sub_db = tmp_path / "substrate.duckdb"
    _seed_substrate(
        sub_db, cgroup_memory_observed_at=datetime(2026, 6, 26, 6, 0, tzinfo=timezone.utc)
    )

    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: sub_db)
    monkeypatch.setattr(
        "lynchpin.core.config.get_config",
        lambda: SimpleNamespace(machine_telemetry_db=live_db),
    )
    monkeypatch.setattr(
        "lynchpin.graph.source_readiness.get_config",
        lambda: SimpleNamespace(machine_telemetry_db=live_db),
    )
    monkeypatch.setattr(
        "lynchpin.sources.machine.readiness",
        lambda: MachineSourceReadiness(
            status="ready", reason="live samples present", live_db=live_db, live_rows=1
        ),
    )

    result = _machine_source()
    assert result.status == "partial"
    assert any("cgroup_memory_sample" in c for c in result.caveats)


@pytest.mark.parametrize("bad_path", ["missing", "directory", "unconfigured"])
def test_machine_promotion_staleness_reports_unavailable_live_db(tmp_path, monkeypatch, bad_path):
    sub_db = tmp_path / "substrate.duckdb"
    _seed_substrate(sub_db)
    path = {"missing": tmp_path / "missing.sqlite", "directory": tmp_path, "unconfigured": None}[bad_path]
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: sub_db)
    monkeypatch.setattr("lynchpin.core.config.get_config", lambda: SimpleNamespace(machine_telemetry_db=path))
    caveats = _machine_promotion_staleness()
    assert len(caveats) == 1
    assert "freshness check failed" in caveats[0]
    assert "telemetry database" in caveats[0]
    assert not (tmp_path / "missing.sqlite").exists()


@pytest.mark.parametrize("timestamp", ["not-a-timestamp", ""])
def test_machine_promotion_staleness_reports_invalid_timestamp(tmp_path, monkeypatch, timestamp):
    live_db = tmp_path / "telemetry.sqlite"
    _seed_live_sqlite(live_db, {"metric_sample": timestamp})
    sub_db = tmp_path / "substrate.duckdb"
    _seed_substrate(sub_db)
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: sub_db)
    monkeypatch.setattr("lynchpin.core.config.get_config", lambda: SimpleNamespace(machine_telemetry_db=live_db))
    caveats = _machine_promotion_staleness()
    assert len(caveats) == 1
    assert "machine_metric_sample" in caveats[0]
    assert "observed_at is invalid" in caveats[0]


def test_machine_promotion_staleness_empty_live_table_is_not_invalid(tmp_path, monkeypatch):
    live_db = tmp_path / "telemetry.sqlite"
    with sqlite3.connect(live_db) as conn:
        conn.execute("CREATE TABLE metric_sample (observed_at TEXT)")
    sub_db = tmp_path / "substrate.duckdb"
    _seed_substrate(sub_db)
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: sub_db)
    monkeypatch.setattr("lynchpin.core.config.get_config", lambda: SimpleNamespace(machine_telemetry_db=live_db))
    assert _machine_promotion_staleness() == ()


def test_failed_promotion_rows_cannot_mask_serving_generation_lag(tmp_path, monkeypatch):
    from lynchpin.substrate.connection import connect
    live_db = tmp_path / "live.sqlite"
    _seed_live_sqlite(live_db, {"metric_sample": "2026-07-06T10:00:00+00:00"})
    sub_db = tmp_path / "substrate.duckdb"
    _seed_substrate(sub_db, metric_observed_at=datetime(2026, 6, 26, 6, tzinfo=timezone.utc))
    with connect(sub_db) as conn:
        conn.execute("INSERT INTO substrate_promotion_run (refresh_id,status,started_at,finished_at) VALUES ('r1','ok',now(),now()), ('failed','failed',now(),now())")
        conn.execute("INSERT INTO machine_metric_sample (observed_at,host,source,source_schema_version,refresh_id) VALUES (?, 'fixture', 'fixture', 1, 'failed')", [datetime(2026,7,6,10,tzinfo=timezone.utc)])
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: sub_db)
    monkeypatch.setattr("lynchpin.core.config.get_config", lambda: SimpleNamespace(machine_telemetry_db=live_db))
    assert any("behind live telemetry" in item for item in _machine_promotion_staleness())


def test_failed_only_promotion_is_unavailable(tmp_path, monkeypatch):
    from lynchpin.substrate.connection import connect
    live_db = tmp_path / "live.sqlite"
    _seed_live_sqlite(live_db, {"metric_sample": "2026-07-06T10:00:00+00:00"})
    sub_db = tmp_path / "substrate.duckdb"
    _seed_substrate(sub_db, metric_observed_at=datetime(2026,7,6,10,tzinfo=timezone.utc))
    with connect(sub_db) as conn:
        conn.execute("INSERT INTO substrate_promotion_run (refresh_id,status,started_at,finished_at) VALUES ('r1','failed',now(),now())")
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: sub_db)
    monkeypatch.setattr("lynchpin.core.config.get_config", lambda: SimpleNamespace(machine_telemetry_db=live_db))
    assert any("no serving promotion generation" in item for item in _machine_promotion_staleness())


def test_freshness_includes_serving_inherited_partition(tmp_path, monkeypatch):
    from lynchpin.substrate.connection import connect
    from lynchpin.substrate.machine_lineage import record_machine_partition
    from lynchpin.substrate.machine import load_machine_promotion_freshness
    live_db = tmp_path / "live.sqlite"
    _seed_live_sqlite(live_db, {"metric_sample": "2026-07-06T10:00:00+00:00"})
    sub_db = tmp_path / "substrate.duckdb"
    _seed_substrate(sub_db, metric_observed_at=datetime(2026, 7, 6, 10, tzinfo=timezone.utc))
    with connect(sub_db) as conn:
        monkeypatch.setattr("lynchpin.core.config.get_config", lambda: SimpleNamespace(machine_telemetry_db=live_db))
        record_machine_partition(conn, table="machine_metric_sample", refresh_id="r1", window_start=date(2026,7,5), window_end=date(2026,7,6))
        record_machine_partition(conn, table="machine_metric_sample", refresh_id="child", predecessor="r1", replacement_start=date(2026,7,7), window_start=date(2026,7,5), window_end=date(2026,7,7))
        conn.execute("INSERT INTO substrate_promotion_run (refresh_id,status,started_at,finished_at) VALUES ('child','ok',now(),now())")
        reports = load_machine_promotion_freshness(conn)
    assert len(reports) == 1
    assert reports[0]["refresh_id"] == "child"
    assert reports[0]["stale"] is False
    assert reports[0]["lag_hours"] == 0


@pytest.mark.parametrize("fresh_tail", [False, True])
def test_window_comparison_detects_partial_history_with_a_fresh_tail(tmp_path, monkeypatch, fresh_tail):
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.machine import load_machine_promotion_freshness
    live = tmp_path / "live.sqlite"
    with sqlite3.connect(live) as conn:
        conn.execute("CREATE TABLE metric_sample (observed_at TEXT NOT NULL)")
        conn.execute("CREATE INDEX metric_time ON metric_sample(observed_at)")
        conn.execute("WITH RECURSIVE rows(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM rows WHERE n<447000) INSERT INTO metric_sample SELECT strftime('%Y-%m-%dT%H:%M:%S+00:00', '2026-06-30', '+' || (n*86400/447001) || ' seconds') FROM rows")
        conn.execute("INSERT INTO metric_sample VALUES ('2026-07-01T01:00:00+00:00')")
    with connect(tmp_path / "sub.duckdb") as conn:
        apply_schema(conn)
        conn.execute("INSERT INTO machine_metric_sample (observed_at,host,source,source_schema_version,refresh_id) SELECT TIMESTAMPTZ '2026-06-30T00:00:00Z' + CAST(n*16200/14999 AS BIGINT) * INTERVAL '1 second','fixture','fixture',1,'r1' FROM range(15000) AS rows(n)")
        if fresh_tail:
            conn.execute("UPDATE machine_metric_sample SET observed_at=TIMESTAMPTZ '2026-06-30T20:00:00Z' WHERE observed_at=TIMESTAMPTZ '2026-06-30T04:30:00Z'")
        monkeypatch.setattr("lynchpin.core.config.get_config", lambda: SimpleNamespace(machine_telemetry_db=live))
        rows = load_machine_promotion_freshness(conn, window=(date(2026,6,30),date(2026,7,1)))
    assert rows[0]["stale"] is False  # A current tail does not prove historical completeness.
    assert rows[0]["live_window_rows"] == 447000
    assert rows[0]["substrate_window_rows"] == 15000
    assert rows[0]["window_row_count_agrees"] is False


def test_window_comparison_rejects_unindexed_full_source_scan(tmp_path, monkeypatch):
    from lynchpin.substrate.connection import connect
    from lynchpin.substrate.machine import load_machine_promotion_freshness
    live = tmp_path / "live.sqlite"
    _seed_live_sqlite(live, {"metric_sample": "2026-07-06T10:00:00+00:00"})
    sub = tmp_path / "sub.duckdb"
    _seed_substrate(sub, metric_observed_at=datetime(2026,7,6,10,tzinfo=timezone.utc))
    with connect(sub) as conn:
        monkeypatch.setattr("lynchpin.core.config.get_config", lambda: SimpleNamespace(machine_telemetry_db=live))
        rows = load_machine_promotion_freshness(conn, window=(date(2026,7,6),date(2026,7,7)))
    assert rows[0]["live_window_rows"] is None
    assert rows[0]["window_row_count_agrees"] is None
    assert "indexed time-range" in rows[0]["history_error"]


def test_window_comparison_measures_empty_source_as_zero(tmp_path, monkeypatch):
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.machine import load_machine_promotion_freshness
    live = tmp_path / "empty.sqlite"
    with sqlite3.connect(live) as conn:
        conn.execute("CREATE TABLE metric_sample (observed_at TEXT NOT NULL)")
        conn.execute("CREATE INDEX metric_time ON metric_sample(observed_at)")
    with connect(tmp_path / "sub.duckdb") as conn:
        apply_schema(conn)
        monkeypatch.setattr("lynchpin.core.config.get_config", lambda: SimpleNamespace(machine_telemetry_db=live))
        rows = load_machine_promotion_freshness(conn, window=(date(2026,6,30),date(2026,7,1)))
    assert rows[0]["live_window_rows"] == rows[0]["substrate_window_rows"] == 0
    assert rows[0]["window_row_count_agrees"] is True


def test_readiness_exposes_window_disagreement(monkeypatch):
    from contextlib import nullcontext
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *a, **kw: nullcontext(object()))
    def report(conn, *, max_lag_hours, window):
        assert window == (date(2026,6,30),date(2026,7,1))
        return [{"table":"machine_metric_sample", "stale":False, "window_row_count_agrees":False,
                 "live_window_rows":447000,"substrate_window_rows":15000,
                 "comparison_window_start":"2026-06-30","comparison_window_end":"2026-07-01"}]
    monkeypatch.setattr("lynchpin.substrate.machine.load_machine_promotion_freshness", report)
    caveats = _machine_promotion_staleness(window=(date(2026,6,30),date(2026,7,1)))
    assert len(caveats)==1 and "447000 live, 15000 substrate" in caveats[0]


def test_window_budget_exhaustion_is_unavailable_not_zero(tmp_path):
    from lynchpin.substrate.machine import _machine_window_row_counts
    with sqlite3.connect(tmp_path / "fixture.sqlite") as conn:
        conn.execute("CREATE TABLE metric_sample (observed_at TEXT NOT NULL)")
        conn.execute("CREATE INDEX metric_time ON metric_sample(observed_at)")
        result = _machine_window_row_counts(None, conn, duck_table="machine_metric_sample", live_table="metric_sample", refresh_id=None, window=(date(2026,6,30),date(2026,7,1)), deadline=-1)
    assert result["live_window_rows"] is None
    assert result["substrate_window_rows"] is None
    assert result["window_row_count_agrees"] is None
    assert "budget exhausted" in result["history_error"]


def test_machine_readiness_retains_measured_zero_rows(tmp_path, monkeypatch):
    path = tmp_path / "fixture.sqlite"
    monkeypatch.setattr("lynchpin.graph.source_readiness.get_config", lambda: SimpleNamespace(machine_telemetry_db=path))
    monkeypatch.setattr("lynchpin.sources.machine.readiness", lambda: MachineSourceReadiness(status="ready", reason="measured empty", live_db=path, live_rows=0))
    monkeypatch.setattr("lynchpin.graph.source_readiness._machine_promotion_staleness", lambda **kwargs: ())
    assert _machine_source().count == 0
