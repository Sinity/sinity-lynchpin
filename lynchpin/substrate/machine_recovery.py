"""Reuse completed fact partitions from a retained, unpublished candidate."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import re
from typing import Any

from lynchpin.core.substrate_sources import source_for_substrate_table
from lynchpin.substrate.machine_lineage import MACHINE_TABLES, record_machine_partition, sqlite_location_identity


def recover_machine_partitions(
    conn: Any, *, path: Path, canonical: Path, sqlite_path: Path,
    window_start: date, window_end: date, sources: Any,
) -> frozenset[str]:
    """Copy only completed full partitions with matching source/window evidence.

    A rejected candidate never becomes the write predecessor or the serving
    generation. Its unrelated, failed and incomplete relations are not reused.
    The new candidate starts from the last verified serving store as usual.
    """
    path = path.resolve(strict=True)
    if path.parent != canonical.parent.resolve() or not re.fullmatch(
        re.escape(canonical.stem) + r"\.candidate-[0-9a-f]{32}\.duckdb", path.name,
    ):
        raise ValueError("machine recovery requires an owner-retained candidate in the serving directory")
    identity = sqlite_location_identity(sqlite_path)
    escaped = str(path).replace("'", "''")
    conn.execute(f"ATTACH '{escaped}' AS machine_recovery (READ_ONLY)")
    recovered = set()
    try:
        for table in sorted(MACHINE_TABLES):
            source = source_for_substrate_table(table)
            if not sources.includes(source):
                continue
            rows = conn.execute(
                "SELECT readiness.refresh_id, readiness.row_count FROM machine_recovery.substrate_source_status readiness "
                "JOIN machine_recovery.substrate_product_lineage lineage ON lineage.product = ? AND lineage.refresh_id = readiness.refresh_id "
                "WHERE readiness.source = ? AND readiness.kind = 'stage' AND readiness.status IN ('ok','empty') "
                "AND readiness.window_start = ? AND readiness.window_end = ? "
                "AND lineage.mode = 'full' AND lineage.predecessor_refresh_id IS NULL "
                "AND lineage.input_fingerprint = ? ORDER BY readiness.recorded_at DESC LIMIT 1",
                [table, source, window_start, window_end, identity],
            ).fetchone()
            if rows is None:
                continue
            previous, expected_count = rows
            completed = conn.execute(
                "SELECT 1 FROM machine_recovery.substrate_run_step WHERE refresh_id = ? "
                "AND step = ? AND status = 'success' LIMIT 1", [previous, "promote_" + table],
            ).fetchone()
            if completed is None:
                continue
            count = conn.execute(f"SELECT COUNT(*) FROM machine_recovery.{table} WHERE refresh_id = ?", [previous]).fetchone()[0]
            if count != expected_count:
                raise ValueError(f"recovery count mismatch for {table}: {count} != {expected_count}")
            current_columns = [row[0] for row in conn.execute(f"DESCRIBE {table}").fetchall()]
            retained_columns = [row[0] for row in conn.execute(f"DESCRIBE machine_recovery.{table}").fetchall()]
            if current_columns != retained_columns:
                raise ValueError(f"recovery schema mismatch for {table}")
            archive = f"machine-recovery:{path.stem}:{previous}"
            existing = conn.execute(
                "SELECT input_fingerprint, logical_row_count, mode FROM substrate_product_lineage WHERE product = ? AND refresh_id = ?",
                [table, archive],
            ).fetchone()
            already_copied = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE refresh_id = ?", [archive]).fetchone()[0]
            if existing is not None or already_copied:
                if existing != (identity, count, "full") or already_copied != count:
                    raise ValueError(f"existing recovery partition conflicts for {table}")
                recovered.add(table)
                continue
            columns = ", ".join(current_columns)
            expressions = ", ".join("? AS refresh_id" if name == "refresh_id" else name for name in current_columns)
            conn.execute(f"INSERT INTO {table} ({columns}) SELECT {expressions} FROM machine_recovery.{table} WHERE refresh_id = ?", [archive, previous])
            record_machine_partition(conn, table=table, refresh_id=archive, window_start=window_start,
                                     window_end=window_end, logical_row_count=count, input_fingerprint=identity)
            from lynchpin.analysis.active.substrate_promote_status import record_source_status

            record_source_status(conn, refresh_id=archive, source=source, status="ok" if count else "empty",
                                 reason=f"reused completed partition from {path.name}", row_count=count,
                                 window_start=window_start, window_end=window_end)
            recovered.add(table)
    finally:
        conn.execute("DETACH machine_recovery")
    if not recovered:
        raise ValueError("retained candidate has no matching completed machine partitions")
    return frozenset(recovered)
