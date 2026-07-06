"""Tests for the sinnix-kx4 machine-promotion freshness readiness gate.

``_machine_promotion_staleness`` (and its caller ``_machine_source``) compare
the DuckDB substrate's machine tables against the live SQLite source and turn
a silent promotion stall (see
.agent/scratch/2026-07-06-machine-promotion-stall-diagnosis.md) into a
visible readiness caveat / status downgrade.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
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


@pytest.mark.parametrize("bad_path", [None])
def test_machine_promotion_staleness_no_live_db_returns_empty(tmp_path, monkeypatch, bad_path):
    sub_db = tmp_path / "substrate.duckdb"
    _seed_substrate(sub_db)
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: sub_db)
    monkeypatch.setattr(
        "lynchpin.core.config.get_config",
        lambda: SimpleNamespace(machine_telemetry_db=tmp_path / "does-not-exist.sqlite"),
    )
    assert _machine_promotion_staleness(max_lag_hours=24.0) == ()
