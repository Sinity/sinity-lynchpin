"""Shared, runtime-scoped locks for substrate publication."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Iterator


def _runtime_lock_root() -> Path:
    """Return the writable root shared by substrate readers and publishers."""
    configured = os.environ.get("LYNCHPIN_SUBSTRATE_LOCK_ROOT")
    if configured:
        return Path(configured).expanduser()
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "lynchpin" / "substrate-locks"
    return Path(tempfile.gettempdir()) / f"lynchpin-{os.getuid()}" / "substrate-locks"


def publication_lock_path(canonical: Path | str) -> Path:
    """Return the shared publication lock for one canonical substrate path."""
    canonical_identity = str(Path(canonical).expanduser().resolve(strict=False))
    identity_hash = hashlib.sha256(os.fsencode(canonical_identity)).hexdigest()
    lock_root = _runtime_lock_root()
    lock_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    return lock_root / f"{identity_hash}.publication.lock"


@contextmanager
def publication_lock(canonical: Path | str, *, exclusive: bool) -> Iterator[None]:
    """Hold the canonical substrate's shared or exclusive publication lock."""
    fd = os.open(publication_lock_path(canonical), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
