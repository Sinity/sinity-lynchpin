"""Resolve machine fact partitions without copying retained history."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import hashlib
import json
from typing import Any

MACHINE_KEYS = {
    "machine_metric_sample": ("observed_at", "host", "source", "refresh_id"),
    "machine_gpu_sample": ("observed_at", "host", "source", "refresh_id"),
    "machine_service_state": ("observed_at", "host", "scope", "unit", "refresh_id"),
    "machine_network_sample": ("observed_at", "host", "interface", "refresh_id"),
    "machine_process_io_delta_sample": ("observed_at", "host", "pid", "process_start_time_ticks", "refresh_id"),
    "machine_process_memory_sample": ("observed_at", "host", "pid", "process_start_time_ticks", "refresh_id"),
    "machine_cgroup_memory_sample": ("observed_at", "host", "label", "refresh_id"),
    "machine_kill_event": ("host", "source_row_id", "refresh_id"),
}
MACHINE_TABLES = frozenset(MACHINE_KEYS)


def validate_machine_keys(conn: Any, physical_table: str, refresh_id: str, *, table: str) -> None:
    """Enforce fact keys with spillable SQL, not permanently resident ART indexes."""
    import duckdb

    keys = MACHINE_KEYS[table]
    nulls = " OR ".join(f"{key} IS NULL" for key in keys)
    if conn.execute(f"SELECT 1 FROM {physical_table} WHERE refresh_id = ? AND ({nulls}) LIMIT 1", [refresh_id]).fetchone():
        raise duckdb.ConstraintException(f"NULL machine fact key in {table}")
    if conn.execute(
        f"SELECT 1 FROM {physical_table} WHERE refresh_id = ? GROUP BY {', '.join(keys)} HAVING COUNT(*) > 1 LIMIT 1",
        [refresh_id],
    ).fetchone():
        raise duckdb.ConstraintException(f"duplicate machine fact key in {table}")


def sqlite_location_identity(path: Path) -> str:
    """Bind a partition to its configured SQLite object, not to its byte digest.

    Device numbers are observations: drift safely requires fresh population.
    The source owner supplies the append-only capture contract.
    """
    resolved = path.resolve()
    stat = resolved.stat()
    observation = json.dumps([str(resolved), stat.st_dev, stat.st_ino], separators=(",", ":"))
    return "sqlite-location-v1:" + hashlib.sha256(observation.encode()).hexdigest()


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _midnight(day: date) -> str:
    return "TIMESTAMPTZ " + _literal(day.isoformat() + "T00:00:00+00:00")


def machine_relation(conn: Any, table: str, refresh_id: str | None) -> str:
    """Return a SQL relation serving one logical generation, newest tail first.

    Raw rows keep their authored physical refresh IDs. Typed readers expose the
    requested logical ID and clip inherited rows to its declared UTC window.
    Legacy complete snapshots remain readable without rewriting their payloads.
    """
    if table not in MACHINE_TABLES:
        raise ValueError(f"unknown machine relation {table!r}")
    if refresh_id is None:
        return table
    has_metadata = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = 'main' "
        "AND table_name = 'substrate_product_lineage'",
    ).fetchone()
    if has_metadata is None:
        return table  # Retained native generations predating partition metadata.
    fields = {row[0] for row in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'main' AND table_name = 'substrate_product_lineage'",
    ).fetchall()}
    if not {"window_start", "window_end"}.issubset(fields):
        return table  # Read the last serving generation before its private migration.
    head = conn.execute(
        "SELECT window_start, window_end FROM substrate_product_lineage "
        "WHERE product = ? AND refresh_id = ?", [table, refresh_id],
    ).fetchone()
    if head is None:
        return table
    from lynchpin.substrate.personal import _lineage

    parts = []
    for partition, replaced in _lineage(conn, product=table, refresh_id=refresh_id):
        filters = [f"refresh_id = {_literal(partition)}"]
        if head[0] is not None:
            filters.append(f"observed_at >= {_midnight(head[0])}")
        if head[1] is not None:
            filters.append(f"observed_at < {_midnight(head[1])}")
        for start, end in replaced:
            if end is None:
                filters.append(f"observed_at < {_midnight(start)}")
            else:
                filters.append(f"NOT (observed_at >= {_midnight(start)} AND observed_at < {_midnight(end)})")
        parts.append(f"SELECT * REPLACE ({_literal(refresh_id)} AS refresh_id) FROM {table} WHERE " + " AND ".join(filters))
    return "(" + " UNION ALL ".join(parts) + ")"


def record_machine_partition(
    conn: Any, *, table: str, refresh_id: str, window_start: date, window_end: date,
    predecessor: str | None = None, replacement_start: date | None = None,
    logical_row_count: int | None = None, input_fingerprint: str | None = None,
) -> None:
    if table not in MACHINE_TABLES:
        raise ValueError(f"unknown machine relation {table!r}")
    if predecessor == refresh_id:
        raise ValueError("machine partition cannot inherit itself")
    conn.execute(
        "INSERT OR REPLACE INTO substrate_product_lineage "
        "(product, refresh_id, predecessor_refresh_id, replacement_start, replacement_end, "
        "window_start, window_end, logical_row_count, mode, input_fingerprint) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [table, refresh_id, predecessor, replacement_start,
         window_end + timedelta(days=1) if replacement_start is not None else None,
         window_start, window_end + timedelta(days=1), logical_row_count,
         "incremental" if predecessor is not None else "full", input_fingerprint],
    )


def machine_predecessor(
    conn: Any, *, table: str, source: str, refresh_id: str,
    window_start: date, window_end: date, input_fingerprint: str | None = None,
) -> str | None:
    """Choose a complete earlier source window; never bridge an unscanned prefix."""
    row = conn.execute(
        "SELECT refresh_id, window_start, window_end FROM substrate_source_status "
        "WHERE source = ? AND refresh_id != ? AND kind = 'stage' AND status IN ('ok', 'empty') "
        "AND window_start <= ? AND window_end >= ? ORDER BY recorded_at DESC LIMIT 1",
        [source, refresh_id, window_start, window_start],
    ).fetchone()
    if row is None:
        return None
    previous, start, end = row
    identity = conn.execute(
        "SELECT input_fingerprint FROM substrate_product_lineage WHERE product = ? AND refresh_id = ?",
        [table, previous],
    ).fetchone()
    if identity is None or input_fingerprint is None or identity[0] != input_fingerprint:
        return None  # Unknown or changed source locations cannot establish inheritance.
    return str(previous)
