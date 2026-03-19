from __future__ import annotations

import argparse
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

import duckdb
import pandas as pd

from ...core.config import get_config
from .core import SourceSpec, TableSpec, WarehouseContext
from .specs import SOURCE_SPECS


def _warehouse_root(root: Optional[Path]) -> Path:
    cfg = get_config()
    return Path(root) if root else cfg.warehouse_root


def _source_specs(selected: Optional[Sequence[str]]) -> List[SourceSpec]:
    if not selected:
        return SOURCE_SPECS
    available = {spec.name.lower(): spec for spec in SOURCE_SPECS}
    requested = [name.strip().lower() for name in selected if name.strip()]
    unknown = sorted({name for name in requested if name not in available})
    if unknown:
        available_text = ", ".join(spec.name for spec in SOURCE_SPECS)
        raise ValueError(
            f"Unknown warehouse source(s): {', '.join(unknown)}. Available: {available_text}"
        )
    return [available[name] for name in requested]


def _duckdb_source_path(root: Path, source: str) -> Path:
    return root / "duckdb" / f"{source}.duckdb"


def _parquet_source_dir(root: Path, source: str) -> Path:
    return root / "parquet" / source


def _source_alias(source: str) -> str:
    return f"{source}_src"


def attach_sources(
    conn: duckdb.DuckDBPyConnection,
    *,
    root: Optional[Path] = None,
    sources: Optional[Sequence[str]] = None,
) -> List[str]:
    root_path = _warehouse_root(root)
    specs = _source_specs(sources)
    existing = {
        row[0] for row in conn.execute("SELECT database_name FROM duckdb_databases()").fetchall()
    }
    attached: List[str] = []
    for spec in specs:
        source_path = _duckdb_source_path(root_path, spec.name)
        if not source_path.exists():
            continue
        alias = _source_alias(spec.name)
        if alias in existing:
            conn.execute(f"DETACH {alias}")
        conn.execute(f"ATTACH '{source_path.as_posix()}' AS {alias} (READ_ONLY)")
        attached.append(spec.name)
        existing.add(alias)
    return attached


def _run_step(label: str, fn: Callable[[], None]) -> None:
    start = time.monotonic()
    print(f"[warehouse] {label}...", flush=True)
    fn()
    elapsed = time.monotonic() - start
    print(f"[warehouse] {label} done in {elapsed:.1f}s", flush=True)


def _drop_managed_relations(
    conn: duckdb.DuckDBPyConnection,
    specs: Sequence[SourceSpec],
) -> None:
    for spec in specs:
        for table in spec.tables:
            # DuckDB raises CatalogException if the object exists but is the
            # wrong type (e.g. DROP TABLE on a VIEW), so try both and suppress.
            for kind in ("TABLE", "VIEW"):
                try:
                    conn.execute(f"DROP {kind} IF EXISTS {table.name}")
                except duckdb.CatalogException:
                    pass


def _insert_table(conn: duckdb.DuckDBPyConnection, spec: TableSpec, ctx: WarehouseContext) -> None:
    conn.execute(spec.create_sql)
    conn.execute(f"DELETE FROM {spec.name}")
    rows = spec.rows(ctx)
    _batched_insert(conn, spec.insert_sql, rows)


_CREATE_TABLE_COL_RE = re.compile(r"\(\s*((?:[^,()]+(?:,\s*)?)+)\)", re.DOTALL)
_COL_NAME_RE = re.compile(r"^\s*(\w+)\s+\w")


def _extract_col_names(create_sql: str) -> list[str]:
    """Parse column names from a CREATE TABLE statement."""
    m = _CREATE_TABLE_COL_RE.search(create_sql)
    if not m:
        return []
    return [
        cm.group(1)
        for col_def in m.group(1).split(",")
        if (cm := _COL_NAME_RE.match(col_def))
    ]


def _write_table_parquet(
    conn: duckdb.DuckDBPyConnection,
    spec: TableSpec,
    ctx: WarehouseContext,
    parquet_dir: Path,
) -> None:
    rows = list(spec.rows(ctx))
    parquet_dir.mkdir(parents=True, exist_ok=True)
    path = parquet_dir / f"{spec.name}.parquet"
    col_names = _extract_col_names(spec.create_sql)
    df = pd.DataFrame(rows, columns=col_names if col_names else None)
    conn.register("_parquet_df", df)
    conn.execute(
        f"COPY (SELECT * FROM _parquet_df) TO '{path.as_posix()}' (FORMAT 'parquet')"
    )
    conn.unregister("_parquet_df")


def materialize_sources(
    *,
    sources: Optional[Sequence[str]] = None,
    root: Optional[Path] = None,
    output_format: str = "parquet",
    limit: Optional[int] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Path:
    root_path = _warehouse_root(root)
    ctx = WarehouseContext(
        limit=limit,
        since=since,
        until=until,
        start_date=start_date,
        end_date=end_date,
    )
    specs = _source_specs(sources)

    def _materialize_source(spec: SourceSpec) -> None:
        if output_format == "duckdb":
            target = _duckdb_source_path(root_path, spec.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            conn = duckdb.connect(str(target))
            try:
                for table in spec.tables:
                    _insert_table(conn, table, ctx)
            finally:
                conn.close()
        elif output_format == "parquet":
            conn = duckdb.connect(":memory:")
            try:
                parquet_dir = _parquet_source_dir(root_path, spec.name)
                for table in spec.tables:
                    _write_table_parquet(conn, table, ctx, parquet_dir)
            finally:
                conn.close()
        else:
            raise ValueError(f"Unsupported output format: {output_format}")

    for spec in specs:
        _run_step(f"materialize {spec.name}", lambda spec=spec: _materialize_source(spec))

    return root_path


def build_views(
    *,
    output: Optional[Path] = None,
    root: Optional[Path] = None,
    output_format: str = "parquet",
    sources: Optional[Sequence[str]] = None,
) -> Path:
    cfg = get_config()
    db_path = Path(output or cfg.warehouse_db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    root_path = _warehouse_root(root)
    specs = _source_specs(sources)

    conn = duckdb.connect(str(db_path))
    try:
        missing: List[str] = []
        manifest_rows: List[Tuple[str, str, str, int, int, datetime]] = []
        now = datetime.now(timezone.utc)
        _drop_managed_relations(conn, specs)
        if output_format == "duckdb":
            attached = set(attach_sources(conn, root=root_path, sources=sources))
            if attached:
                print(
                    "[warehouse] note: duckdb views require re-attaching per-source DBs "
                    "on each new connection; use --format parquet for portable views."
                )
            for spec in specs:
                source_path = _duckdb_source_path(root_path, spec.name)
                expected_tables = len(spec.tables)
                if spec.name not in attached:
                    missing.append(spec.name)
                    manifest_rows.append(
                        (
                            spec.name,
                            output_format,
                            str(source_path),
                            0,
                            expected_tables,
                            now,
                        )
                    )
                    continue
                alias = _source_alias(spec.name)
                present_tables = 0
                for table in spec.tables:
                    try:
                        conn.execute(
                            f"CREATE OR REPLACE VIEW {table.name} AS SELECT * FROM {alias}.{table.name}"
                        )
                        present_tables += 1
                    except duckdb.CatalogException:
                        missing.append(f"{spec.name}.{table.name}")
                manifest_rows.append(
                    (
                        spec.name,
                        output_format,
                        str(source_path),
                        present_tables,
                        expected_tables,
                        now,
                    )
                )
        elif output_format == "parquet":
            for spec in specs:
                parquet_dir = _parquet_source_dir(root_path, spec.name)
                expected_tables = len(spec.tables)
                present_tables = 0
                if not parquet_dir.exists():
                    missing.append(spec.name)
                    manifest_rows.append(
                        (
                            spec.name,
                            output_format,
                            str(parquet_dir),
                            present_tables,
                            expected_tables,
                            now,
                        )
                    )
                    continue
                for table in spec.tables:
                    parquet_path = parquet_dir / f"{table.name}.parquet"
                    if not parquet_path.exists():
                        missing.append(f"{spec.name}.{table.name}")
                        continue
                    conn.execute(
                        "CREATE OR REPLACE VIEW "
                        f"{table.name} AS SELECT * FROM read_parquet('{parquet_path.as_posix()}')"
                    )
                    present_tables += 1
                manifest_rows.append(
                    (
                        spec.name,
                        output_format,
                        str(parquet_dir),
                        present_tables,
                        expected_tables,
                        now,
                    )
                )
        else:
            raise ValueError(f"Unsupported output format: {output_format}")

        conn.execute(
            "CREATE TABLE IF NOT EXISTS warehouse_manifest ("
            "source TEXT, format TEXT, source_path TEXT, present_tables BIGINT, "
            "expected_tables BIGINT, updated_at TIMESTAMP)"
        )
        if sources is None:
            conn.execute("DELETE FROM warehouse_manifest")
        else:
            conn.executemany(
                "DELETE FROM warehouse_manifest WHERE source = ?",
                [(spec.name,) for spec in specs],
            )
        if manifest_rows:
            conn.executemany(
                "INSERT INTO warehouse_manifest VALUES (?, ?, ?, ?, ?, ?)",
                manifest_rows,
            )

        if missing:
            missing_text = ", ".join(sorted(set(missing)))
            print(f"[warehouse] missing sources: {missing_text}")
    finally:
        conn.close()

    return db_path


def refresh(
    *,
    output: Optional[Path] = None,
    root: Optional[Path] = None,
    output_format: str = "parquet",
    sources: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Path:
    _run_step(
        "materialize sources",
        lambda: materialize_sources(
            sources=sources,
            root=root,
            output_format=output_format,
            limit=limit,
            since=since,
            until=until,
            start_date=start_date,
            end_date=end_date,
        ),
    )
    db_path = build_views(output=output, root=root, output_format=output_format, sources=sources)
    return db_path


def _parse_datetime_arg(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid datetime: {value}")


def _add_common_args(parser: argparse.ArgumentParser, *, include_output: bool) -> None:
    parser.add_argument(
        "--format",
        choices=["duckdb", "parquet"],
        default="parquet",
        help="Output format for per-source materialization (parquet recommended for portable views).",
    )
    if include_output:
        parser.add_argument("--output", type=Path, help="Warehouse DuckDB path for views.")
    parser.add_argument("--root", type=Path, help="Root directory for per-source outputs.")
    parser.add_argument(
        "--sources",
        type=str,
        default=None,
        help="Comma-separated source list (default: all).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit rows per table.")
    parser.add_argument("--since", type=_parse_datetime_arg, help="ISO timestamp lower bound.")
    parser.add_argument("--until", type=_parse_datetime_arg, help="ISO timestamp upper bound.")
    parser.add_argument("--start-date", type=str, help="YYYY-MM-DD lower bound for date-only sources.")
    parser.add_argument("--end-date", type=str, help="YYYY-MM-DD upper bound for date-only sources.")


def cli() -> None:
    parser = argparse.ArgumentParser(description="Build/query the Lynchpin warehouse view database.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build the view-only DuckDB warehouse.")
    _add_common_args(build_parser, include_output=True)

    materialize_parser = subparsers.add_parser(
        "materialize",
        help="Materialize per-source warehouse tables without building the view DB.",
    )
    _add_common_args(materialize_parser, include_output=False)

    refresh_parser = subparsers.add_parser(
        "refresh",
        help="Materialize per-source tables and rebuild the view DB.",
    )
    _add_common_args(refresh_parser, include_output=True)

    args = parser.parse_args()
    sources = args.sources.split(",") if args.sources else None

    if args.command == "materialize":
        root_path = materialize_sources(
            sources=sources,
            root=args.root,
            output_format=args.format,
            limit=args.limit,
            since=args.since,
            until=args.until,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        print(f"Materialized sources under {root_path}")
        return

    if args.command == "refresh":
        db_path = refresh(
            output=args.output,
            root=args.root,
            output_format=args.format,
            sources=sources,
            limit=args.limit,
            since=args.since,
            until=args.until,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        print(f"Wrote {db_path}")
        return

    db_path = build_views(output=args.output, root=args.root, output_format=args.format, sources=sources)
    print(f"Wrote {db_path}")


def _batched_insert(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    rows: Iterable[Tuple],
    batch_size: int = 20000,
) -> None:
    batch: List[Tuple] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            conn.executemany(sql, batch)
            batch.clear()
    if batch:
        conn.executemany(sql, batch)


if __name__ == "__main__":
    cli()
