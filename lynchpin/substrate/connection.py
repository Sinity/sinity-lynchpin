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
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import shutil
from typing import TYPE_CHECKING, Iterator
from uuid import uuid4

if TYPE_CHECKING:
    import duckdb

SUBSTRATE_VERSION = 41
"""Bump on schema-incompatible changes; triggers drop-and-rebuild on next promote."""


_substrate_path_override: ContextVar[Path | None] = ContextVar(
    "substrate_path_override",
    default=None,
)


@dataclass(frozen=True)
class CandidateGeneration:
    """A complete substrate generation staged before it becomes serving."""

    candidate: Path
    canonical: Path
    refresh_id: str


def substrate_path() -> Path:
    """Return the substrate path for the current promotion context."""
    override = _substrate_path_override.get()
    if override is not None:
        override.parent.mkdir(parents=True, exist_ok=True)
        return override

    from lynchpin.core.config import get_config

    cfg = get_config()
    duck_dir = cfg.local_root / "duck"
    duck_dir.mkdir(parents=True, exist_ok=True)
    return duck_dir / "substrate.duckdb"


def substrate_read_snapshot_path() -> Path:
    """Return the path to the read-only snapshot of the substrate.

    The snapshot is a point-in-time hard copy used by MCP read tools when
    the canonical substrate is held under an exclusive write lock
    (materializations can hold the lock for 30-60+ minutes). The snapshot
    file is updated by ``update_read_snapshot()`` and stays available
    to readers regardless of the canonical's lock state.
    """
    return substrate_path().with_suffix(".read-snapshot.duckdb")


def update_read_snapshot(path: Path | None = None) -> Path | None:
    """Copy a substrate generation to its adjacent read-snapshot location.

    The optional path deliberately controls both the source and destination.
    Candidate promotion therefore creates a candidate snapshot instead of
    overwriting the serving snapshot before the candidate is verified.
    """
    canonical = path if path is not None else substrate_path()
    snapshot = canonical.with_suffix(".read-snapshot.duckdb")
    if not canonical.exists():
        return None
    # Use shutil.copy2 to preserve mtime; DuckDB doesn't keep external
    # state in extended attributes so this is safe. Atomic-rename pattern:
    # write to .tmp first, then rename — readers never see a partial copy.
    tmp = snapshot.with_suffix(".tmp")
    shutil.copy2(canonical, tmp)
    tmp.replace(snapshot)
    return snapshot


def generation_refresh_id(path: Path) -> str | None:
    """Return the latest usable promotion ID, or None for an unready store."""
    import duckdb

    try:
        with duckdb.connect(str(path), read_only=True) as conn:
            row = conn.execute(
                """
                SELECT refresh_id
                FROM substrate_promotion_run
                WHERE status IN ('ok', 'degraded')
                ORDER BY finished_at DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            source_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM substrate_source_status
                WHERE refresh_id = ?
                """,
                [row[0]],
            ).fetchone()[0]
    except duckdb.Error:
        # Missing schema tables and engine/open failures both mean this path is
        # not a verified Lynchpin generation. Minimal test databases and old
        # pre-substrate files must preserve the ordinary snapshot fallback.
        return None
    return str(row[0]) if source_count else None


def _archive_generation(path: Path, label: str) -> Path | None:
    if not path.exists():
        return None
    archived = path.with_name(
        f"{path.name}.{label}-{datetime.now().astimezone():%Y%m%dT%H%M%S%z}-{uuid4().hex}"
    )
    path.replace(archived)
    return archived


def _archive_candidate(candidate: Path, label: str) -> None:
    """Preserve an abandoned candidate and every adjacent DuckDB sidecar."""
    _archive_generation(candidate, label)
    _archive_generation(candidate.with_name(f"{candidate.name}.wal"), label)
    _archive_generation(candidate.with_suffix(".read-snapshot.duckdb"), label)


def _archive_interrupted_candidates(canonical: Path) -> None:
    """Archive candidates left behind when a process exits without unwinding."""
    pattern = f"{canonical.stem}.candidate-*{canonical.suffix}"
    for candidate in canonical.parent.glob(pattern):
        _archive_candidate(candidate, "interrupted")


def _publish_candidate(generation: CandidateGeneration) -> None:
    """Publish a checked candidate while retaining prior generation files."""
    candidate_snapshot = update_read_snapshot(generation.candidate)
    if candidate_snapshot is None:
        raise RuntimeError("candidate substrate disappeared before snapshot publication")

    serving_snapshot = generation.canonical.with_suffix(".read-snapshot.duckdb")
    _archive_generation(generation.canonical, "previous")
    generation.candidate.replace(generation.canonical)
    _archive_generation(serving_snapshot, "previous")
    candidate_snapshot.replace(serving_snapshot)


@contextmanager
def candidate_generation() -> Iterator[CandidateGeneration]:
    """Stage a complete materialization before replacing the serving generation.

    The candidate begins as a copy of canonical so incremental promoters retain
    their existing facts. If canonical is corrupt, recovery acts only on the
    candidate. A previous verified read snapshot remains serving until a new
    candidate has a successful promotion record and source-status coverage.
    """
    canonical = substrate_path()
    _archive_interrupted_candidates(canonical)
    refresh_id = uuid4().hex
    candidate = canonical.with_name(
        f"{canonical.stem}.candidate-{refresh_id}{canonical.suffix}"
    )
    if canonical.exists():
        shutil.copy2(canonical, candidate)
    if generation_refresh_id(canonical) is not None:
        update_read_snapshot(canonical)

    token = _substrate_path_override.set(candidate)
    generation = CandidateGeneration(
        candidate=candidate,
        canonical=canonical,
        refresh_id=refresh_id,
    )
    try:
        yield generation
        if generation_refresh_id(candidate) is None:
            raise RuntimeError("candidate has no verified promoted generation")
        _publish_candidate(generation)
    except Exception:
        _archive_candidate(candidate, "failed")
        raise
    finally:
        _substrate_path_override.reset(token)


def _quarantine_suffix() -> str:
    return datetime.now().astimezone().strftime(".corrupt-%Y%m%dT%H%M%S%z")


def rebuild_corrupt_substrate(
    canonical: Path | None = None,
) -> Path:
    """Atomically replace a broken derived substrate with a clean schema.

    DuckDB internal metadata/checkpoint errors can leave both canonical and
    read-snapshot generations readable while making promotion abort inside the
    engine. Reusing either file is therefore unsafe. Build and checkpoint a
    fresh temporary database first; only then quarantine canonical and WAL and
    atomically install the clean derived store. The old read snapshot remains
    available to readers until the next successful promotion replaces it.

    Also quarantines any stale ``.wal`` sitting next to the corrupt canonical:
    that WAL holds uncheckpointed writes against the *broken* base file, and
    DuckDB replays it by file position on next open. Copying an older,
    unrelated snapshot into place while leaving that WAL behind would replay
    mismatched transactions on top of it — reintroducing corruption instead
    of recovering from it.
    """
    canonical = canonical if canonical is not None else substrate_path()
    rebuilt = canonical.with_suffix(".rebuild.tmp")
    if rebuilt.exists():
        rebuilt.unlink()
    try:
        import duckdb

        with duckdb.connect(str(rebuilt)) as conn:
            apply_schema(conn)
            conn.execute("CHECKPOINT")
    except Exception as rebuild_exc:
        if rebuilt.exists():
            rebuilt.unlink()
        raise RuntimeError(
            "clean substrate rebuild failed before canonical replacement; "
            "canonical database was left in place"
        ) from rebuild_exc

    suffix = _quarantine_suffix()
    quarantine = canonical.with_name(canonical.name + suffix)
    if canonical.exists():
        canonical.replace(quarantine)
    wal = canonical.with_name(canonical.name + ".wal")
    if wal.exists():
        wal.replace(wal.with_name(wal.name + suffix))
    rebuilt.replace(canonical)
    return quarantine


@contextmanager
def connect(
    path: Path | None = None,
    *,
    read_only: bool = False,
    snapshot_fallback: bool = True,
    rebuild_corrupt: bool = False,
) -> Iterator["duckdb.DuckDBPyConnection"]:
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
    # A failed recovery may leave a clean but unpromoted canonical database.
    # Prefer the prior verified snapshot in that state rather than making read
    # clients observe an empty schema as if it were a successful generation.
    if read_only and snapshot_fallback and target == substrate_path():
        snapshot = substrate_read_snapshot_path()
        if (
            generation_refresh_id(target) is None
            and generation_refresh_id(snapshot) is not None
        ):
            target = snapshot
    # DuckDB raises IOException for cross-process write-lock conflicts
    # and ConnectionException for same-process config conflicts (e.g.
    # an existing writer in the same interpreter). Both signal "canonical
    # is unavailable in our preferred mode"; fall back to the snapshot
    # in either case.
    try:
        conn = duckdb.connect(str(target), read_only=read_only)
    except (duckdb.IOException, duckdb.ConnectionException, duckdb.InternalException) as exc:
        if not read_only or not snapshot_fallback:
            if rebuild_corrupt and not read_only and isinstance(exc, duckdb.InternalException):
                rebuild_corrupt_substrate(target)
                conn = duckdb.connect(str(target), read_only=False)
            else:
                raise
        else:
            snapshot = substrate_read_snapshot_path()
            if not snapshot.exists():
                raise
            conn = duckdb.connect(str(snapshot), read_only=True)
    try:
        yield conn
    finally:
        conn.close()


def reset_substrate(path: Path | None = None) -> None:
    """Delete the substrate file. Used by tests and on schema-version bump."""
    target = path if path is not None else substrate_path()
    if target.exists():
        target.unlink()


def apply_schema(conn: "duckdb.DuckDBPyConnection") -> None:
    """Apply the substrate DDL idempotently.

    Reads ``SUBSTRATE_VERSION`` from a ``substrate_meta`` table. If absent
    or stale, drops all tables and re-applies the full DDL. Otherwise no-op.
    """
    from lynchpin.substrate.schema import DDL_STATEMENTS, DROP_STATEMENTS
    from lynchpin.substrate.views import ensure_views

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS substrate_meta (
            key   VARCHAR PRIMARY KEY,
            value VARCHAR NOT NULL
        )
        """
    )
    row = conn.execute(
        "SELECT value FROM substrate_meta WHERE key = 'version'"
    ).fetchone()
    current = int(row[0]) if row else None

    if current != SUBSTRATE_VERSION:
        for stmt in DROP_STATEMENTS:
            conn.execute(stmt)
        for stmt in DDL_STATEMENTS:
            conn.execute(stmt)
        conn.execute(
            "INSERT OR REPLACE INTO substrate_meta VALUES ('version', ?)",
            [str(SUBSTRATE_VERSION)],
        )
    ensure_views(conn)


def prune_commit_history(
    keep_latest_n: int = 1, dry_run: bool = True, path: Path | None = None
) -> dict[str, int]:
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
    read_only = dry_run  # Use read_only for dry_run mode

    with connect(target, read_only=read_only) as conn:
        # Get all distinct refresh_ids from commit_fact, ordered by materialized_at DESC
        refresh_ids_result = conn.execute(
            """
            SELECT DISTINCT refresh_id, MAX(materialized_at) as latest
            FROM commit_fact
            WHERE refresh_id IS NOT NULL
            GROUP BY refresh_id
            ORDER BY latest DESC
            """
        ).fetchall()

        if not refresh_ids_result:
            # No refresh_ids to prune
            return {
                "commit_fact": 0,
                "file_change_fact": 0,
                "symbol_change": 0,
                "refresh_ids_deleted": [],
                "refresh_ids_kept": [],
                "dry_run": dry_run,
                "message": "No refresh_ids found in commit_fact",
            }

        all_refresh_ids = [row[0] for row in refresh_ids_result]
        refresh_ids_to_keep = all_refresh_ids[:keep_latest_n]
        refresh_ids_to_delete = all_refresh_ids[keep_latest_n:]

        if not refresh_ids_to_delete:
            # Nothing to delete
            return {
                "commit_fact": 0,
                "file_change_fact": 0,
                "symbol_change": 0,
                "refresh_ids_deleted": [],
                "refresh_ids_kept": refresh_ids_to_keep,
                "dry_run": dry_run,
                "message": f"No stale refresh_ids (keeping latest {keep_latest_n})",
            }

        counts = {
            "commit_fact": 0,
            "file_change_fact": 0,
            "symbol_change": 0,
        }

        if not dry_run:
            # Perform actual deletion
            for table in ["commit_fact", "file_change_fact", "symbol_change"]:
                # Check if table exists
                exists = conn.execute(
                    f"SELECT COUNT(*) FROM information_schema.tables WHERE table_name = '{table}'"
                ).fetchone()
                if not exists or exists[0] == 0:
                    continue

                # Delete rows for old refresh_ids - count them first, then delete
                placeholders = ", ".join("?" * len(refresh_ids_to_delete))
                count_before = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE refresh_id IN ({placeholders})",
                    refresh_ids_to_delete,
                ).fetchone()[0]

                conn.execute(
                    f"DELETE FROM {table} WHERE refresh_id IN ({placeholders})",
                    refresh_ids_to_delete,
                )
                counts[table] = count_before
        else:
            # Dry run: count rows that WOULD be deleted
            for table in ["commit_fact", "file_change_fact", "symbol_change"]:
                # Check if table exists
                exists = conn.execute(
                    f"SELECT COUNT(*) FROM information_schema.tables WHERE table_name = '{table}'"
                ).fetchone()
                if not exists or exists[0] == 0:
                    continue

                # Count rows for old refresh_ids
                placeholders = ", ".join("?" * len(refresh_ids_to_delete))
                result = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE refresh_id IN ({placeholders})",
                    refresh_ids_to_delete,
                ).fetchone()
                counts[table] = result[0] if result else 0

    return {
        **counts,
        "refresh_ids_deleted": refresh_ids_to_delete,
        "refresh_ids_kept": refresh_ids_to_keep,
        "dry_run": dry_run,
    }
