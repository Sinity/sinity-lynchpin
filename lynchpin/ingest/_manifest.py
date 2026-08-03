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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _tmp_sibling(path: Path) -> Path:
    return path.with_name(f".{path.name}.tmp")


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp-file-then-rename swap."""
    tmp_path = _tmp_sibling(path)
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def atomic_write_ndjson(path: Path, rows: Iterable[Any], *, dumps: Any = None) -> None:
    """Write ``rows`` as newline-delimited JSON to ``path`` atomically.

    Each row is serialized with ``dumps`` (default: ``json.dumps`` with
    ``ensure_ascii=False, sort_keys=True``, matching every materializer's
    existing serialization convention) and written to a sibling ``.tmp``
    file, which is then renamed into place -- so a reader (or a crashed
    writer) never observes a truncated file.
    """
    serialize = dumps or (lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True))
    tmp_path = _tmp_sibling(path)
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(serialize(row))
            handle.write("\n")
    tmp_path.replace(path)


def write_manifest(path: Path, fields: dict[str, Any]) -> None:
    """Write a materializer manifest JSON file.

    Adds ``materialized_at`` (ISO timestamp) if not already present.
    Sorts keys for stable diffs. Written atomically (see module docstring).
    """
    if "materialized_at" not in fields:
        fields = {**fields, "materialized_at": datetime.now(timezone.utc).astimezone().isoformat()}
    atomic_write_text(path, json.dumps(fields, indent=2, sort_keys=True) + "\n")
