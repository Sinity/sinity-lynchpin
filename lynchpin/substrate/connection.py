"""DuckDB substrate path management and lazy connection.

The substrate file lives at ``.lynchpin/duck/substrate.duckdb`` (under
``LynchpinConfig.generated_root`` so it sits alongside the JSON artifact dir,
not in the cachew cache).

Concurrency: single writer (materialization DAG); many readers via DuckDB's MVCC.
We never run concurrent writers today; the assumption is documented here so
future MCP-server work knows the constraint.

Schema versioning: we track ``SUBSTRATE_VERSION``. When it changes, the
``apply_schema`` step drops + recreates rather than migrating — the substrate
is *derived* from sources, not authoritative. Re-promote is cheap.
"""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterator
if TYPE_CHECKING:
    import duckdb
SUBSTRATE_VERSION = 33
'Bump on schema-incompatible changes; triggers drop-and-rebuild on next promote.'

def substrate_path() -> Path:
    """Return the canonical DuckDB substrate file path."""
    from lynchpin.core.config import get_config
    cfg = get_config()
    duck_dir = cfg.local_root / 'duck'
    duck_dir.mkdir(parents=True, exist_ok=True)
    return duck_dir / 'substrate.duckdb'

def substrate_read_snapshot_path() -> Path:
    return substrate_path().with_suffix('.read-snapshot.duckdb')

def update_read_snapshot(path: Path | None=None) -> Path | None:
    """Copy the current canonical substrate to its read-snapshot location.

    Idempotent: overwrites any prior snapshot. Returns the snapshot path
    on success, None if the canonical is currently write-locked AND there
    is no readable copy to clone (extremely rare; caller can retry later).

    Designed to be called at the END of a successful substrate_promote
    (when the write lock is released and the freshest data is committed)
    and OPTIONALLY at the START to capture the prior generation before
    invalidation. Either approach gives MCP readers a frozen view they
    can rely on during the next write window.
    """
    import shutil
    canonical = path if path is not None else substrate_path()
    snapshot = substrate_read_snapshot_path()
    if not canonical.exists():
        return None
    tmp = snapshot.with_suffix('.tmp')
    shutil.copy2(canonical, tmp)
    tmp.replace(snapshot)
    return snapshot

@contextmanager
def connect(path: Path | None=None, *, read_only: bool=False, snapshot_fallback: bool=True) -> Iterator['duckdb.DuckDBPyConnection']:
    """Yield a DuckDB connection to the substrate.

    When ``read_only=True`` and the canonical substrate is held under an
    exclusive write lock (materialization DAG in flight), this falls back to the
    read-snapshot copy if one exists. Returns a slightly-stale but live
    connection instead of erroring. Set ``snapshot_fallback=False`` to preserve
    the strict lock-error behavior for callers that need to distinguish current
    canonical availability from snapshot availability.

    Caller responsibility: do not run concurrent writers. Reads against
    the canonical are MVCC-safe; reads against the snapshot are
    point-in-time and may trail the canonical by one promote cycle.
    """
    import duckdb
    target = path if path is not None else substrate_path()
    try:
        conn = duckdb.connect(str(target), read_only=read_only)
    except (duckdb.IOException, duckdb.ConnectionException):
        if not read_only or not snapshot_fallback:
            raise
        snapshot = substrate_read_snapshot_path()
        if not snapshot.exists():
            raise
        conn = duckdb.connect(str(snapshot), read_only=True)
    try:
        yield conn
    finally:
        conn.close()

def reset_substrate(path: Path | None=None) -> None:
    """Delete the substrate file. Used by tests and on schema-version bump."""
    target = path if path is not None else substrate_path()
    if target.exists():
        target.unlink()

def apply_schema(conn: 'duckdb.DuckDBPyConnection') -> None:
    """Apply the substrate DDL idempotently.

    Reads ``SUBSTRATE_VERSION`` from a ``substrate_meta`` table. If absent
    or stale, drops all tables and re-applies the full DDL. Otherwise no-op.
    """
    from lynchpin.substrate.schema import DDL_STATEMENTS, DROP_STATEMENTS
    from lynchpin.substrate.views import ensure_views
    conn.execute('\n        CREATE TABLE IF NOT EXISTS substrate_meta (\n            key   VARCHAR PRIMARY KEY,\n            value VARCHAR NOT NULL\n        )\n        ')
    row = conn.execute("SELECT value FROM substrate_meta WHERE key = 'version'").fetchone()
    current = int(row[0]) if row else None
    if current != SUBSTRATE_VERSION:
        for stmt in DROP_STATEMENTS:
            conn.execute(stmt)
        for stmt in DDL_STATEMENTS:
            conn.execute(stmt)
        conn.execute("INSERT OR REPLACE INTO substrate_meta VALUES ('version', ?)", [str(SUBSTRATE_VERSION)])
    ensure_views(conn)

def prune_commit_history(keep_latest_n: int=1, dry_run: bool=True, path: Path | None=None) -> dict[str, int]:
    """Remove stale refresh_ids from commit_fact and related tables.

    Identifies refresh_ids in commit_fact ordered by materialized_at desc,
    keeps the latest N (default 1), and deletes rows for older ones from:
    - commit_fact
    - file_change_fact (if exists)
    - symbol_change (if exists)

    Parameters:
        keep_latest_n: Number of most recent refresh_ids to preserve (default 1).
        dry_run: If True (default), return counts without deleting. If False, perform deletion.
        path: Optional substrate path. If None, uses substrate_path().

    Returns:
        Dictionary with deleted row counts per table:
        {
            "commit_fact": int,
            "file_change_fact": int,
            "symbol_change": int,
            "refresh_ids_deleted": [str, ...],
            "refresh_ids_kept": [str, ...],
        }
    """
    target = path if path is not None else substrate_path()
    read_only = dry_run
    with connect(target, read_only=read_only) as conn:
        refresh_ids_result = conn.execute('\n            SELECT DISTINCT refresh_id, MAX(materialized_at) as latest\n            FROM commit_fact\n            WHERE refresh_id IS NOT NULL\n            GROUP BY refresh_id\n            ORDER BY latest DESC\n            ').fetchall()
        if not refresh_ids_result:
            return {'commit_fact': 0, 'file_change_fact': 0, 'symbol_change': 0, 'refresh_ids_deleted': [], 'refresh_ids_kept': [], 'dry_run': dry_run, 'message': 'No refresh_ids found in commit_fact'}
        all_refresh_ids = [row[0] for row in refresh_ids_result]
        refresh_ids_to_keep = all_refresh_ids[:keep_latest_n]
        refresh_ids_to_delete = all_refresh_ids[keep_latest_n:]
        if not refresh_ids_to_delete:
            return {'commit_fact': 0, 'file_change_fact': 0, 'symbol_change': 0, 'refresh_ids_deleted': [], 'refresh_ids_kept': refresh_ids_to_keep, 'dry_run': dry_run, 'message': f'No stale refresh_ids (keeping latest {keep_latest_n})'}
        counts = {'commit_fact': 0, 'file_change_fact': 0, 'symbol_change': 0}
        if not dry_run:
            for table in ['commit_fact', 'file_change_fact', 'symbol_change']:
                exists = conn.execute(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_name = '{table}'").fetchone()
                if not exists or exists[0] == 0:
                    continue
                placeholders = ', '.join('?' * len(refresh_ids_to_delete))
                count_before = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE refresh_id IN ({placeholders})', refresh_ids_to_delete).fetchone()[0]
                conn.execute(f'DELETE FROM {table} WHERE refresh_id IN ({placeholders})', refresh_ids_to_delete)
                counts[table] = count_before
        else:
            for table in ['commit_fact', 'file_change_fact', 'symbol_change']:
                exists = conn.execute(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_name = '{table}'").fetchone()
                if not exists or exists[0] == 0:
                    continue
                placeholders = ', '.join('?' * len(refresh_ids_to_delete))
                result = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE refresh_id IN ({placeholders})', refresh_ids_to_delete).fetchone()
                counts[table] = result[0] if result else 0
    return {**counts, 'refresh_ids_deleted': refresh_ids_to_delete, 'refresh_ids_kept': refresh_ids_to_keep, 'dry_run': dry_run}
