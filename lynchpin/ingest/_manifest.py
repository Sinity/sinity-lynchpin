"""Shared atomic-write helpers for ingest materializers.

Every materializer here rewrites its full canonical NDJSON (and manifest)
on each run -- incremental runs read the existing file, merge in the
window they just computed, and rewrite the whole thing. A direct
``path.open("w")``/``path.write_text()`` truncates the file before the new
content is fully written, so a crash mid-write, an OOM kill, or two
overlapping runs of the same materializer can leave a torn/partial file on
disk (confirmed: a single truncated line in atuin's history.ndjson,
lynchpin-mxo). Writing to a sibling temp file and renaming into place is
atomic on the same filesystem (POSIX ``rename(2)``): a reader either sees
the old complete file or the new complete file, never a partial one.
"""

from __future__ import annotations

import json
import os
import stat
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..core.errors import MaterializationError

_SHRINK_GUARD_MIN_ROWS = 1000
_SHRINK_GUARD_THRESHOLD = 0.5


def _open_temp(path: Path) -> tuple[int, Path]:
    """Create a uniquely named temp file beside *path* with normal umask."""
    while True:
        tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            fd = os.open(
                tmp_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o666,
            )
        except FileExistsError:
            continue
        return fd, tmp_path


def _preserve_existing_mode(path: Path, tmp_path: Path) -> None:
    """Keep the destination's mode when an existing file is replaced."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return
    tmp_path.chmod(mode)


def _fsync_parent_directory(path: Path) -> None:
    """Make a successful replacement durable in the containing directory."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(path, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _atomic_write(path: Path, write: Callable[[Any], None]) -> None:
    """Write a temp file, then publish it with durable same-filesystem replace."""
    fd, tmp_path = _open_temp(path)
    owned_fd: int | None = fd
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            owned_fd = None
            write(handle)
            _preserve_existing_mode(path, tmp_path)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_parent_directory(path.parent)
    except BaseException:
        if owned_fd is not None:
            os.close(owned_fd)
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def guard_incremental_shrinkage(
    manifest: Path,
    new_row_count: int,
    *,
    dataset: str,
) -> None:
    """Refuse a suspiciously large row-count drop in an incremental run.

    Incremental (windowed) materialization keeps every existing row outside
    the window and replaces only the window itself, so total row count
    should never collapse. When it does, the most likely cause is that the
    "existing rows" read saw a torn/partial file (confirmed incident,
    lynchpin-9tw: concurrent pre-atomic-write rewriters shrank the
    canonical AW events file from 2.47M to 206K rows because each writer
    trusted whatever fragment it managed to read as the complete existing
    row set). Atomic writes (this module) sever the torn-read vector, but
    this guard also stops any *other* cause of silent mass row loss --
    an accidentally-truncated input, a bad merge -- from being persisted
    over the canonical file.

    Only meaningful for windowed runs; callers on a full-rebuild path
    should not call it. Override deliberately with
    ``LYNCHPIN_ALLOW_SHRINK=1`` when a large shrink is intended (e.g.
    a purge of known-bad rows).
    """
    if os.environ.get("LYNCHPIN_ALLOW_SHRINK") == "1":
        return
    payload = _read_manifest_dict(manifest)
    previous_raw = payload.get("row_count")
    if not isinstance(previous_raw, int) or previous_raw < _SHRINK_GUARD_MIN_ROWS:
        return
    if new_row_count >= previous_raw * _SHRINK_GUARD_THRESHOLD:
        return
    raise MaterializationError(
        dataset,
        reason=(
            f"incremental materialization would shrink {dataset} from "
            f"{previous_raw} to {new_row_count} rows (>50% loss); an "
            "incremental run keeps all rows outside its window, so this "
            "usually means the existing-rows read was torn or truncated. "
            "Refusing to persist. Set LYNCHPIN_ALLOW_SHRINK=1 to override "
            "deliberately."
        ),
    )


def _read_manifest_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp-file-then-rename swap."""
    _atomic_write(path, lambda handle: handle.write(text))


def atomic_write_ndjson(path: Path, rows: Iterable[Any], *, dumps: Any = None) -> None:
    """Write ``rows`` as newline-delimited JSON to ``path`` atomically.

    Each row is serialized with ``dumps`` (default: ``json.dumps`` with
    ``ensure_ascii=False, sort_keys=True``, matching every materializer's
    existing serialization convention) and written to a sibling ``.tmp``
    file, which is then renamed into place -- so a reader (or a crashed
    writer) never observes a truncated file.
    """
    serialize = dumps or (lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True))

    def write_rows(handle: Any) -> None:
        for row in rows:
            handle.write(serialize(row))
            handle.write("\n")

    _atomic_write(path, write_rows)


def write_manifest(path: Path, fields: dict[str, Any]) -> None:
    """Write a materializer manifest JSON file.

    Adds ``materialized_at`` (ISO timestamp) if not already present.
    Sorts keys for stable diffs. Written atomically (see module docstring).
    """
    if "materialized_at" not in fields:
        fields = {**fields, "materialized_at": datetime.now(timezone.utc).astimezone().isoformat()}
    atomic_write_text(path, json.dumps(fields, indent=2, sort_keys=True) + "\n")
