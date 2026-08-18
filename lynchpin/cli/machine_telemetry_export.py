"""Export machine-telemetry SQLite history to a partitioned Parquet lake.

sinnix-2g54 stage 2/5 (see the design doc's Option C): the collector's live
SQLite grows unbounded (0.5 GB/day) while the same rows compress 34-44x as
Parquet. This exporter is read-only against the source database. It writes
one hive-partitioned dataset per table
(``<lake_root>/<table>/dt=YYYY-MM-DD/part.parquet``) and only ever touches
*sealed* UTC calendar days -- the current, still-growing day is never
exported, so every partition it writes is immutable once written and safe to
re-run idempotently (``--full`` re-exports everything; the default only
exports days not already present in the lake).
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import typer

log = logging.getLogger(__name__)

# (source table, day-partition column). Every real telemetry table in the
# collector's schema except `source_status` (tiny config table, not a time
# series) and `sqlite_sequence` (SQLite-internal). hardware_state's time
# column is `captured_at`, everything else is `observed_at`.
_TABLES: tuple[tuple[str, str], ...] = (
    ("metric_sample", "observed_at"),
    ("service_state", "observed_at"),
    ("gpu_sample", "observed_at"),
    ("network_sample", "observed_at"),
    ("block_device_sample", "observed_at"),
    ("service_cgroup_io_sample", "observed_at"),
    ("service_cgroup_pressure_sample", "observed_at"),
    ("process_io_delta_sample", "observed_at"),
    ("process_memory_sample", "observed_at"),
    ("cgroup_memory_sample", "observed_at"),
    ("kill_event", "observed_at"),
    ("hardware_state", "captured_at"),
)


@dataclass(frozen=True)
class TableExportResult:
    table: str
    days_exported: tuple[str, ...]
    rows_exported: int
    verified_days: int
    bytes_written: int


class LakeVerificationError(RuntimeError):
    """A parquet partition's row count or key checksum disagreed with SQLite."""


def _existing_lake_days(lake_root: Path, table: str) -> set[str]:
    table_dir = lake_root / table
    if not table_dir.is_dir():
        return set()
    days = set()
    for entry in table_dir.iterdir():
        if entry.is_dir() and entry.name.startswith("dt="):
            days.add(entry.name[len("dt="):])
    return days


def verify_day_partition(conn: Any, *, table: str, time_col: str, day: str, part_dir: Path) -> int:
    """Compare a written parquet day-partition against its SQLite source; return row count.

    Raises `LakeVerificationError` if row count or the `sum(id)` natural-key
    checksum disagree -- `id` is AUTOINCREMENT on every collector table and
    every table is insert-only, so the sum is a cheap, deterministic proxy for
    "same set of rows" without hashing every column.
    """
    src_count, src_checksum = conn.execute(
        f"SELECT count(*), sum(id) FROM src.{table} "
        f"WHERE substr({time_col}, 1, 10) = ?",
        [day],
    ).fetchone()
    pq_count, pq_checksum = conn.execute(
        f"SELECT count(*), sum(id) FROM read_parquet('{part_dir}/*.parquet')"
    ).fetchone()
    if pq_count != src_count or pq_checksum != src_checksum:
        raise LakeVerificationError(
            f"{table} dt={day}: sqlite count={src_count} checksum={src_checksum} "
            f"!= parquet count={pq_count} checksum={pq_checksum}"
        )
    return int(pq_count)


def export_table(
    conn: Any,
    *,
    table: str,
    time_col: str,
    lake_root: Path,
    today: date,
    full: bool = False,
) -> TableExportResult:
    """Export sealed days of ``table`` to the parquet lake, verifying each."""
    out_dir = lake_root / table
    out_dir.mkdir(parents=True, exist_ok=True)

    dt_rows = conn.execute(
        f"SELECT DISTINCT substr({time_col}, 1, 10) AS dt FROM src.{table} "
        f"WHERE substr({time_col}, 1, 10) < ?",
        [today.isoformat()],
    ).fetchall()
    sealed_days = sorted(r[0] for r in dt_rows)

    if not full:
        already = _existing_lake_days(lake_root, table)
        target_days = [d for d in sealed_days if d not in already]
    else:
        target_days = sealed_days

    if not target_days:
        return TableExportResult(table=table, days_exported=(), rows_exported=0, verified_days=0, bytes_written=0)

    day_list_sql = ", ".join(f"'{d}'" for d in target_days)
    conn.execute(
        f"""
        COPY (
            SELECT *, substr({time_col}, 1, 10) AS dt
            FROM src.{table}
            WHERE substr({time_col}, 1, 10) IN ({day_list_sql})
        ) TO '{out_dir}' (
            FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (dt),
            FILENAME_PATTERN 'part', OVERWRITE_OR_IGNORE 1
        )
        """
    )

    rows_exported = 0
    bytes_written = 0
    for day in target_days:
        part_dir = out_dir / f"dt={day}"
        pq_count = verify_day_partition(conn, table=table, time_col=time_col, day=day, part_dir=part_dir)
        rows_exported += pq_count
        for f in part_dir.glob("*.parquet"):
            bytes_written += f.stat().st_size

    return TableExportResult(
        table=table,
        days_exported=tuple(target_days),
        rows_exported=rows_exported,
        verified_days=len(target_days),
        bytes_written=bytes_written,
    )


def run_export(
    *,
    sqlite_path: Path,
    lake_root: Path,
    full: bool = False,
    tables: tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> list[TableExportResult]:
    import duckdb

    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    conn = duckdb.connect()
    conn.execute("INSTALL sqlite; LOAD sqlite;")
    conn.execute(f"ATTACH '{sqlite_path}' AS src (TYPE SQLITE, READ_ONLY)")

    results = []
    for table, time_col in _TABLES:
        if tables and table not in tables:
            continue
        log.info("machine-telemetry-export: %s (sealed days before %s)", table, today.isoformat())
        result = export_table(conn, table=table, time_col=time_col, lake_root=lake_root, today=today, full=full)
        results.append(result)
        if result.days_exported:
            log.info(
                "machine-telemetry-export: %s: %d day(s), %s rows, %.2f MB verified",
                table, len(result.days_exported), f"{result.rows_exported:,}", result.bytes_written / 1e6,
            )
    return results


def _export_command(
    sqlite_path: Path = typer.Option(None, "--db", help="Source SQLite (default: configured live database)"),
    lake_root: Path = typer.Option(None, "--lake-root", help="Parquet lake output root (default: configured lake root)"),
    full: bool = typer.Option(False, "--full", help="Re-export every sealed day, not just missing ones"),
    tables: str = typer.Option("", "--tables", help="Comma-separated table subset (default: all)"),
) -> None:
    from lynchpin.core.config import get_config

    cfg = get_config()
    db = sqlite_path or cfg.machine_telemetry_db
    lake = lake_root or cfg.machine_telemetry_lake_root
    table_filter = tuple(t.strip() for t in tables.split(",") if t.strip()) or None

    if not db.exists():
        typer.echo(f"source database not found: {db}", err=True)
        raise typer.Exit(1)

    results = run_export(sqlite_path=db, lake_root=lake, full=full, tables=table_filter)

    total_rows = sum(r.rows_exported for r in results)
    total_bytes = sum(r.bytes_written for r in results)
    total_days = sum(len(r.days_exported) for r in results)
    typer.echo(
        f"exported {total_days} table-day partition(s), {total_rows:,} rows, "
        f"{total_bytes / 1e6:.2f} MB parquet, all verified row-count+checksum equal to source"
    )


_app = typer.Typer(
    help="Export machine-telemetry SQLite history to a partitioned Parquet lake",
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
_app.command()(_export_command)
_command = typer.main.get_command(_app)


def main(argv: list[str] | None = None) -> int:
    import click

    try:
        _command.main(args=list(argv) if argv is not None else None, standalone_mode=False)
    except click.UsageError as exc:
        sys.stderr.write(f"Error: {exc.format_message()}\n")
        return 2
    except (typer.Exit, SystemExit) as exc:
        code = getattr(exc, "exit_code", None)
        if code is None:
            code = getattr(exc, "code", 0)
        return int(code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
