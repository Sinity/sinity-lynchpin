"""Shared locks for substrate publication."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
from typing import Iterator


def publication_lock_path(canonical: Path | str) -> Path:
    """Return the shared publication lock for one canonical substrate path."""
    canonical_path = Path(canonical).expanduser().resolve(strict=False)
    canonical_identity = str(canonical_path)
    identity_hash = hashlib.sha256(os.fsencode(canonical_identity)).hexdigest()
    # The database lives in local_root/duck. Keep the lock in local_root so
    # read-only duck directories can still be observed without environment-
    # dependent runtime paths.
    lock_root = canonical_path.parent.parent / ".substrate-locks"
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
