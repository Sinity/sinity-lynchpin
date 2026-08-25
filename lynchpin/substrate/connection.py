"""DuckDB substrate path management and lazy connection.

The substrate file lives at ``.lynchpin/duck/substrate.duckdb`` (under
``LynchpinConfig.generated_root`` so it sits alongside the JSON artifact dir,
not in the cachew cache).

Concurrency: single writer (materialization DAG); many readers via DuckDB's MVCC.
We never run concurrent writers today; the assumption is documented here so
future MCP-server work knows the constraint.

Schema versioning tracks ``SUBSTRATE_VERSION``. Explicit additive transitions
migrate in place; incompatible or unknown versions drop and recreate because
the substrate is derived from sources rather than authoritative.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
import ctypes
import errno
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import signal
import struct
import threading
from typing import TYPE_CHECKING, Any, Iterator, Literal
from uuid import uuid4

from lynchpin.substrate.locking import publication_lock

if TYPE_CHECKING:
    import duckdb

SUBSTRATE_VERSION = 44
"""Current schema contract; incompatible changes rebuild, declared additive changes migrate."""

log = logging.getLogger(__name__)
_CANCELLATION_SIGNALS = (signal.SIGINT, signal.SIGTERM)
_CANDIDATE_RECEIPT_PREFIX = "candidate-receipt-"
_CANDIDATE_RECEIPT_LIMIT = 16
_CANDIDATE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_PREVIOUS_TOKEN_RE = re.compile(
    r"^previous-[0-9]{8}T[0-9]{6}[+-][0-9]{4}-[0-9a-f]{32}$"
)


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


@dataclass
class CandidateGeneration:
    """A complete substrate generation staged before it becomes serving."""

    candidate: Path
    canonical: Path
    refresh_id: str
    seed_source: Path
    seed_mode: Literal["reflink", "logical-index-rebuild", "bootstrap"]
    seed_logical_rows: int
    receipt_refresh_id: str | None = None
    expected_refresh_id: str | None = None
    expected_graph_refresh_id: str | None = None
    predecessor_identity: tuple[int, int, int] | None = None
    phase_evidence: list[dict[str, object]] = field(default_factory=list)


@dataclass
class ServingGeneration:
    """One lock-consistent observation of the published serving triple.

    The shared publication lock remains held while callers inspect the
    connection, snapshot path, and status manifest.  A publisher therefore
    cannot replace one member of the triple between those observations.
    """

    connection: Any
    database_path: Path
    snapshot_path: Path
    manifest: dict[str, object] | None


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


def _substrate_path_value(path: Path | str | None = None) -> Path:
    """Coerce configured and explicit substrate paths at the public boundary."""
    if path is not None:
        return Path(path)
    candidate = _substrate_path_override.get()
    return candidate if candidate is not None else Path(substrate_path())


def substrate_read_snapshot_path() -> Path:
    """Return the path to the read-only snapshot of the substrate.

    The snapshot is a point-in-time hard copy used by MCP read tools when
    the canonical substrate is held under an exclusive write lock
    (materializations can hold the lock for 30-60+ minutes). The snapshot
    file is updated by ``update_read_snapshot()`` and stays available
    to readers regardless of the canonical's lock state.
    """
    return Path(substrate_path()).with_suffix(".read-snapshot.duckdb")


def update_read_snapshot(path: Path | str | None = None) -> Path | None:
    """Copy a substrate generation to its adjacent read-snapshot location.

    The optional path deliberately controls both the source and destination.
    Candidate promotion therefore creates a candidate snapshot instead of
    overwriting the serving snapshot before the candidate is verified.
    """
    canonical = _substrate_path_value(path)
    snapshot = canonical.with_suffix(".read-snapshot.duckdb")
    if not canonical.exists():
        return None
    # Same-filesystem reflinks preserve an immutable verified generation without
    # rereading or rewriting its historical pages. Atomic rename means readers
    # see either the previous complete snapshot or this complete clone.
    tmp = snapshot.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    try:
        _reflink_clone(canonical, tmp)
        tmp.replace(snapshot)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_directory(snapshot.parent)
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
    """Preserve a non-candidate diagnostic artifact such as a corrupt database."""
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


def _candidate_path(canonical: Path, attempt_id: str) -> Path:
    if not _CANDIDATE_ID_RE.fullmatch(attempt_id):
        raise CandidateGenerationRejected("invalid candidate attempt identity")
    return canonical.with_name(f"{canonical.stem}.candidate-{attempt_id}{canonical.suffix}")


def _valid_candidate_path(canonical: Path, path: Path) -> bool:
    return (
        path.parent == canonical.parent
        and not path.is_symlink()
        and path.name.startswith(f"{canonical.stem}.candidate-")
        and path.suffix == canonical.suffix
        and _CANDIDATE_ID_RE.fullmatch(path.stem.rsplit("-", 1)[-1]) is not None
    )


def _write_candidate_receipt(
    canonical: Path,
    *,
    attempt_id: str,
    logical_refresh_id: str | None,
    status: str,
    error: BaseException | None,
    phase_evidence: list[dict[str, object]] | None = None,
) -> Path:
    """Persist bounded evidence before removing an abandoned candidate."""
    receipt = canonical.with_name(f"{_CANDIDATE_RECEIPT_PREFIX}{attempt_id}.json")
    payload = {
        "schema": "lynchpin.substrate-candidate-receipt.v1",
        "candidate_attempt_id": attempt_id,
        "logical_refresh_id": logical_refresh_id,
        "status": status,
        "error": f"{type(error).__name__}: {error}" if error is not None else None,
        "phase_evidence": (phase_evidence or [])[-16:],
    }
    temporary = receipt.with_name(f".{receipt.name}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    _fsync_file(temporary)
    _replace(temporary, receipt)
    _fsync_directory(receipt.parent)
    _prune_candidate_receipts(canonical)
    return receipt


def _prune_candidate_receipts(canonical: Path) -> tuple[Path, ...]:
    """Keep only the newest bounded set of validated diagnostic receipts."""
    receipts: list[Path] = []
    for path in canonical.parent.glob(f"{_CANDIDATE_RECEIPT_PREFIX}*.json"):
        attempt_id = path.name.removeprefix(_CANDIDATE_RECEIPT_PREFIX).removesuffix(".json")
        if (
            path.parent == canonical.parent
            and not path.is_symlink()
            and _CANDIDATE_ID_RE.fullmatch(attempt_id)
        ):
            receipts.append(path)
    receipts.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    removed = tuple(receipts[_CANDIDATE_RECEIPT_LIMIT:])
    for path in removed:
        path.unlink()
    if removed:
        _fsync_directory(canonical.parent)
    return removed


def _remove_candidate(candidate: Path, canonical: Path) -> tuple[Path, ...]:
    """Remove only a validated candidate and its known sidecars."""
    if not _valid_candidate_path(canonical, candidate):
        raise CandidateGenerationRejected("candidate cleanup path is outside the substrate directory")
    removed: list[Path] = []
    for artifact in _candidate_artifacts(candidate):
        if artifact.exists():
            artifact.unlink()
            removed.append(artifact)
    if removed:
        _fsync_directory(canonical.parent)
    return tuple(removed)


def _recover_interrupted_candidate(canonical: Path, candidate: Path) -> None:
    """Extract a small receipt and discard an unclean candidate on startup."""
    attempt_id = candidate.stem.rsplit("-", 1)[-1]
    try:
        _write_candidate_receipt(
            canonical,
            attempt_id=attempt_id,
            logical_refresh_id=None,
            status="interrupted",
            error=None,
        )
        _remove_candidate(candidate, canonical)
    except OSError:
        log.exception("could not clean interrupted candidate: %s", candidate)


def _archive_interrupted_candidates(canonical: Path) -> None:
    """Bound candidates left behind when a process exits without unwinding."""
    pattern = f"{canonical.stem}.candidate-*{canonical.suffix}"
    for candidate in canonical.parent.glob(pattern):
        if _valid_candidate_path(canonical, candidate):
            _recover_interrupted_candidate(canonical, candidate)
            log.warning("removed interrupted candidate while retaining canonical generation: %s", canonical)


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


def _archive_name(path: Path, label: str, token: str | None = None) -> Path:
    suffix = token or f"{datetime.now().astimezone():%Y%m%dT%H%M%S%z}-{uuid4().hex}"
    return path.with_name(
        f"{path.name}.{label}-{suffix}"
    )


def _fsync_directory(path: Path) -> None:
    """Persist directory entry changes before a generation is reported serving."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace(source: Path, destination: Path) -> None:
    """Indirection kept injectable for publication-failure regression tests."""
    source.replace(destination)


def _publication_intent_path(canonical: Path) -> Path:
    return canonical.with_name(f".{canonical.name}.publication.json")


def _writer_reservation_lock_path(canonical: Path) -> Path:
    """Return the lock that admits exactly one candidate builder."""
    return canonical.with_name(f".{canonical.name}.writer.lock")


def _artifact_identity(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"device": stat.st_dev, "inode": stat.st_ino, "size": stat.st_size}


def _identity_matches(path: Path, identity: dict[str, object]) -> bool:
    try:
        return _artifact_identity(path) == identity
    except OSError:
        return False


def _write_publication_intent(path: Path, intent: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(intent, sort_keys=True), encoding="utf-8")
    _fsync_file(temporary)
    _replace(temporary, path)
    _fsync_directory(path.parent)


def _publication_step(_step: str) -> None:
    """Injectable durable-step hook used to simulate process death in tests."""


def _reconcile_publication(canonical: Path) -> None:
    """Finish or roll back an interrupted serving-triple publication.

    The intent is persisted before a serving name changes. A reader calls this
    before opening the substrate, so a SIGKILL between individual renames never
    exposes a mixed database, snapshot, and manifest generation.
    """
    intent_path = _publication_intent_path(canonical)
    if not intent_path.exists():
        return
    try:
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        replacements = intent["replacements"]
        backups = intent["backups"]
        had_target = set(intent["had_target"])
        if not isinstance(replacements, list) or not isinstance(backups, dict):
            raise ValueError("invalid publication intent")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise CandidateGenerationRejected("unreadable substrate publication intent") from exc

    pending: list[dict[str, object]] = []
    published: list[dict[str, object]] = []
    inconsistent = False
    for entry in replacements:
        if not isinstance(entry, dict):
            inconsistent = True
            continue
        target = Path(str(entry["target"]))
        candidate = Path(str(entry["candidate"]))
        identity = entry["identity"]
        if not isinstance(identity, dict):
            inconsistent = True
        elif _identity_matches(target, identity):
            published.append(entry)
        elif _identity_matches(candidate, identity):
            pending.append(entry)
        else:
            inconsistent = True
    if not inconsistent:
        try:
            for entry in pending:
                _replace(Path(str(entry["candidate"])), Path(str(entry["target"])))
                published.append(entry)
            _fsync_directory(canonical.parent)
        except OSError:
            inconsistent = True
    if inconsistent:
        for entry in reversed(published):
            target = Path(str(entry["target"]))
            backup_name = backups.get(str(target))
            if backup_name:
                _replace(Path(str(backup_name)), target)
            elif str(target) not in had_target:
                target.unlink(missing_ok=True)
        _fsync_directory(canonical.parent)
    intent_path.unlink(missing_ok=True)
    _fsync_directory(canonical.parent)


@contextmanager
def _promotion_lock(canonical: Path) -> Iterator[None]:
    """Reserve the sole candidate builder without blocking serving readers."""
    lock_path = _writer_reservation_lock_path(canonical)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CandidateGenerationRejected("another substrate promotion owns the writer lock") from exc
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextmanager
def _publication_read_lock(canonical: Path) -> Iterator[None]:
    """Prevent an active publisher from moving serving artifacts during open."""
    with publication_lock(canonical, exclusive=False):
        yield


@contextmanager
def _publication_write_lock(canonical: Path) -> Iterator[None]:
    """Exclude complete serving observations only while publishing a triple."""
    with publication_lock(canonical, exclusive=True):
        yield


@contextmanager
def _stable_publication_read(canonical: Path) -> Iterator[None]:
    """Open only after any active publication has completed or been recovered."""
    while True:
        with _publication_read_lock(canonical):
            if not _publication_intent_path(canonical).exists():
                yield
                return
        # A durable intent without a lock owner is an interrupted publisher.
        # Reconcile while holding the same exclusive lock that protects a live
        # publisher, then retry the stable shared-lock read path.
        with _publication_write_lock(canonical):
            _reconcile_publication(canonical)


def _publication_backups(targets: tuple[Path, ...]) -> dict[Path, Path]:
    """Hard-link serving artifacts before any atomic replacement occurs."""
    backups: dict[Path, Path] = {}
    token = f"{datetime.now().astimezone():%Y%m%dT%H%M%S%z}-{uuid4().hex}"
    for target in targets:
        if target.exists():
            backup = _archive_name(target, "previous", token)
            os.link(target, backup)
            backups[target] = backup
    if backups:
        _fsync_directory(targets[0].parent)
    return backups


def _previous_artifact(canonical: Path, path: Path) -> tuple[str, str] | None:
    """Return (role, token) only for a canonical, adjacent previous artifact."""
    if path.parent != canonical.parent or path.is_symlink():
        return None
    roles = {
        canonical.name: "database",
        canonical.with_suffix(".read-snapshot.duckdb").name: "snapshot",
        canonical.with_suffix(".manifest.json").name: "manifest",
    }
    for base_name, role in roles.items():
        prefix = f"{base_name}."
        if path.name.startswith(prefix):
            token = path.name[len(prefix) :]
            if _PREVIOUS_TOKEN_RE.fullmatch(token):
                return role, token.removeprefix("previous-")
    return None


def substrate_retention_census(path: Path | str | None = None) -> dict[str, object]:
    """Describe eligible previous triples without changing the substrate."""
    canonical = _substrate_path_value(path)
    groups: dict[str, dict[str, Path]] = {}
    invalid: list[str] = []
    eligible: list[str] = []
    for entry in canonical.parent.iterdir():
        parsed = _previous_artifact(canonical, entry)
        if parsed is None:
            continue
        role, token = parsed
        groups.setdefault(token, {})[role] = entry

    triples: list[dict[str, object]] = []
    for token, artifacts in groups.items():
        database = artifacts.get("database")
        complete = all(role in artifacts for role in ("database", "snapshot", "manifest"))
        verified = database is not None and generation_refresh_id(database) is not None
        eligible.extend(str(p) for p in artifacts.values())
        if not complete or not verified:
            invalid.extend(str(p) for p in artifacts.values())
            continue
        assert database is not None
        refresh_id = generation_refresh_id(database)
        triples.append(
            {
                "token": token,
                "refresh_id": refresh_id,
                "artifacts": {role: str(artifacts[role]) for role in artifacts},
            }
        )
    triples.sort(key=lambda item: str(item["token"]), reverse=True)
    return {
        "canonical": str(canonical),
        "serving": [
            str(canonical),
            str(canonical.with_suffix(".read-snapshot.duckdb")),
            str(canonical.with_suffix(".manifest.json")),
        ],
        "verified_previous": triples,
        "eligible_previous": eligible,
        "invalid_previous": invalid,
    }


def substrate_retention_plan(
    path: Path | str | None = None, *, retain_failed: bool = False
) -> dict[str, object]:
    """Return the bounded deletion plan; this function never deletes files."""
    canonical = _substrate_path_value(path)
    census = substrate_retention_census(canonical)
    verified = census["verified_previous"]
    assert isinstance(verified, list)
    keep = verified[:1]
    eligible = census["eligible_previous"]
    assert isinstance(eligible, list)
    delete: list[str] = []
    kept_paths = {
        str(value)
        for triple in keep
        for value in (triple["artifacts"].values() if isinstance(triple, dict) else ())
    }
    delete.extend(
        value
        for value in eligible
        if isinstance(value, str) and value not in kept_paths
    )
    return {
        "canonical": str(canonical),
        "retain_failed": retain_failed,
        "keep_previous": keep,
        "delete": delete,
        "invalid_not_eligible": [],
        "dry_run": True,
    }


def apply_substrate_retention(
    path: Path | str | None = None, *, dry_run: bool = True, retain_failed: bool = False
) -> dict[str, object]:
    """Apply a validated bounded retention plan, or return it as a dry run."""
    canonical = _substrate_path_value(path)
    if dry_run:
        return substrate_retention_plan(canonical, retain_failed=retain_failed)
    with _publication_write_lock(canonical):
        _reconcile_publication(canonical)
        plan = substrate_retention_plan(canonical, retain_failed=retain_failed)
        deleted: list[str] = []
        planned_delete = plan["delete"]
        assert isinstance(planned_delete, list)
        for value in planned_delete:
            target = Path(str(value))
            if target.parent != canonical.parent or _previous_artifact(canonical, target) is None:
                raise CandidateGenerationRejected("retention plan contained an unsafe path")
            target.unlink(missing_ok=True)
            deleted.append(str(target))
        if deleted:
            _fsync_directory(canonical.parent)
        plan["deleted"] = deleted
        plan["dry_run"] = False
        return plan


def _rollback_publication(
    *, backups: dict[Path, Path], replaced: tuple[Path, ...], had_target: set[Path]
) -> None:
    """Restore every serving name after a failed publication rename."""
    for target in reversed(replaced):
        backup = backups.get(target)
        if backup is not None:
            _replace(backup, target)
        elif target not in had_target:
            target.unlink(missing_ok=True)
    if replaced:
        _fsync_directory(replaced[0].parent)


def _assert_candidate_manifest_identity(
    candidate_manifest: Path,
    expected_refresh_id: str,
    expected_graph_refresh_id: str | None,
) -> None:
    try:
        manifest = __import__("json").loads(candidate_manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CandidateGenerationRejected("candidate publication manifest is unreadable") from exc
    if manifest.get("latest_refresh_id") != expected_refresh_id:
        raise CandidateGenerationRejected(
            "candidate publication manifest does not identify the requested refresh"
        )
    if (
        expected_graph_refresh_id is not None
        and manifest.get("latest_graph_refresh_id") != expected_graph_refresh_id
    ):
        raise CandidateGenerationRejected(
            "candidate publication manifest does not identify the requested graph refresh"
        )


def _publish_candidate(generation: CandidateGeneration) -> None:
    """Publish a checked candidate with durable intent and read reconciliation."""
    from lynchpin.substrate.status_manifest import (
        substrate_status_manifest_path,
        write_substrate_status_manifest,
    )

    # Building the snapshot and manifest touches only candidate artifacts, so
    # do it before acquiring the short publication lock.
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

    if generation.expected_refresh_id is not None:
        _assert_candidate_manifest_identity(
            candidate_manifest,
            generation.expected_refresh_id,
            generation.expected_graph_refresh_id,
        )

    for artifact in (generation.candidate, candidate_snapshot, candidate_manifest):
        _fsync_file(artifact)

    with _publication_write_lock(generation.canonical):
        if generation.predecessor_identity is not None:
            current = _artifact_identity(generation.canonical)
            if current != {
                "device": generation.predecessor_identity[0],
                "inode": generation.predecessor_identity[1],
                "size": generation.predecessor_identity[2],
            }:
                raise CandidateGenerationRejected("candidate predecessor changed before publication")
        serving_snapshot = generation.canonical.with_suffix(".read-snapshot.duckdb")
        serving_manifest = substrate_status_manifest_path(generation.canonical)
        replacements = (
            (generation.candidate, generation.canonical),
            (candidate_snapshot, serving_snapshot),
            (candidate_manifest, serving_manifest),
        )
        targets = tuple(target for _candidate, target in replacements)
        had_target = {target for target in targets if target.exists()}
        backups = _publication_backups(targets)
        intent = {
            "schema": "lynchpin.substrate-publication.v1",
            "replacements": [
                {
                    "candidate": str(candidate),
                    "target": str(target),
                    "identity": _artifact_identity(candidate),
                }
                for candidate, target in replacements
            ],
            "backups": {str(target): str(backup) for target, backup in backups.items()},
            "had_target": [str(target) for target in had_target],
        }
        intent_path = _publication_intent_path(generation.canonical)
        replaced: list[Path] = []
        try:
            _write_publication_intent(intent_path, intent)
            _publication_step("intent-durable")
            for index, (candidate, target) in enumerate(replacements):
                _replace(candidate, target)
                replaced.append(target)
                _fsync_directory(generation.canonical.parent)
                _publication_step(f"replacement-{index}-durable")
            intent_path.unlink(missing_ok=True)
            _fsync_directory(generation.canonical.parent)
            _publication_step("intent-cleared")
        except Exception:
            _rollback_publication(
                backups=backups,
                replaced=tuple(replaced),
                had_target=had_target,
            )
            intent_path.unlink(missing_ok=True)
            _fsync_directory(generation.canonical.parent)
            raise


def _quote_identifier(identifier: str) -> str:
    """Quote an identifier obtained from DuckDB catalog metadata."""
    return '"' + identifier.replace('"', '""') + '"'


# linux/fs.h FICLONE: clone source extents into an empty destination file.
# The managed derived root is Btrfs; refusing a non-CoW filesystem is safer
# than silently returning to whole-generation reads and writes.
_FICLONE = 0x40049409
_FS_IOC_GETFLAGS = 0x80086601
_FS_NOCOW_FL = 0x00800000
_BTRFS_SUPER_MAGIC = 0x9123683E


class _StatFs(ctypes.Structure):
    _fields_ = [
        ("f_type", ctypes.c_long),
        ("f_bsize", ctypes.c_long),
        ("f_blocks", ctypes.c_ulong),
        ("f_bfree", ctypes.c_ulong),
        ("f_bavail", ctypes.c_ulong),
        ("f_files", ctypes.c_ulong),
        ("f_ffree", ctypes.c_ulong),
        ("f_fsid", ctypes.c_int * 2),
        ("f_namelen", ctypes.c_long),
        ("f_frsize", ctypes.c_long),
        ("f_flags", ctypes.c_long),
        ("f_spare", ctypes.c_long * 4),
    ]


def _filesystem_type(path: Path) -> int:
    info = _StatFs()
    result = ctypes.CDLL(None, use_errno=True).statfs(os.fsencode(path), ctypes.byref(info))
    if result != 0:
        raise OSError(ctypes.get_errno(), f"statfs failed for {path}")
    return int(info.f_type)


def _file_flags(path: Path) -> int:
    flags = bytearray(4)
    fd = os.open(path, os.O_RDONLY | (os.O_DIRECTORY if path.is_dir() else 0))
    try:
        fcntl.ioctl(fd, _FS_IOC_GETFLAGS, flags, True)
    finally:
        os.close(fd)
    return struct.unpack("I", flags)[0]


def _wal_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.wal")


def _assert_reflink_preflight(source: Path, destination: Path) -> None:
    """Reject clones that cannot preserve CoW and checkpoint semantics."""
    destination_parent = destination.parent
    source_stat = source.stat()
    destination_stat = destination_parent.stat()
    if source_stat.st_dev != destination_stat.st_dev:
        raise CandidateGenerationRejected("copy-on-write clone requires one filesystem")
    if (
        _filesystem_type(source) != _BTRFS_SUPER_MAGIC
        or _filesystem_type(destination_parent) != _BTRFS_SUPER_MAGIC
    ):
        raise CandidateGenerationRejected("copy-on-write clone requires a Btrfs source and destination")
    for path, role in (
        (source, "source file"),
        (source.parent, "source directory"),
        (destination_parent, "destination directory"),
    ):
        if _file_flags(path) & _FS_NOCOW_FL:
            raise CandidateGenerationRejected(
                f"copy-on-write clone rejects NOCOW {role}: {path}"
            )
    if _wal_path(source).exists():
        raise CandidateGenerationRejected(
            "copy-on-write clone refuses an uncheckpointed DuckDB WAL predecessor"
        )


def _reflink_clone(source: Path, destination: Path) -> None:
    """Create a same-filesystem CoW clone without a full logical reseed."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    _assert_reflink_preflight(source, destination)
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


def _logical_index_rebuild_seed(source: Path, candidate: Path, refresh_id: str) -> int:
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
    return copied_rows


def _latest_verified_generation(canonical: Path) -> Path:
    """Find the newest readable verified generation without trusting file names.

    A prior candidate or corruption quarantine can be the only readable source
    after DuckDB rejects the current serving file. Promotion metadata and source
    coverage, rather than an archive suffix or modification time, establish
    whether a path is eligible to seed a new candidate.
    """
    import duckdb

    candidates = [canonical]
    census = substrate_retention_census(canonical)
    verified_previous = census["verified_previous"]
    assert isinstance(verified_previous, list)
    for triple in verified_previous:
        assert isinstance(triple, dict)
        artifacts = triple["artifacts"]
        assert isinstance(artifacts, dict)
        database = artifacts.get("database")
        if database is not None:
            candidates.append(Path(str(database)))
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
    metrics: tuple[dict[str, object], ...],
    receipt_refresh_id: str | None = None,
) -> dict[str, object]:
    """Append phase evidence while the candidate is the only writable generation."""
    import duckdb

    from lynchpin.substrate.run_steps import PhaseMeasurement, record_phase_evidence

    assert isinstance(measurement, PhaseMeasurement)
    target_refresh_id = receipt_refresh_id or refresh_id
    try:
        with duckdb.connect(str(candidate)) as conn:
            payload = record_phase_evidence(
                conn,
                refresh_id=target_refresh_id,
                measurement=measurement,
                metrics=metrics,  # type: ignore[arg-type]
            )
            conn.execute("CHECKPOINT")
        payload["refresh_id"] = target_refresh_id
    except Exception as exc:  # noqa: BLE001 - receipt loss must not discard canonical work.
        payload = measurement.payload(metrics=metrics)  # type: ignore[arg-type]
        payload["refresh_id"] = target_refresh_id
        payload["receipt_status"] = "degraded"
        payload["receipt_error"] = f"{type(exc).__name__}: {exc}"
        log.warning(
            "candidate seed receipt write failed: refresh_id=%s status=degraded error=%s",
            target_refresh_id,
            exc,
        )
    return payload


def bind_candidate_publication(
    generation: CandidateGeneration,
    expected_refresh_id: str,
    *,
    require_graph: bool = True,
) -> None:
    """Require publication to carry the exact promotion and optional graph refresh."""
    if generation.expected_refresh_id is not None and generation.expected_refresh_id != expected_refresh_id:
        raise CandidateGenerationRejected("candidate publication refresh identity changed during build")
    generation.expected_refresh_id = expected_refresh_id
    if require_graph:
        if (
            generation.expected_graph_refresh_id is not None
            and generation.expected_graph_refresh_id != expected_refresh_id
        ):
            raise CandidateGenerationRejected("candidate publication graph refresh changed during build")
        generation.expected_graph_refresh_id = expected_refresh_id


def _verify_candidate_publication_contract(
    candidate: Path,
    expected_refresh_id: str,
    expected_graph_refresh_id: str | None,
) -> None:
    """Check exact graph, promotion, and source coverage before publication."""
    import duckdb

    try:
        with duckdb.connect(str(candidate), read_only=True) as conn:
            promotion = conn.execute(
                """
                SELECT status FROM substrate_promotion_run
                WHERE refresh_id = ?
                """,
                [expected_refresh_id],
            ).fetchone()
            graph = (
                conn.execute(
                    "SELECT 1 FROM evidence_graph_build WHERE refresh_id = ?",
                    [expected_graph_refresh_id],
                ).fetchone()
                if expected_graph_refresh_id is not None
                else None
            )
            coverage = conn.execute(
                """
                SELECT COUNT(*)
                FROM substrate_source_status
                WHERE refresh_id = ?
                """,
                [expected_refresh_id],
            ).fetchone()
            graph_coverage = (
                conn.execute(
                    """
                    SELECT status FROM substrate_source_status
                    WHERE refresh_id = ? AND source = 'evidence_graph'
                    ORDER BY recorded_at DESC LIMIT 1
                    """,
                    [expected_graph_refresh_id],
                ).fetchone()
                if expected_graph_refresh_id is not None
                else None
            )
    except duckdb.Error as exc:
        raise CandidateGenerationRejected("candidate publication contract cannot be read") from exc
    if promotion is None or promotion[0] not in {"ok", "degraded"}:
        raise CandidateGenerationRejected("candidate is missing the requested promotion refresh")
    if expected_graph_refresh_id is not None and graph is None:
        raise CandidateGenerationRejected("candidate is missing the requested evidence graph refresh")
    if not coverage or int(coverage[0]) == 0:
        raise CandidateGenerationRejected("candidate is missing source coverage for requested refresh")
    if (
        expected_graph_refresh_id is not None
        and (graph_coverage is None or graph_coverage[0] != "ok")
    ):
        raise CandidateGenerationRejected("candidate is missing usable evidence-graph coverage")


def _bind_candidate_attempt_evidence(generation: CandidateGeneration) -> None:
    """Make successful-refresh evidence point at the candidate attempt that produced it."""
    if generation.expected_refresh_id is None:
        return
    import json
    import duckdb

    from lynchpin.substrate.run_steps import record_run_step

    try:
        with duckdb.connect(str(generation.candidate)) as conn:
            record_run_step(
                conn,
                refresh_id=generation.expected_refresh_id,
                step="candidate_attempt_evidence",
                status="ok",
                message=json.dumps(
                    {
                        "schema": "lynchpin.candidate-attempt-evidence.v1",
                        "candidate_attempt_id": generation.refresh_id,
                        "published_refresh_id": generation.expected_refresh_id,
                        "candidate_seed": {
                            "mode": generation.seed_mode,
                            "source": str(generation.seed_source),
                            "logical_rows_reconstructed": generation.seed_logical_rows,
                            "candidate_bytes": generation.candidate.stat().st_size,
                        },
                        "phases": generation.phase_evidence,
                    },
                    sort_keys=True,
                ),
                row_count=None,
            )
            conn.execute("CHECKPOINT")
    except Exception as exc:  # noqa: BLE001 - canonical work is already complete.
        generation.phase_evidence.append(
            {
                "schema": "lynchpin.candidate-attempt-evidence.v1",
                "refresh_id": generation.expected_refresh_id,
                "receipt_status": "degraded",
                "receipt_error": f"{type(exc).__name__}: {exc}",
                "published_refresh_id": generation.expected_refresh_id,
            }
        )
        log.warning(
            "candidate attempt receipt write failed: refresh_id=%s status=degraded error=%s",
            generation.expected_refresh_id,
            exc,
        )


@contextmanager
def candidate_generation(
    *,
    rebuild_indexes: bool = False,
    bootstrap: bool = False,
    receipt_refresh_id: str | None = None,
    retain_failed: bool = False,
) -> Iterator[CandidateGeneration]:
    """Stage a complete materialization before replacing the serving generation.

    Ordinary incremental promotion uses a same-filesystem copy-on-write clone
    of the verified canonical generation. It therefore preserves compatible
    predecessor coverage without full logical reads or historical writes.
    ``rebuild_indexes=True`` is the explicit audited recovery path: it finds a
    readable verified retained generation and copies logical rows into fresh
    DuckDB index structures. ``bootstrap=True`` is reserved for initial
    recovery when no serving artifacts exist. Ordinary callers must not use
    it. Serving sidecars are untouched until verification succeeds, and SIGINT/SIGTERM
    archive only the candidate until the serving triple is published.
    """
    canonical = _substrate_path_value()
    refresh_id = uuid4().hex
    candidate = _candidate_path(canonical, refresh_id)
    token = None
    published = False
    generation: CandidateGeneration | None = None
    with _promotion_lock(canonical), _candidate_interruption_handlers():
        try:
            with _publication_write_lock(canonical):
                _reconcile_publication(canonical)
            _archive_interrupted_candidates(canonical)
            from lynchpin.substrate.run_steps import log_phase_evidence, measure_phase

            if rebuild_indexes and bootstrap:
                raise CandidateGenerationRejected(
                    "candidate bootstrap and logical-index rebuild are mutually exclusive"
                )
            if bootstrap:
                serving_artifacts = (
                    canonical,
                    _wal_path(canonical),
                    substrate_read_snapshot_path(),
                    canonical.with_suffix(".manifest.json"),
                )
                if any(path.exists() for path in serving_artifacts):
                    raise CandidateGenerationRejected(
                        "candidate bootstrap requires no serving database, WAL, snapshot, or manifest"
                    )
                seed_mode: Literal["reflink", "logical-index-rebuild", "bootstrap"] = "bootstrap"
                seed_source = canonical
                seed_logical_rows = 0
                with measure_phase("candidate_write") as seed_measurement:
                    import duckdb

                    with duckdb.connect(str(candidate)) as conn:
                        apply_schema(conn)
                        conn.execute("CHECKPOINT")
            elif rebuild_indexes:
                seed_mode: Literal["reflink", "logical-index-rebuild"] = "logical-index-rebuild"
                with _publication_read_lock(canonical):
                    seed_source = _latest_verified_generation(canonical)
                    with measure_phase("candidate_write") as seed_measurement:
                        seed_logical_rows = _logical_index_rebuild_seed(
                            seed_source, candidate, refresh_id
                        )
            else:
                with _publication_read_lock(canonical):
                    if generation_refresh_id(canonical) is None:
                        raise CandidateGenerationRejected(
                            "steady-state candidate generation requires a verified canonical "
                            "generation; use --bootstrap for an empty substrate or "
                            "--rebuild-candidate-indexes for audited recovery"
                        )
                    seed_mode = "reflink"
                    seed_source = canonical
                    seed_logical_rows = 0
                    with measure_phase("candidate_write") as seed_measurement:
                        _reflink_clone(canonical, candidate)
            with _publication_read_lock(canonical):
                predecessor = _artifact_identity(canonical) if canonical.exists() else None
            candidate_phase = _record_candidate_phase(
                candidate,
                refresh_id=refresh_id,
                receipt_refresh_id=receipt_refresh_id,
                measurement=seed_measurement,
                metrics=(
                    {
                        "name": "candidate_generation_size",
                        "unit": "bytes",
                        "value": candidate.stat().st_size,
                    },
                    {
                        "name": "candidate_seed_logical_rows",
                        "unit": "rows",
                        "value": seed_logical_rows,
                    },
                ),
            )
            log_phase_evidence(
                seed_measurement,
                metrics=(
                    {
                        "name": "candidate_generation_size",
                        "unit": "bytes",
                        "value": candidate.stat().st_size,
                    },
                    {
                        "name": "candidate_seed_logical_rows",
                        "unit": "rows",
                        "value": seed_logical_rows,
                    },
                ),  # type: ignore[arg-type]
            )

            token = _substrate_path_override.set(candidate)
            generation = CandidateGeneration(
                candidate=candidate,
                canonical=canonical,
                refresh_id=refresh_id,
                seed_source=seed_source,
                seed_mode=seed_mode,
                seed_logical_rows=seed_logical_rows,
                receipt_refresh_id=receipt_refresh_id,
                predecessor_identity=(
                    predecessor["device"], predecessor["inode"], predecessor["size"]
                ) if predecessor is not None else None,
            )
            generation.phase_evidence.append(candidate_phase)
            yield generation
            if generation.expected_refresh_id is None:
                if generation_refresh_id(candidate) is None:
                    raise RuntimeError("candidate has no verified promoted generation")
            else:
                _verify_candidate_publication_contract(
                    candidate,
                    generation.expected_refresh_id,
                    generation.expected_graph_refresh_id,
                )
                _bind_candidate_attempt_evidence(generation)
            with measure_phase("publication") as publication_measurement:
                with _defer_candidate_interruptions() as deferred:
                    _publish_candidate(generation)
                    published = True
            # The serving triple has already moved, so this terminal metric is
            # deliberately journal-visible rather than another post-publication
            # database write that would make the snapshot stale.
            log_phase_evidence(
                publication_measurement,
                metrics=(
                    {"name": "published_artifacts", "unit": "artifacts", "value": 3},
                ),  # type: ignore[arg-type]
            )
            # Publication has completed atomically; only now may older rollback
            # triples be removed. The census and path validation protect all
            # serving names from this cleanup.
            apply_substrate_retention(generation.canonical, dry_run=False)
            if deferred:
                raise CandidateGenerationInterrupted(deferred[0])
        except BaseException as exc:
            if isinstance(exc, CandidateGenerationInterrupted):
                status = "interrupted"
            elif isinstance(exc, Exception):
                status = "failed"
            else:
                status = "cancelled"
            attempt_id = generation.refresh_id if generation is not None else refresh_id
            logical_refresh_id = (
                (
                    generation.expected_refresh_id or generation.receipt_refresh_id
                    if generation is not None
                    else receipt_refresh_id
                )
            )
            _write_candidate_receipt(
                canonical,
                attempt_id=attempt_id,
                logical_refresh_id=logical_refresh_id,
                status=status,
                error=exc,
                phase_evidence=generation.phase_evidence if generation is not None else None,
            )
            if not published and not retain_failed:
                _remove_candidate(candidate, canonical)
            if published:
                log.warning(
                    "candidate generation was published before %s; canonical generation is serving: %s",
                    type(exc).__name__,
                    canonical,
                )
            raise
        finally:
            if token is not None:
                _substrate_path_override.reset(token)


@contextmanager
def bootstrap_candidate_generation(
    *, receipt_refresh_id: str | None = None, retain_failed: bool = False
) -> Iterator[CandidateGeneration]:
    """Explicit initial/recovery API for an empty serving-artifact set only."""
    with candidate_generation(
        bootstrap=True,
        receipt_refresh_id=receipt_refresh_id,
        retain_failed=retain_failed,
    ) as generation:
        yield generation


def _quarantine_suffix() -> str:
    return datetime.now().astimezone().strftime(".corrupt-%Y%m%dT%H%M%S%z")


def rebuild_corrupt_substrate(
    canonical: Path | str | None = None,
) -> Path:
    """Archive a corrupt generation and require verified logical recovery.

    A readable snapshot does not prove a safe write predecessor. The corrupt
    database and WAL are retained for evidence, then the caller must invoke
    the explicit logical candidate rebuild from a verified generation.
    """
    canonical = _substrate_path_value(canonical)
    archived = _archive_candidate(canonical, "corrupt")
    if not archived:
        raise CandidateGenerationRejected("corrupt substrate disappeared before it could be archived")
    raise CandidateGenerationRejected(
        "corrupt substrate was archived; rerun --all --promote --rebuild-candidate-indexes "
        "to rebuild from a verified logical predecessor"
    )


@contextmanager
def connect(
    path: Path | str | None = None,
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

    target = _substrate_path_value(path)
    canonical_target = target
    candidate = _substrate_path_override.get()
    if candidate is not None and not read_only and target != candidate:
        raise CandidateGenerationRejected(
            "candidate generation cannot write outside its staged substrate"
        )
    serving_target = target == _substrate_path_value() and _substrate_path_override.get() is None
    if serving_target and not read_only:
        if target.exists() or _publication_intent_path(target).exists():
            with _publication_write_lock(target):
                _reconcile_publication(target)
        if not rebuild_corrupt:
            raise CandidateGenerationRejected(
                "direct canonical substrate writes are rejected; run the writer through "
                "candidate_generation or bootstrap_candidate_generation"
            )
    # A failed recovery may leave a clean but unpromoted canonical database.
    # Prefer the prior verified snapshot in that state rather than making read
    # clients observe an empty schema as if it were a successful generation.
    if read_only and snapshot_fallback and canonical_target == _substrate_path_value():
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
    stable_read = (
        _stable_publication_read(canonical_target)
        if serving_target and read_only
        else nullcontext()
    )
    with stable_read:
        try:
            conn = duckdb.connect(str(target), read_only=read_only)
        except (duckdb.IOException, duckdb.ConnectionException, duckdb.InternalException) as exc:
            if not read_only or not snapshot_fallback:
                if rebuild_corrupt and not read_only and isinstance(exc, duckdb.InternalException):
                    rebuild_corrupt_substrate(target)
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


@contextmanager
def serving_generation(path: Path | str | None = None) -> Iterator[ServingGeneration]:
    """Yield a connection and status manifest from one published generation.

    This is the API for consumers that need the serving database, immutable
    snapshot, and manifest to agree.  It holds the shared publication lock for
    the entire observation, including the DuckDB connection lifetime.
    """
    import duckdb

    target = _substrate_path_value(path)
    if target != _substrate_path_value() or _substrate_path_override.get() is not None:
        with connect(target, read_only=True) as conn:
            yield ServingGeneration(
                connection=conn,
                database_path=target,
                snapshot_path=target.with_suffix(".read-snapshot.duckdb"),
                manifest=None,
            )
        return

    from lynchpin.substrate.status_manifest import _load_stable_substrate_status_manifest

    with _stable_publication_read(target):
        selected = target
        snapshot = substrate_read_snapshot_path()
        if generation_refresh_id(target) is None and generation_refresh_id(snapshot) is not None:
            selected = snapshot
        try:
            conn = duckdb.connect(str(selected), read_only=True)
        except (duckdb.IOException, duckdb.ConnectionException, duckdb.InternalException):
            if selected == snapshot or not snapshot.exists():
                raise
            selected = snapshot
            conn = duckdb.connect(str(selected), read_only=True)
        try:
            yield ServingGeneration(
                connection=conn,
                database_path=selected,
                snapshot_path=snapshot,
                # The only serving manifest describes canonical inode metadata.
                # A fallback snapshot has no matching sidecar, so returning the
                # canonical manifest here would create a mixed generation.
                manifest=(
                    _load_stable_substrate_status_manifest(target)
                    if selected == target
                    else None
                ),
            )
        finally:
            conn.close()


def reset_substrate(path: Path | str | None = None) -> None:
    """Delete the substrate file. Used by tests and on schema-version bump."""
    target = _substrate_path_value(path)
    if target.exists():
        target.unlink()


def apply_schema(conn: "duckdb.DuckDBPyConnection") -> None:
    """Apply the substrate DDL idempotently.

    Reads ``SUBSTRATE_VERSION`` from a ``substrate_meta`` table. Declared
    additive transitions migrate in place; absent, unknown, or incompatible
    versions drop all tables and reapply the full DDL. Otherwise this is a no-op.
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

    if current == 43 and SUBSTRATE_VERSION == 44:
        conn.execute(
            "ALTER TABLE evidence_graph_build ADD COLUMN IF NOT EXISTS "
            "predecessor_refresh_id VARCHAR"
        )
        conn.execute(
            "ALTER TABLE evidence_graph_build ADD COLUMN IF NOT EXISTS "
            "predecessor_tail_start DATE"
        )
        conn.execute(
            "INSERT OR REPLACE INTO substrate_meta VALUES ('version', ?)",
            [str(SUBSTRATE_VERSION)],
        )
    elif current != SUBSTRATE_VERSION:
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
    keep_latest_n: int = 1, dry_run: bool = True, path: Path | str | None = None
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
    target = _substrate_path_value(path)
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
