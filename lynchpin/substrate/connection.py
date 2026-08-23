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
import errno
import fcntl
import logging
from pathlib import Path
import signal
import threading
from typing import TYPE_CHECKING, Iterator, Literal
from uuid import uuid4

if TYPE_CHECKING:
    import duckdb

SUBSTRATE_VERSION = 41
"""Bump on schema-incompatible changes; triggers drop-and-rebuild on next promote."""

log = logging.getLogger(__name__)
_CANCELLATION_SIGNALS = (signal.SIGINT, signal.SIGTERM)


class CandidateGenerationInterrupted(KeyboardInterrupt):
    """A termination signal received while a candidate generation was active."""

    def __init__(self, signal_number: int) -> None:
        self.signal_number = signal_number
        super().__init__(f"candidate generation interrupted by {signal.Signals(signal_number).name}")


class CandidateGenerationRejected(RuntimeError):
    """Abort an unsafe candidate while retaining the verified serving generation."""


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
    seed_source: Path
    seed_mode: Literal["reflink", "logical-index-rebuild"]


def in_candidate_generation() -> bool:
    """Return whether this context writes an unpublishable candidate generation."""
    return _substrate_path_override.get() is not None


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
    return Path(substrate_path()).with_suffix(".read-snapshot.duckdb")


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
    # Same-filesystem reflinks preserve an immutable verified generation without
    # rereading or rewriting its historical pages. Atomic rename means readers
    # see either the previous complete snapshot or this complete clone.
    tmp = snapshot.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    _reflink_clone(canonical, tmp)
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


def _candidate_artifacts(candidate: Path) -> tuple[Path, ...]:
    """Return every known database, snapshot, and manifest artifact for a candidate."""
    snapshot = candidate.with_suffix(".read-snapshot.duckdb")
    manifest = candidate.with_suffix(".manifest.json")
    return (
        candidate,
        candidate.with_name(f"{candidate.name}.wal"),
        snapshot,
        snapshot.with_suffix(".tmp"),
        manifest,
        manifest.with_name(f".{manifest.name}.tmp"),
    )


def _archive_candidate(candidate: Path, label: str) -> tuple[Path, ...]:
    """Preserve an abandoned candidate and every adjacent publication sidecar."""
    archived = tuple(
        artifact
        for path in _candidate_artifacts(candidate)
        if (artifact := _archive_generation(path, label)) is not None
    )
    if archived:
        log.warning(
            "archived %s candidate generation artifacts; canonical generation was not modified: %s",
            label,
            ", ".join(str(path) for path in archived),
        )
    return archived


def _archive_interrupted_candidates(canonical: Path) -> None:
    """Archive candidates left behind when a process exits without unwinding."""
    pattern = f"{canonical.stem}.candidate-*{canonical.suffix}"
    for candidate in canonical.parent.glob(pattern):
        archived = _archive_candidate(candidate, "interrupted")
        if archived:
            log.warning(
                "recovered interrupted candidate generation while retaining canonical generation: %s",
                canonical,
            )


def _raise_candidate_interruption(signal_number: int, _frame: object) -> None:
    raise CandidateGenerationInterrupted(signal_number)


@contextmanager
def _candidate_interruption_handlers() -> Iterator[None]:
    """Turn SIGINT/SIGTERM into unwindable candidate-context exceptions."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    previous_handlers = {
        signal_number: signal.getsignal(signal_number)
        for signal_number in _CANCELLATION_SIGNALS
    }
    try:
        for signal_number in _CANCELLATION_SIGNALS:
            signal.signal(signal_number, _raise_candidate_interruption)
        yield
    finally:
        for signal_number, previous_handler in previous_handlers.items():
            signal.signal(signal_number, previous_handler)


@contextmanager
def _defer_candidate_interruptions() -> Iterator[list[int]]:
    """Defer cancellation until the serving database/snapshot/manifest move completes."""
    if threading.current_thread() is not threading.main_thread():
        deferred: list[int] = []
        yield deferred
        return

    deferred = []

    def defer(signal_number: int, _frame: object) -> None:
        deferred.append(signal_number)

    previous_handlers = {
        signal_number: signal.getsignal(signal_number)
        for signal_number in _CANCELLATION_SIGNALS
    }
    try:
        for signal_number in _CANCELLATION_SIGNALS:
            signal.signal(signal_number, defer)
        yield deferred
    finally:
        for signal_number, previous_handler in previous_handlers.items():
            signal.signal(signal_number, previous_handler)


def _publish_candidate(generation: CandidateGeneration) -> None:
    """Publish a checked candidate with matching database and manifest sidecars."""
    from lynchpin.substrate.status_manifest import (
        substrate_status_manifest_path,
        write_substrate_status_manifest,
    )

    candidate_snapshot = update_read_snapshot(generation.candidate)
    if candidate_snapshot is None:
        raise RuntimeError("candidate substrate disappeared before snapshot publication")

    candidate_manifest = substrate_status_manifest_path(generation.candidate)
    if write_substrate_status_manifest(
        generation.candidate,
        output_path=candidate_manifest,
        published_path=generation.canonical,
    ) is None:
        raise RuntimeError("candidate substrate disappeared before manifest publication")

    serving_snapshot = generation.canonical.with_suffix(".read-snapshot.duckdb")
    serving_manifest = substrate_status_manifest_path(generation.canonical)
    # Move sidecars first. If either move fails, the current canonical database
    # remains serving; the candidate is archived by the surrounding context.
    _archive_generation(serving_snapshot, "previous")
    candidate_snapshot.replace(serving_snapshot)
    _archive_generation(serving_manifest, "previous")
    candidate_manifest.replace(serving_manifest)
    # The canonical database move is last. Nothing fallible follows it, so a
    # failed sidecar publication cannot leave an unverified candidate serving.
    _archive_generation(generation.canonical, "previous")
    generation.candidate.replace(generation.canonical)


def _quote_identifier(identifier: str) -> str:
    """Quote an identifier obtained from DuckDB catalog metadata."""
    return '"' + identifier.replace('"', '""') + '"'


# linux/fs.h FICLONE: clone source extents into an empty destination file.
# The managed derived root is Btrfs; refusing a non-CoW filesystem is safer
# than silently returning to whole-generation reads and writes.
_FICLONE = 0x40049409


def _reflink_clone(source: Path, destination: Path) -> None:
    """Create a same-filesystem CoW clone without a full logical reseed."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as src, destination.open("xb") as dst:
            fcntl.ioctl(dst.fileno(), _FICLONE, src.fileno())
    except OSError as exc:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        if exc.errno in {errno.EOPNOTSUPP, errno.ENOTTY, errno.EXDEV, errno.EINVAL}:
            raise CandidateGenerationRejected(
                "steady-state candidate generation requires a same-filesystem "
                "copy-on-write clone; use --rebuild-candidate-indexes for the "
                "explicit logical recovery path"
            ) from exc
        raise


def _base_table_names(conn: "duckdb.DuckDBPyConnection", catalog: str | None = None) -> set[str]:
    """Return main-schema base tables for the current or attached catalog."""
    if catalog is None:
        catalog = str(conn.execute("SELECT current_database()").fetchone()[0])
    rows = conn.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_catalog = ? AND table_schema = 'main' AND table_type = 'BASE TABLE'
        """,
        [catalog],
    ).fetchall()
    return {str(row[0]) for row in rows}


def _logical_index_rebuild_seed(source: Path, candidate: Path, refresh_id: str) -> None:
    """Copy verified logical rows into fresh schema and index structures.

    This deliberately does not copy DuckDB pages, checkpoints, or WAL state.
    It uses a readable verified generation, which can be an archive when the
    serving file's physical ART indexes can no longer accept a promotion write.
    """
    import duckdb

    if generation_refresh_id(source) is None:
        raise CandidateGenerationRejected(
            "logical index rebuild requires a readable verified generation"
        )

    with duckdb.connect(str(candidate)) as conn:
        apply_schema(conn)
        source_sql = str(source).replace("'", "''")
        conn.execute(f"ATTACH '{source_sql}' AS source (READ_ONLY)")
        source_version = conn.execute(
            "SELECT value FROM \"source\".\"main\".\"substrate_meta\" "
            "WHERE key = 'version'"
        ).fetchone()
        if source_version != (str(SUBSTRATE_VERSION),):
            raise CandidateGenerationRejected(
                "logical index rebuild requires source schema version "
                f"{SUBSTRATE_VERSION}, found {source_version[0] if source_version else 'none'}"
            )

        source_tables = _base_table_names(conn, "source")
        candidate_tables = _base_table_names(conn)
        if source_tables != candidate_tables:
            missing = ", ".join(sorted(candidate_tables - source_tables))
            unexpected = ", ".join(sorted(source_tables - candidate_tables))
            details = "; ".join(
                item
                for item in (
                    f"missing source tables: {missing}" if missing else None,
                    f"unexpected source tables: {unexpected}" if unexpected else None,
                )
                if item is not None
            )
            raise CandidateGenerationRejected(
                "logical index rebuild requires matching source and candidate "
                f"schema tables ({details})"
            )

        copied_rows = 0
        for table in sorted(source_tables):
            if table == "substrate_meta":
                continue
            quoted = _quote_identifier(table)
            source_relation = f'"source"."main".{quoted}'
            source_count = conn.execute(
                f"SELECT count(*) FROM {source_relation}"
            ).fetchone()[0]
            conn.execute(
                f"INSERT INTO {quoted} BY NAME SELECT * FROM {source_relation}"
            )
            candidate_count = conn.execute(
                f"SELECT count(*) FROM {quoted}"
            ).fetchone()[0]
            if candidate_count != source_count:
                raise CandidateGenerationRejected(
                    "logical index rebuild copied an incomplete table "
                    f"({table}: {candidate_count} != {source_count})"
                )
            copied_rows += int(source_count)

        from lynchpin.substrate.run_steps import record_run_step

        record_run_step(
            conn,
            refresh_id=refresh_id,
            step="candidate_index_rebuild",
            status="ok",
            message=f"rebuilt candidate indexes from verified logical rows: {source.name}",
            row_count=copied_rows,
        )
        conn.execute("CHECKPOINT")


def _latest_verified_generation(canonical: Path) -> Path:
    """Find the newest readable verified generation without trusting file names.

    A prior candidate or corruption quarantine can be the only readable source
    after DuckDB rejects the current serving file. Promotion metadata and source
    coverage, rather than an archive suffix or modification time, establish
    whether a path is eligible to seed a new candidate.
    """
    import duckdb

    candidates = [canonical]
    candidates.extend(
        path
        for path in canonical.parent.glob(f"{canonical.stem}*.duckdb*")
        if path.is_file()
        and path != canonical
        and ".read-snapshot.duckdb" not in path.name
        and ".wal" not in path.name
    )
    verified: list[tuple[datetime, Path]] = []
    for path in candidates:
        refresh_id = generation_refresh_id(path)
        if refresh_id is None:
            continue
        try:
            with duckdb.connect(str(path), read_only=True) as conn:
                row = conn.execute(
                    """
                    SELECT finished_at
                    FROM substrate_promotion_run
                    WHERE refresh_id = ? AND status IN ('ok', 'degraded')
                    ORDER BY finished_at DESC
                    LIMIT 1
                    """,
                    [refresh_id],
                ).fetchone()
        except duckdb.Error:
            continue
        if row is not None and isinstance(row[0], datetime):
            verified.append((row[0], path))
    if not verified:
        raise CandidateGenerationRejected(
            "candidate generation requires a readable verified substrate source"
        )
    return max(verified, key=lambda item: (item[0], item[1].name))[1]


def _record_candidate_phase(
    candidate: Path,
    *,
    refresh_id: str,
    measurement: object,
    count: int | None,
) -> None:
    """Append phase evidence while the candidate is the only writable generation."""
    import duckdb

    from lynchpin.substrate.run_steps import PhaseMeasurement, record_phase_evidence

    assert isinstance(measurement, PhaseMeasurement)
    with duckdb.connect(str(candidate)) as conn:
        record_phase_evidence(
            conn,
            refresh_id=refresh_id,
            measurement=measurement,
            count=count,
        )
        conn.execute("CHECKPOINT")


@contextmanager
def candidate_generation(
    *, rebuild_indexes: bool = False
) -> Iterator[CandidateGeneration]:
    """Stage a complete materialization before replacing the serving generation.

    Ordinary incremental promotion uses a same-filesystem copy-on-write clone
    of the verified canonical generation. It therefore preserves compatible
    predecessor coverage without full logical reads or historical writes.
    ``rebuild_indexes=True`` is the explicit audited recovery path: it finds a
    readable verified retained generation and copies logical rows into fresh
    DuckDB index structures. Neither path seeds an empty candidate. Serving
    sidecars are untouched until verification succeeds, and SIGINT/SIGTERM
    archive only the candidate until the serving triple is published.
    """
    canonical = substrate_path()
    refresh_id = uuid4().hex
    candidate = canonical.with_name(
        f"{canonical.stem}.candidate-{refresh_id}{canonical.suffix}"
    )
    token = None
    published = False
    with _candidate_interruption_handlers():
        try:
            _archive_interrupted_candidates(canonical)
            from lynchpin.substrate.run_steps import log_phase_evidence, measure_phase

            if rebuild_indexes:
                seed_mode: Literal["reflink", "logical-index-rebuild"] = "logical-index-rebuild"
                seed_source = _latest_verified_generation(canonical)
                with measure_phase("candidate_write") as seed_measurement:
                    _logical_index_rebuild_seed(seed_source, candidate, refresh_id)
            else:
                if generation_refresh_id(canonical) is None:
                    raise CandidateGenerationRejected(
                        "steady-state candidate generation requires a verified canonical "
                        "generation; use --rebuild-candidate-indexes only for audited recovery"
                    )
                seed_mode = "reflink"
                seed_source = canonical
                with measure_phase("candidate_write") as seed_measurement:
                    _reflink_clone(canonical, candidate)
            _record_candidate_phase(
                candidate,
                refresh_id=refresh_id,
                measurement=seed_measurement,
                count=candidate.stat().st_size,
            )
            log_phase_evidence(seed_measurement, count=candidate.stat().st_size)

            token = _substrate_path_override.set(candidate)
            generation = CandidateGeneration(
                candidate=candidate,
                canonical=canonical,
                refresh_id=refresh_id,
                seed_source=seed_source,
                seed_mode=seed_mode,
            )
            yield generation
            if generation_refresh_id(candidate) is None:
                raise RuntimeError("candidate has no verified promoted generation")
            with measure_phase("publication") as publication_measurement:
                with _defer_candidate_interruptions() as deferred:
                    _publish_candidate(generation)
                    published = True
            # The serving triple has already moved, so this terminal metric is
            # deliberately journal-visible rather than another post-publication
            # database write that would make the snapshot stale.
            log_phase_evidence(publication_measurement, count=3)
            if deferred:
                raise CandidateGenerationInterrupted(deferred[0])
        except BaseException as exc:
            if published:
                log.warning(
                    "candidate generation was published before %s; canonical generation is serving: %s",
                    type(exc).__name__,
                    canonical,
                )
            else:
                label = "failed" if isinstance(exc, Exception) else "cancelled"
                _archive_candidate(candidate, label)
            raise
        finally:
            if token is not None:
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
    candidate = _substrate_path_override.get()
    if candidate is not None and not read_only and target != candidate:
        raise CandidateGenerationRejected(
            "candidate generation cannot write outside its staged substrate"
        )
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
