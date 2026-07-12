"""Tests for OperatorDay's machine kill/PSI daily fill (sinnix-kx4)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from lynchpin.analysis import operator_daily as od
from lynchpin.substrate.connection import apply_schema, connect


def _insert(conn: Any, table: str, **cols: Any) -> None:
    names = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO {table} ({names}) VALUES ({placeholders})", list(cols.values()))


def _metric(conn: Any, day: int, hour: int, refresh_id: str, **overrides: Any) -> None:
    cols: dict[str, Any] = {
        "observed_at": datetime(2026, 7, day, hour, tzinfo=timezone.utc),
        "host": "sinnix-prime",
        "source": "machine.telemetry",
        "source_schema_version": 5,
        "gap_codes": [],
        "refresh_id": refresh_id,
    }
    cols.update(overrides)
    _insert(conn, "machine_metric_sample", **cols)


def _kill(conn: Any, day: int, hour: int, source_row_id: int, refresh_id: str) -> None:
    _insert(
        conn, "machine_kill_event",
        observed_at=datetime(2026, 7, day, hour, tzinfo=timezone.utc),
        host="sinnix-prime", boot_id="boot-a", source_schema_version=5,
        killer="earlyoom", victim_comm="cc1plus", victim_pid=999,
        victim_rss_mib=512, oom_score=900, raw_line="kill",
        source_row_id=source_row_id, refresh_id=refresh_id,
    )


def _machine_rows(start: date, end: date) -> dict[date, od.OperatorDay]:
    rows = {
        start + timedelta(days=offset): od.OperatorDay(date=start + timedelta(days=offset))
        for offset in range((end - start).days + 1)
    }
    present = {day: set() for day in rows}
    ctx = od._FillContext(
        rows=rows,
        present=present,
        bounds={},
        start=start,
        end=end,
        source="machine",
    )
    od._fill_machine_pressure(ctx)
    for day, row in rows.items():
        row.sources_present = frozenset(present[day])
    return rows


def test_machine_kill_and_psi_daily_fill(tmp_path, monkeypatch):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        # Day 1: two kills, moderate PSI.
        _metric(conn, 1, 0, "r1", memory_psi_some_avg10=5.0, io_psi_some_avg10=3.0)
        _metric(conn, 1, 1, "r1", memory_psi_some_avg10=12.0, io_psi_some_avg10=4.0)
        _kill(conn, 1, 0, source_row_id=1, refresh_id="r1")
        _kill(conn, 1, 1, source_row_id=2, refresh_id="r1")
        # Day 2: no kills, quiet.
        _metric(conn, 2, 0, "r1", memory_psi_some_avg10=1.0, io_psi_some_avg10=1.0)
        # Day 3: not captured by machine telemetry at all.

    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: db)
    rows = _machine_rows(date(2026, 7, 1), date(2026, 7, 3))

    assert rows[date(2026, 7, 1)].has_source("machine")
    assert rows[date(2026, 7, 1)].machine_kill_events == 2
    assert rows[date(2026, 7, 1)].machine_peak_memory_psi_some_avg10 == 12.0
    assert rows[date(2026, 7, 1)].machine_peak_io_psi_some_avg10 == 4.0

    assert rows[date(2026, 7, 2)].has_source("machine")
    assert rows[date(2026, 7, 2)].machine_kill_events == 0
    assert rows[date(2026, 7, 2)].machine_peak_memory_psi_some_avg10 == 1.0

    # Day never captured by machine telemetry: absent, not a fabricated zero.
    assert not rows[date(2026, 7, 3)].has_source("machine")
    assert rows[date(2026, 7, 3)].measured("machine", rows[date(2026, 7, 3)].machine_kill_events) is None


def test_overlapping_refresh_ids_do_not_double_count_kills(tmp_path, monkeypatch):
    """A stale manual-rebuild refresh_id and the daily rolling refresh_id can
    both hold rows for the same day (see sinnix-kx4 promotion history); the
    daily fill must dedupe via latest_machine_rows, not double-count.
    """
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        _metric(conn, 5, 0, "manual-rebuild:old", memory_psi_some_avg10=8.0)
        _metric(conn, 5, 0, "rolling:today", memory_psi_some_avg10=9.0)
        _kill(conn, 5, 0, source_row_id=42, refresh_id="manual-rebuild:old")
        _kill(conn, 5, 0, source_row_id=42, refresh_id="rolling:today")

    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: db)
    rows = _machine_rows(date(2026, 7, 5), date(2026, 7, 5))

    # Same source_row_id promoted under two refresh_ids for the same live
    # event — latest_machine_rows collapses it to one row.
    assert rows[date(2026, 7, 5)].machine_kill_events == 1
