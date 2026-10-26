"""Private immutable reads for one materialization and its promotion."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from typing import Iterator

from .io import clone_file


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
    # Growth is allowed; the guarantee is that the copied prefix bytes are
    # still the file's prefix, on the same inode.
    def compatible(current: tuple[int, ...] | None) -> bool:
        return current is not None and current[:2] == before[:2] and current[2] >= before[2]

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
    """Files copied for one operation, plus pinned collections.

    ``capture`` copies explicit files now. ``pin_collection`` records a
    collection's membership and file versions without reading payloads; a
    member is copied the first time a reader opens it, so an operation copies
    only the members its window touches. Append-only members (files a producer
    is still writing) are copied up to their pinned length and may keep
    growing; every other member must be unchanged when it is copied.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.observed_at = datetime.now(UTC)
        self.files: dict[Path, PinnedFile] = {}
        self.collections: dict[Path, tuple[Path, ...]] = {}
        self.member_versions: dict[Path, tuple[int, ...]] = {}
        self.append_members: set[Path] = set()
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
                pinned = self.member_versions.get(original)
                if pinned is not None:
                    grown = (before is not None and before[:2] == pinned[:2] and before[2] >= pinned[2])
                    if not (grown if original in append_paths else before == pinned):
                        raise RuntimeError(f"materialization collection member changed before capture: {original}")
                    before = pinned
                if before is not None:
                    if original in append_paths:
                        _copy_prefix(original, target, before[2])
                    else:
                        # Never borrow an inode that a producer can append to.
                        clone_file(original, target)
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

    def pin_collection(self, root: Path, paths: tuple[Path, ...], *, append_only: tuple[Path, ...] = ()) -> tuple[Path, ...]:
        """Freeze membership and versions; members are copied on first read."""
        with self.lock:
            if root not in self.collections:
                versions = {path: _version(path) for path in paths}
                if any(value is None for value in versions.values()):
                    raise RuntimeError(f"materialization collection changed during discovery: {root}")
                self.member_versions.update({path: value for path, value in versions.items() if value is not None})
                self.append_members.update(path for path in append_only if path in versions)
                self.collections[root] = paths
            return self.collections[root]

    def _is_member(self, original: Path) -> bool:
        return original.parent in self.collections and original in self.collections[original.parent]

    def lookup(self, original: Path) -> PinnedFile | None:
        with self.lock:
            item = self.files.get(original)
            if item is None and original.parent in self.collections:
                if self._is_member(original):
                    self.capture((original,), append_only=(original,) if original in self.append_members else ())
                    item = self.files[original]
                else:
                    item = PinnedFile(self.root / "absent-collection-input", None, None)
            if item is not None and _version(item.path) != item.copy_version:
                raise RuntimeError(f"materialization input snapshot changed: {original}")
            return item

    def version(self, original: Path) -> tuple[bool, tuple[int, ...] | None]:
        """Pinned identity without copying an unread collection member."""
        with self.lock:
            if original in self.files:
                item = self.files[original]
                if _version(item.path) != item.copy_version:
                    raise RuntimeError(f"materialization input snapshot changed: {original}")
                return True, item.source_version
            if self._is_member(original):
                return True, self.member_versions[original]
            if original.parent in self.collections:
                return True, None
            return False, None


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
    if snapshot is None:
        return False, None
    pinned, version = snapshot.version(path)
    return pinned, list(version) if version is not None else None


def pinned_collection(root: Path) -> tuple[Path, ...] | None:
    snapshot = _ACTIVE.get()
    if snapshot is None:
        return None
    with snapshot.lock:
        return snapshot.collections.get(root)


def snapshot_today() -> date | None:
    snapshot = _ACTIVE.get()
    return snapshot.observed_at.date() if snapshot is not None else None
