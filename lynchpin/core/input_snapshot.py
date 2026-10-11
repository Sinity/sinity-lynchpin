"""Private immutable reads for one materialization and its promotion."""

from __future__ import annotations

import fcntl
import os
import shutil
import stat
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from typing import Iterator


@dataclass(frozen=True)
class PinnedFile:
    path: Path
    source_version: tuple[int, ...] | None
    copy_version: tuple[int, ...] | None


def _version(path: Path) -> tuple[int, ...] | None:
    try:
        value = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(value.st_mode):
        raise ValueError(f"materialization input is not a regular file: {path}")
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _copy_file(source: Path, target: Path) -> None:
    # Clone extents on supporting filesystems; retain a bounded-memory copy
    # route elsewhere. Never borrow an inode that a producer can append to.
    try:
        with source.open("rb") as incoming, target.open("wb") as outgoing:
            fcntl.ioctl(outgoing.fileno(), 0x40049409, incoming.fileno())  # FICLONE
    except OSError:
        shutil.copyfile(source, target)


def _copy_prefix(source: Path, target: Path, length: int) -> None:
    # An open capture has an append-only byte boundary, not a stable EOF.
    # Avoid cloning its growing inode or following new bytes indefinitely.
    with source.open("rb") as incoming, target.open("wb") as outgoing:
        remaining = length
        while remaining:
            block = incoming.read(min(remaining, 1024 * 1024))
            if not block:
                raise RuntimeError(f"materialization input truncated during capture: {source}")
            outgoing.write(block)
            remaining -= len(block)


def _validate_prefix(source: Path, target: Path, before: tuple[int, ...]) -> None:
    def compatible(current: tuple[int, ...] | None) -> bool:
        return (current is not None and current[:2] == before[:2]
                and current[2] >= before[2]
                and (current[2] > before[2] or current == before))

    if not compatible(_version(source)):
        raise RuntimeError(f"materialization input changed during capture: {source}")
    with source.open("rb") as incoming, target.open("rb") as captured:
        remaining = before[2]
        while remaining:
            length = min(remaining, 1024 * 1024)
            block = incoming.read(length)
            if len(block) != length or block != captured.read(length):
                raise RuntimeError(f"materialization input prefix changed during capture: {source}")
            remaining -= length
    if not compatible(_version(source)):
        raise RuntimeError(f"materialization input changed during capture: {source}")


class InputSnapshot:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.observed_at = datetime.now(UTC)
        self.files: dict[Path, PinnedFile] = {}
        self.collections: dict[Path, tuple[Path, ...]] = {}
        self.lock = RLock()

    def capture(self, paths: tuple[Path, ...], *, append_only: tuple[Path, ...] = ()) -> None:
        # Commit the entire captured set together. A failed copy cannot leave
        # readers with a mixture of frozen and live inputs.
        with self.lock:
            pending: dict[Path, PinnedFile] = {}
            append_paths = set(append_only)
            for original in dict.fromkeys(paths):
                if original in self.files:
                    continue
                target = self.root / str(len(self.files) + len(pending))
                before = _version(original)
                if before is not None:
                    if original in append_paths:
                        _copy_prefix(original, target, before[2])
                    else:
                        _copy_file(original, target)
                    target.chmod(0o600)
                    os.utime(target, ns=(before[3], before[3]))
                    if original not in append_paths and _version(original) != before:
                        raise RuntimeError(f"materialization input changed during capture: {original}")
                elif original.exists():
                    raise RuntimeError(f"materialization input appeared during capture: {original}")
                pending[original] = PinnedFile(target, before, _version(target))
            for original, item in pending.items():
                if original in append_paths and item.source_version is not None:
                    _validate_prefix(original, item.path, item.source_version)
                elif _version(original) != item.source_version:
                    raise RuntimeError(f"materialization input changed during capture: {original}")
            self.files.update(pending)

    def capture_collection(self, root: Path, paths: tuple[Path, ...], *, append_only: tuple[Path, ...] = ()) -> None:
        with self.lock:
            if root not in self.collections:
                self.capture(paths, append_only=append_only)
                self.collections[root] = paths

    def lookup(self, original: Path) -> PinnedFile | None:
        with self.lock:
            item = self.files.get(original)
            if item is None and original.parent in self.collections:
                item = PinnedFile(self.root / "absent-collection-input", None, None)
            if item is not None and _version(item.path) != item.copy_version:
                raise RuntimeError(f"materialization input snapshot changed: {original}")
            return item


_ACTIVE: ContextVar[InputSnapshot | None] = ContextVar("materialization_inputs", default=None)


def current_input_snapshot() -> InputSnapshot | None:
    """Identify the current operation for source observations scoped to it."""
    return _ACTIVE.get()


@contextmanager
def input_snapshot() -> Iterator[InputSnapshot]:
    """Reuse a parent's snapshot through worker contexts and promotion."""
    existing = _ACTIVE.get()
    if existing is not None:
        yield existing
        return
    with TemporaryDirectory(prefix="lynchpin-inputs-") as directory:
        snapshot = InputSnapshot(Path(directory))
        token = _ACTIVE.set(snapshot)
        try:
            yield snapshot
        finally:
            _ACTIVE.reset(token)


def read_input_path(path: Path) -> Path:
    snapshot = _ACTIVE.get()
    item = snapshot.lookup(path) if snapshot is not None else None
    return item.path if item is not None else path


def pinned_version(path: Path) -> tuple[bool, list[int] | None]:
    """Return original evidence identity, never the temporary copy's inode."""
    snapshot = _ACTIVE.get()
    item = snapshot.lookup(path) if snapshot is not None else None
    if item is None:
        return False, None
    return True, list(item.source_version) if item.source_version is not None else None


def pinned_collection(root: Path) -> tuple[Path, ...] | None:
    snapshot = _ACTIVE.get()
    if snapshot is None:
        return None
    with snapshot.lock:
        return snapshot.collections.get(root)


def snapshot_today() -> date | None:
    snapshot = _ACTIVE.get()
    return snapshot.observed_at.date() if snapshot is not None else None
