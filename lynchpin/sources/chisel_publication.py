"""Transactional publication for Chisel output trees.

The caller builds only inside the yielded sibling candidate.  Publication
validates selected project manifests, then exchanges the candidate and visible
root in one Linux rename operation.  A failed build or validation leaves the
previous visible tree untouched.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence


_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}


class PublicationBusyError(RuntimeError):
    """Another Chisel writer already owns this output root."""


class PublicationValidationError(RuntimeError):
    """A candidate contains unsafe or inconsistent generated artifacts."""


def _thread_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


def _seed_candidate(source: Path, target: Path) -> None:
    """Copy root files and hard-link immutable existing project trees.

    Project builders replace selected project directories wholesale. Keeping
    unselected trees as hard links avoids copying large bundles and archives.
    Root-level index files are copied because callers commonly rewrite them.
    """
    target.mkdir()
    for child in source.iterdir():
        destination = target / child.name
        if child.is_symlink():
            raise PublicationValidationError(f"symlink in existing output: {child}")
        if child.is_dir():
            if child.name in {"growth", "logs"}:
                from .chisel_cache import copy_file

                shutil.copytree(child, destination, copy_function=copy_file)
            else:
                shutil.copytree(child, destination, copy_function=os.link)
        elif child.is_file():
            if child.name.endswith((".tar.gz", ".bundle")):
                os.link(child, destination)
            else:
                shutil.copy2(child, destination)


@contextlib.contextmanager
def staged_publication(output_root: Path) -> Iterator[Path]:
    """Acquire an exclusive writer lock and yield a seeded sibling candidate.

    The candidate is removed on exit unless publication moved it into place.
    The lock covers build, validation, and publication, rejecting overlapping
    writers in both threads and processes.
    """
    output_root = output_root.absolute()
    output_root.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output_root.parent / f".{output_root.name}.chisel.lock"
    local = _thread_lock(lock_path)
    if not local.acquire(blocking=False):
        raise PublicationBusyError(f"Chisel output is already being built: {output_root}")
    fd: int | None = None
    candidate: Path | None = None
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PublicationBusyError(
                f"Chisel output is already being built: {output_root}"
            ) from exc
        candidate = Path(tempfile.mkdtemp(
            prefix=f".{output_root.name}.candidate-", dir=output_root.parent
        ))
        candidate.rmdir()
        if output_root.exists():
            if not output_root.is_dir() or output_root.is_symlink():
                raise PublicationValidationError(f"output root is not a directory: {output_root}")
            _validate_tree(output_root)
            _seed_candidate(output_root, candidate)
        else:
            candidate.mkdir()
        yield candidate
    finally:
        if candidate is not None and candidate.exists():
            shutil.rmtree(candidate)
        if fd is not None:
            os.close(fd)
        local.release()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise PublicationValidationError(f"symlink in candidate: {path}")
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise PublicationValidationError(f"candidate path escapes root: {path}") from exc


def _validate_project(root: Path, name: str) -> None:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise PublicationValidationError(f"invalid project name: {name!r}")
    project_dir = root / name
    if not project_dir.is_dir() or project_dir.is_symlink():
        raise PublicationValidationError(f"candidate project directory missing: {name}")
    manifest_path = project_dir / f"{name}-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublicationValidationError(f"invalid project manifest for {name}: {exc}") from exc
    if manifest.get("project") != name or not isinstance(manifest.get("artifacts"), list):
        raise PublicationValidationError(f"manifest identity/artifacts invalid for {name}")
    seen: set[str] = set()
    declared: set[Path] = set()
    for row in manifest["artifacts"]:
        rel = row.get("name")
        if not isinstance(rel, str) or "\\" in rel:
            raise PublicationValidationError(f"invalid or duplicate artifact path in {name}: {rel!r}")
        relative = Path(rel)
        if (relative.is_absolute() or not relative.parts
                or any(part in {"", ".", ".."} for part in relative.parts)
                or rel in seen):
            raise PublicationValidationError(f"invalid or duplicate artifact path in {name}: {rel!r}")
        seen.add(rel)
        artifact = project_dir / relative
        try:
            artifact.resolve().relative_to(project_dir.resolve())
        except ValueError as exc:
            raise PublicationValidationError(f"artifact escapes project: {name}/{rel}") from exc
        declared.add(relative)
        if rel == manifest_path.name:
            if int(row.get("bytes", -1)) != artifact.stat().st_size:
                raise PublicationValidationError(f"manifest self-size mismatch: {name}/{rel}")
            continue
        if not artifact.is_file() or artifact.is_symlink():
            raise PublicationValidationError(f"manifest artifact missing/unsafe: {name}/{rel}")
        if int(row.get("bytes", -1)) != artifact.stat().st_size:
            raise PublicationValidationError(f"manifest size mismatch: {name}/{rel}")
        expected = row.get("sha256")
        if not isinstance(expected, str) or not expected:
            raise PublicationValidationError(f"manifest hash missing: {name}/{rel}")
        if _sha256(artifact) != expected:
            raise PublicationValidationError(f"manifest hash mismatch: {name}/{rel}")
    actual = {path.relative_to(project_dir) for path in project_dir.rglob("*")
              if path.is_file()}
    if manifest_path.name not in seen:
        raise PublicationValidationError(f"manifest does not list itself: {name}")
    if actual != declared:
        missing = sorted(p.as_posix() for p in actual - declared)
        absent = sorted(p.as_posix() for p in declared - actual)
        raise PublicationValidationError(
            f"manifest file inventory mismatch for {name}: undeclared={missing}, missing={absent}"
        )


def _archive_previous(old_root: Path, candidate_root: Path, names: Sequence[str]) -> None:
    old = [old_root / f"{name}-all.tar.gz" for name in names]
    old = [path for path in old if path.is_file()]
    if not old:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S.%fZ")
    archive_dir = candidate_root / "archive" / stamp
    archive_dir.mkdir(parents=True, exist_ok=True)
    for path in old:
        os.link(path, archive_dir / path.name)


def _exchange_directories(left: Path, right: Path) -> None:
    """Atomically exchange sibling directories with Linux renameat2."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable; refusing non-atomic publication")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                          ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(left), -100, os.fsencode(right), 2)  # RENAME_EXCHANGE
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), f"{left} <-> {right}")


def publish_candidate(candidate_root: Path, output_root: Path, names: Sequence[str]) -> None:
    """Validate selected projects and atomically publish the complete tree.

    Existing unselected project artifacts are retained from the seeded
    candidate. Old combined archives are hard-linked into its archive directory
    immediately before the atomic exchange.
    """
    candidate_root = candidate_root.absolute()
    output_root = output_root.absolute()
    if candidate_root.parent != output_root.parent:
        raise PublicationValidationError("candidate and output root must be siblings")
    if not candidate_root.is_dir() or candidate_root.is_symlink():
        raise PublicationValidationError("candidate root is not a directory")
    _validate_tree(candidate_root)
    for name in names:
        _validate_project(candidate_root, name)
    if output_root.exists():
        _archive_previous(output_root, candidate_root, names)
    if not output_root.exists():
        os.replace(candidate_root, output_root)
        return
    _exchange_directories(candidate_root, output_root)
