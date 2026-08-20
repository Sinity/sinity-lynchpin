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

import json
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import typer

log = logging.getLogger(__name__)

# (source table, day-partition column). Every time-series table in the
# collector's schema except `sqlite_sequence` (SQLite-internal). hardware_state's
# time column is `captured_at`, everything else is `observed_at`.
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
_METADATA_TABLES: tuple[str, ...] = ("source_status",)


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
        if (
            entry.is_dir()
            and entry.name.startswith("dt=")
            and any(entry.glob("*.parquet"))
        ):
            days.add(entry.name[len("dt=") :])
    return days


def verify_exported_days(
    conn: Any, *, table: str, time_col: str, target_days: list[str], out_dir: Path
) -> dict[str, int]:
    """Compare every just-written day-partition against its SQLite source; return row counts by day.

    One grouped query against each side (not one query per day): on the two
    tables with no usable `observed_at` index (`service_state`,
    `cgroup_memory_sample`), a per-day SQLite query is a full table scan --
    N of those turns verification into the same O(days) full-scan pathology
    the design doc measured for the un-de-chunked promoter. `id` is
    AUTOINCREMENT on every collector table and every table is insert-only, so
    `sum(id)` is a cheap, deterministic proxy for "same set of rows" without
    hashing every column.

    Raises `LakeVerificationError` on the first day whose row count or
    checksum disagree.
    """
    if not target_days:
        return {}
    day_list_sql = ", ".join(f"'{d}'" for d in target_days)
    src_rows = {
        r[0]: (r[1], r[2])
        for r in conn.execute(
            f"SELECT substr({time_col}, 1, 10) AS dt, count(*), sum(id) FROM src.{table} "
            f"WHERE substr({time_col}, 1, 10) IN ({day_list_sql}) GROUP BY dt"
        ).fetchall()
    }
    globs = [str(out_dir / f"dt={day}" / "*.parquet") for day in target_days]
    pq_rows = {
        r[0]: (r[1], r[2])
        for r in conn.execute(
            "SELECT CAST(dt AS VARCHAR), count(*), sum(id) FROM read_parquet(?, hive_partitioning = true) GROUP BY dt",
            [globs],
        ).fetchall()
    }
    counts: dict[str, int] = {}
    for day in target_days:
        src_count, src_checksum = src_rows.get(day, (0, None))
        pq_count, pq_checksum = pq_rows.get(day, (0, None))
        if pq_count != src_count or pq_checksum != src_checksum:
            raise LakeVerificationError(
                f"{table} dt={day}: sqlite count={src_count} checksum={src_checksum} "
                f"!= parquet count={pq_count} checksum={pq_checksum}"
            )
        counts[day] = int(pq_count)
    return counts


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

    already = _existing_lake_days(lake_root, table)
    if already:
        # An existing partition is not evidence of a complete export: a
        # process death can leave an empty or truncated directory behind.
        # Verify it against the live source before treating the sealed day as
        # immutable and skipping it. A mismatch is a hard stop rather than a
        # silent overwrite, so the operator can inspect the lake and source.
        verify_exported_days(
            conn,
            table=table,
            time_col=time_col,
            target_days=sorted(already & set(sealed_days)),
            out_dir=out_dir,
        )

    if not full:
        target_days = [d for d in sealed_days if d not in already]
    else:
        target_days = sealed_days

    if not target_days:
        return TableExportResult(
            table=table,
            days_exported=(),
            rows_exported=0,
            verified_days=0,
            bytes_written=0,
        )

    day_list_sql = ", ".join(f"'{d}'" for d in target_days)
    staging_root = Path(tempfile.mkdtemp(prefix=".staging-", dir=out_dir))
    try:
        conn.execute(
            f"""
            COPY (
                SELECT *, substr({time_col}, 1, 10) AS dt
                FROM src.{table}
                WHERE substr({time_col}, 1, 10) IN ({day_list_sql})
            ) TO '{staging_root}' (
                FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (dt),
                FILENAME_PATTERN 'part', OVERWRITE_OR_IGNORE 1
            )
            """
        )

        # A partition becomes visible only after its complete staged output
        # has passed the source count+checksum check. Directory rename is the
        # publication boundary; an interrupted COPY leaves only a hidden
        # staging directory that the next run can safely ignore.
        verified_counts = verify_exported_days(
            conn,
            table=table,
            time_col=time_col,
            target_days=target_days,
            out_dir=staging_root,
        )
        for day in target_days:
            staged_partition = staging_root / f"dt={day}"
            final_partition = out_dir / f"dt={day}"
            if final_partition.exists():
                if not full:
                    raise LakeVerificationError(
                        f"{table} dt={day}: partition appeared during export; refusing overwrite"
                    )
                staged_files = list(staged_partition.glob("*.parquet"))
                final_files = list(final_partition.glob("*.parquet"))
                if len(staged_files) != 1 or len(final_files) != 1:
                    raise LakeVerificationError(
                        f"{table} dt={day}: expected one parquet file for atomic replacement"
                    )
                # Full re-export replaces the single immutable file in place;
                # os.replace keeps readers from observing a partial file.
                os.replace(staged_files[0], final_files[0])
                staged_partition.rmdir()
            else:
                staged_partition.rename(final_partition)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

    rows_exported = sum(verified_counts.values())
    bytes_written = 0
    for day in target_days:
        for f in (out_dir / f"dt={day}").glob("*.parquet"):
            bytes_written += f.stat().st_size

    return TableExportResult(
        table=table,
        days_exported=tuple(target_days),
        rows_exported=rows_exported,
        verified_days=len(target_days),
        bytes_written=bytes_written,
    )


def _export_metadata_table(conn: Any, *, table: str, lake_root: Path) -> int:
    """Export and verify a non-time-series collector table atomically."""
    if table != "source_status":
        raise ValueError(f"unsupported metadata table: {table}")
    out_dir = lake_root / table
    out_dir.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=".staging-", dir=out_dir))
    staged_file = staging_root / "metadata.parquet"
    final_file = out_dir / "metadata.parquet"
    try:
        conn.execute(
            f"COPY (SELECT * FROM src.{table}) TO '{staged_file}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        source_count, source_checksum = conn.execute(
            "SELECT count(*), sum(hash(source, checked_at, status, "
            "coalesce(reason, ''), payload_json)) FROM src.source_status"
        ).fetchone()
        parquet_count, parquet_checksum = conn.execute(
            "SELECT count(*), sum(hash(source, checked_at, status, "
            "coalesce(reason, ''), payload_json)) FROM read_parquet(?)",
            [str(staged_file)],
        ).fetchone()
        if (parquet_count, parquet_checksum) != (source_count, source_checksum):
            raise LakeVerificationError(
                f"{table}: sqlite count={source_count} checksum={source_checksum} "
                f"!= parquet count={parquet_count} checksum={parquet_checksum}"
            )
        os.replace(staged_file, final_file)
        return int(parquet_count)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def _write_coverage_manifest(
    *,
    lake_root: Path,
    sqlite_path: Path,
    generated_at: datetime,
    metadata_rows: dict[str, int],
) -> None:
    """Publish an atomic receipt of every collector table's lake coverage."""
    tables = {
        table: {
            "time_column": time_col,
            "sealed_days": sorted(_existing_lake_days(lake_root, table)),
        }
        for table, time_col in _TABLES
    }
    for table in _METADATA_TABLES:
        tables[table] = {
            "time_column": None,
            "sealed_days": [],
            "metadata_path": f"{table}/metadata.parquet",
            "row_count": metadata_rows[table],
        }
    manifest = {
        "schema": "sinnix-machine-telemetry-lake-v1",
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "source_database": str(sqlite_path),
        "tables": tables,
    }
    manifest_path = lake_root / "manifest.json"
    fd, tmp_name = tempfile.mkstemp(prefix=".manifest-", dir=lake_root)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")
        os.replace(tmp_path, manifest_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def run_export(
    *,
    sqlite_path: Path,
    lake_root: Path,
    full: bool = False,
    tables: tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> list[TableExportResult]:
    import duckdb

    export_time = now or datetime.now(timezone.utc)
    today = export_time.astimezone(timezone.utc).date()
    lake_root.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect()
    conn.execute("INSTALL sqlite; LOAD sqlite;")
    conn.execute(f"ATTACH '{sqlite_path}' AS src (TYPE SQLITE, READ_ONLY)")

    results = []
    for table, time_col in _TABLES:
        if tables and table not in tables:
            continue
        log.info(
            "machine-telemetry-export: %s (sealed days before %s)",
            table,
            today.isoformat(),
        )
        result = export_table(
            conn,
            table=table,
            time_col=time_col,
            lake_root=lake_root,
            today=today,
            full=full,
        )
        results.append(result)
        if result.days_exported:
            log.info(
                "machine-telemetry-export: %s: %d day(s), %s rows, %.2f MB verified",
                table,
                len(result.days_exported),
                f"{result.rows_exported:,}",
                result.bytes_written / 1e6,
            )
    metadata_rows = {
        table: _export_metadata_table(conn, table=table, lake_root=lake_root)
        for table in _METADATA_TABLES
    }
    _write_coverage_manifest(
        lake_root=lake_root,
        sqlite_path=sqlite_path,
        generated_at=export_time,
        metadata_rows=metadata_rows,
    )
    return results


def _export_command(
    sqlite_path: Path = typer.Option(
        None, "--db", help="Source SQLite (default: configured live database)"
    ),
    lake_root: Path = typer.Option(
        None,
        "--lake-root",
        help="Parquet lake output root (default: configured lake root)",
    ),
    full: bool = typer.Option(
        False, "--full", help="Re-export every sealed day, not just missing ones"
    ),
    tables: str = typer.Option(
        "", "--tables", help="Comma-separated table subset (default: all)"
    ),
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
        _command.main(
            args=list(argv) if argv is not None else None, standalone_mode=False
        )
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
